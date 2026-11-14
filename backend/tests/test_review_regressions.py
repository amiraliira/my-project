"""Review regressions against an explicitly disposable real PostgreSQL DB."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pak_maintenance.common import ApiError, User, dumps, now
from pak_maintenance.exports import ExportStream
from test_api_integration import ApiIntegrationTests


class ReviewRegressions(unittest.TestCase):
    password = ApiIntegrationTests.password
    setUpClass = classmethod(ApiIntegrationTests.setUpClass.__func__)
    setUp = ApiIntegrationTests.setUp
    tearDown = ApiIntegrationTests.tearDown
    request = ApiIntegrationTests.request
    login = ApiIntegrationTests.login
    work_body = staticmethod(ApiIntegrationTests.work_body)
    create_and_submit = ApiIntegrationTests.create_and_submit
    approve = ApiIntegrationTests.approve

    def test_dashboard_grouped_overview_preserves_all_role_scopes_and_cancelled_semantics(self):
        completed = self.create_and_submit()
        for actor in ('sm', 'se', 'head'):
            self.login(actor)
        completed = self.approve('sm', completed, [('m1', 0), ('m2', '.75')])
        completed = self.approve('se', completed, [('e1', '2.5')])
        completed = self.approve('head', completed, coefficient=3)
        # Distinct creators, specialties, states, and dates expose accidental
        # broadening of scope or counting cancelled work as open/overdue.
        extras = [
            ('draft-mech','EM','draft','m1','m1','2020-01-01',15),
            ('cancel-mech','EM','cancelled','m1','m1','2020-01-01',20),
            ('returned-electric','PROJECT','returned','e1','e1','2020-01-01',45),
            ('future-pm','PM','draft','sm','m2','2099-01-01',0),
            ('outside-em','EM','draft','outside','outside',None,35),
            ('unassigned-project','PROJECT','draft','head',None,None,0),
        ]
        with self.owner.transaction() as c:
            for identifier, kind, status, creator, assignee, due, minutes in extras:
                body = dumps(dict(type=kind,description='synthetic scope review',title=identifier,participants=[],parts=[],checklist=[]))
                c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
VALUES(:id,:id,1,1,:type,:status,'asset',:creator,:assignee,'2026-10-10',:due,:body,:id,'synthetic scope review',:minutes,:stamp,:stamp)""",
                    dict(id=identifier,type=kind,status=status,creator=creator,assignee=assignee,due=due,body=body,minutes=minutes,stamp=now()))
        expected_visibility = {
            'head': {item[0] for item in extras}, 'planner': {item[0] for item in extras},
            'sm': {'draft-mech','cancel-mech','future-pm'},
            'se': {'returned-electric','outside-em'},
            'm1': {'draft-mech','cancel-mech'}, 'm2': {'future-pm'},
        }
        for actor, visible in expected_visibility.items():
            with self.subTest(actor=actor):
                if actor not in self.cookies:
                    self.login(actor)
                if actor in ('m1','m2'):
                    # Technician HTTP access intentionally remains confined to
                    # registration/PM. Still exercise the internal read scope.
                    with self.assertRaises(ApiError) as denied:
                        self.request('GET','/api/dashboard',actor=actor)
                    self.assertEqual(denied.exception.status,403)
                    with self.services.db.connect() as c:
                        viewer=User.from_row(c.one('SELECT * FROM users WHERE id=:id',dict(id=actor)))
                    result=self.services.reporting.dashboard(viewer)
                else:
                    result = self.request('GET','/api/dashboard',actor=actor).payload
                selected = [item for item in extras if item[0] in visible]
                types = Counter({'EM': 1}); statuses = Counter({'completed': 1})
                types.update(item[1] for item in selected); statuses.update(item[2] for item in selected)
                self.assertEqual(result['total'], 1 + len(selected))
                self.assertEqual(result['completed'], 1)
                self.assertEqual(result['open'], sum(item[2] not in {'completed','cancelled'} for item in selected))
                self.assertEqual(result['overdue'], sum(item[2] not in {'completed','cancelled'} and item[5]=='2020-01-01' for item in selected))
                self.assertEqual((result['repairMinutes'],result['personMinutes']), (60,180))
                self.assertEqual({row['name']:row['count'] for row in result['byType']}, dict(types))
                self.assertEqual({row['name']:row['count'] for row in result['byStatus']}, dict(statuses))
                self.assertEqual(result['byAsset'][0]['minutes'],60)
                self.assertEqual(result['repair'][0]['minutes'],60+sum(item[6] for item in selected if item[1]=='EM' and item[2]!='cancelled'))
                self.assertEqual(result['score'],Decimal('9.75') if actor=='head' else None)

    def test_paged_inbox_search_is_forwarded_and_stays_in_supervisor_scope(self):
        submitted = self.create_and_submit()
        self.login('sm')
        selected = self.request('GET','/api/inbox',actor='sm',query={'page':'1','pageSize':'25','q':submitted['number']}).payload
        self.assertEqual([row['id'] for row in selected['orders']],[submitted['id']])
        absent = self.request('GET','/api/inbox',actor='sm',query={'page':'1','pageSize':'25','q':'does-not-exist'}).payload
        self.assertEqual((absent['total'],absent['orders']),(0,[]))

    def test_dashboard_legacy_mix_retains_per_order_half_even_rounding(self):
        self.login('head')
        stamp=now()
        with self.owner.transaction() as c:
            c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
VALUES('legacy-mix','SYN-MIX',4,2,'EM','completed','asset','m1','m1','2026-10-10',NULL,'{}','legacy mixed tie','synthetic only',60,:stamp,:stamp)""",dict(stamp=stamp))
            for person in ('m1','m2'):
                c.execute("INSERT INTO participants(order_id,person_id,minutes,specialty) VALUES('legacy-mix',:person,60,'مکانیک')",dict(person=person))
            c.execute("""INSERT INTO approvals(id,order_id,revision,stage,specialty,decision,coefficient,reason,actor_id,created_at)
VALUES('legacy-super','legacy-mix',2,'supervisor','مکانیک','approve',0.005,'synthetic legacy','sm',:stamp),
('legacy-chief','legacy-mix',2,'head','','approve',1,'synthetic legacy','head',:stamp)""",dict(stamp=stamp))
            c.execute("""INSERT INTO participant_scores(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at)
VALUES('legacy-mix',2,'m1','مکانیک',0.01,'sm','legacy-super',:stamp)""",dict(stamp=stamp))
            c.execute("""INSERT INTO completed_scores(order_id,revision,person_id,supervisor_coefficient,head_coefficient,score_raw,score_cents,source)
VALUES('legacy-mix',2,'m1',0.01,1,'0.01',1,'individual'),
('legacy-mix',2,'m2',0.005,1,'0.005',NULL,'legacy-specialty')""")
        result=self.request('GET','/api/dashboard',actor='head').payload
        self.assertEqual(result['score'],Decimal('0.02'))

    def test_prepared_csv_closes_postgresql_snapshot_before_slow_consumer(self):
        def rows():
            with self.services.db.transaction(write=False) as c:
                c.execute("SET LOCAL idle_in_transaction_session_timeout='1s'")
                yield (c.scalar('SELECT 1'),)
                yield (c.scalar('SELECT 2'),)
        stream = ExportStream(['value'], rows()).prepare(self.directory/'backups/.exports')
        try:
            with self.owner.connect() as c:
                open_runtime_connections = c.scalar('SELECT COUNT(*) FROM pg_stat_activity WHERE datname=:database AND usename=:runtime',
                    dict(database=self.services.db.config.database,runtime=self.services.db.config.user))
            self.assertEqual(open_runtime_connections,0)
            # Longer than this snapshot's PostgreSQL idle transaction limit.
            # Download succeeds because only the private prepared file remains.
            time.sleep(1.2)
            self.assertEqual(b''.join(stream), b'\xef\xbb\xbfvalue\r\n1\r\n2\r\n')
        finally:
            stream.close()
        self.assertEqual(list((self.directory/'backups/.exports').iterdir()),[])

    def test_work_csv_respects_search_and_status_without_returning_other_reports(self):
        import csv
        from io import StringIO
        submitted = self.create_and_submit()
        self.login('head')
        result = self.request('GET','/api/reports/works.csv',actor='head',
            query={'q':submitted['number'],'status':'supervisor','type':'EM'})
        try:
            rows = list(csv.DictReader(StringIO(b''.join(result.stream).decode('utf-8-sig'))))
        finally:
            result.stream.close()
        self.assertEqual([row['شماره'] for row in rows],[submitted['number']])
        result = self.request('GET','/api/reports/works.csv',actor='head',query={'q':'no-such-report'})
        try:
            self.assertEqual(list(csv.DictReader(StringIO(b''.join(result.stream).decode('utf-8-sig')))),[])
        finally:
            result.stream.close()

    def test_future_execution_stays_draft_until_it_really_ends_in_tehran(self):
        class Clock(datetime):
            instant=datetime(2026,10,20,5,0,tzinfo=timezone.utc)  # 08:30 Tehran
            @classmethod
            def now(cls,tz=None):
                return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)
        self.login('m1')
        body=dict(self.work_body(),workDate='2026-10-20')
        with patch('pak_maintenance.work_service.datetime',Clock):
            saved=self.request('POST','/api/orders',actor='m1',body=body).payload
            with self.assertRaises(ApiError) as future:
                self.request('POST','/api/orders/'+saved['id']+'/submit',actor='m1',body={'version':saved['version']})
            self.assertEqual(future.exception.status,400)
            with self.services.db.connect() as c:
                self.assertEqual(c.one('SELECT status,version FROM orders WHERE id=:id',{'id':saved['id']}),{'status':'draft','version':saved['version']})
                self.assertEqual(c.scalar('SELECT COUNT(*) FROM approvals'),0)
            Clock.instant=datetime(2026,10,20,6,0,tzinfo=timezone.utc)  # 09:30 Tehran
            submitted=self.request('POST','/api/orders/'+saved['id']+'/submit',actor='m1',body={'version':saved['version']}).payload
            self.assertEqual(submitted['status'],'supervisor')

    def test_overnight_execution_uses_the_following_tehran_date(self):
        class Clock(datetime):
            instant=datetime(2026,10,19,20,45,tzinfo=timezone.utc)  # Oct20 00:15 Tehran
            @classmethod
            def now(cls,tz=None):
                return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)
        self.login('m1')
        body=dict(self.work_body(),workDate='2026-10-19',startTime='23:30',endTime='00:30')
        with patch('pak_maintenance.work_service.datetime',Clock):
            saved=self.request('POST','/api/orders',actor='m1',body=body).payload
            with self.assertRaises(ApiError):
                self.request('POST','/api/orders/'+saved['id']+'/submit',actor='m1',body={'version':saved['version']})
            Clock.instant=datetime(2026,10,19,21,15,tzinfo=timezone.utc)  # 00:45 Tehran
            submitted=self.request('POST','/api/orders/'+saved['id']+'/submit',actor='m1',body={'version':saved['version']}).payload
            self.assertEqual((submitted['status'],submitted['durationMinutes']),('supervisor',60))


del ApiIntegrationTests

if __name__=='__main__':
    unittest.main()
