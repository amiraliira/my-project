"""Recorded downtime, exact line identity and role-safe drilldown on real SQL."""
from hashlib import sha256
from uuid import uuid4
import unittest

from pak_maintenance.common import ApiError, User, dumps, now
from pak_maintenance.em_downtime import EmDowntimeReport
from pak_maintenance.reporting_service import ReportingService
import test_pm_service as fixtures


class EmDowntimeTests(unittest.TestCase):
    tearDown = fixtures.PmTests.tearDown

    def setUp(self):
        fixtures.PmTests.setUp(self)
        self.report = EmDowntimeReport(self.db, self.work)
        self.users['manager'] = User('manager', role='manager')
        self.users['planner'] = User('planner', role='planner')

    def fingerprint(self):
        if self.db.is_sqlite:
            return sha256(self.path.read_bytes()).hexdigest()
        with self.db.transaction(write=False) as c:
            data={table:sorted(c.query('SELECT * FROM '+table),key=dumps) for table in
                  ('orders','participants','order_parts','audit','completed_scores','schema_version')}
        return sha256(dumps(data).encode()).hexdigest()

    def seed(self, *, hall='سالن یک', line='خط یک', minutes=25, stopped=True,
             work_date='2026-10-10', kind='EM', status='completed', people=('m1', 'm2'),
             raw=None, missing=False):
        identifier = uuid4().hex
        asset = 'a-' + identifier
        body = dict(type=kind, title='رفع خرابی', description='بازدید و تعویض قطعه',
                    workDate=work_date, startTime='08:00', endTime='09:00',
                    productionStopped=stopped, downtimeMinutes=minutes, participants=[], parts=[])
        if missing:
            body.pop('downtimeMinutes')
        with self.db.transaction() as c:
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES(:id,:id,'پمپ',:hall,:line,'{}')",
                      dict(id=asset, hall=hall, line=line))
            c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,
created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
VALUES(:id,:id,1,1,:kind,'draft',:asset,:creator,:creator,:day,NULL,:body,'رفع خرابی','بازدید و تعویض قطعه',60,:stamp,:stamp)""",
                      dict(id=identifier, kind=kind, asset=asset, creator=people[0], day=work_date,
                           body=dumps(body) if raw is None else raw, stamp=now()))
            for index, person in enumerate(people):
                c.execute("INSERT INTO participants(order_id,person_id,minutes,specialty) VALUES(:id,:person,:minutes,:spec)",
                          dict(id=identifier, person=person, minutes=30 + index * 30, spec=self.users[person].specialty))
            c.execute("UPDATE orders SET status=:status WHERE id=:id", dict(status=status, id=identifier))
        return identifier

    def summary(self, viewer='head', **changes):
        args=dict(start='2026-10-01', end='2026-10-31')
        args.update(changes)
        return self.report.summary(self.users[viewer], **args)

    def orders(self, viewer='head', **changes):
        args=dict(start='2026-10-01', end='2026-10-31', hall='سالن یک', line='خط یک')
        args.update(changes)
        return self.report.orders(self.users[viewer], **args)

    def test_downtime_does_not_become_repair_or_person_time_and_counts_do_not_multiply(self):
        one=self.seed(minutes=15);two=self.seed(minutes=45)
        report=self.summary()
        self.assertEqual(report['metric'],'reported-em-downtime-v1')
        self.assertEqual((report['totalOrders'],report['totalLines']),(2,1))
        self.assertEqual(report['totals'],dict(activityCount=2,downtimeMinutes=60,repairMinutes=120,personMinutes=180,unknownDowntimeCount=0))
        details=self.orders()
        self.assertEqual(details['totals'],report['totals'])
        self.assertEqual({row['id'] for row in details['orders']},{one,two})
        self.assertEqual({row['reportedDowntimeMinutes'] for row in details['orders']},{15,45})
        self.assertEqual(sum(row['activityCount'] for row in report['items']),2)

    def test_only_completed_em_real_boolean_and_inclusive_work_dates_are_counted(self):
        first=self.seed(work_date='2026-10-01');last=self.seed(work_date='2026-10-31')
        for changes in [dict(work_date='2026-09-30'),dict(work_date='2026-11-01'),dict(kind='PM'),
                        dict(kind='PROJECT'),dict(status='head'),dict(status='cancelled'),
                        dict(status='draft'),dict(stopped=False),dict(stopped='true'),dict(stopped=1)]:
            self.seed(**changes)
        self.assertEqual(self.summary()['totalOrders'],2)
        self.assertEqual({row['id'] for row in self.orders()['orders']},{first,last})

    def test_exact_pair_separates_halls_unclassified_literal_placeholder_and_sql_metacharacters(self):
        wanted=self.seed(hall="سالن '&= یک",line='خط | یک')
        self.seed(hall='سالن دو',line='خط | یک')
        empty=self.seed(line='');literal=self.seed(line='خط تعیین نشده')
        summary=self.summary()
        self.assertEqual(summary['totalLines'],4)
        self.assertEqual([r['id'] for r in self.orders(hall="سالن '&= یک",line='خط | یک')['orders']],[wanted])
        self.assertEqual([r['id'] for r in self.orders(line='')['orders']],[empty])
        self.assertEqual([r['id'] for r in self.orders(line='خط تعیین نشده')['orders']],[literal])
        self.assertEqual(self.orders(line="' OR 1=1 --")['total'],0)

    def test_pages_cover_all_groups_and_all_orders_with_matching_totals(self):
        wanted={self.seed(line='خط '+str(index)) for index in range(13)}
        groups=[row for page in range(1,4) for row in self.summary(page=page,page_size=5)['items']]
        self.assertEqual(len({(row['hall'],row['line']) for row in groups}),13)
        for _ in range(28):wanted.add(self.seed(line='خط 0'))
        pages=[self.orders(line='خط 0',page=page,page_size=10) for page in range(1,4)]
        self.assertEqual([r['total'] for r in pages],[29]*3)
        self.assertEqual([len(r['orders']) for r in pages],[10,10,9])
        self.assertEqual(len({row['id'] for result in pages for row in result['orders']}),29)
        self.assertEqual({r['totals']['downtimeMinutes'] for r in pages},{29*25})

    def test_zero_missing_and_invalid_durations_do_not_fabricate_measured_zero(self):
        zero=self.seed(minutes=0);missing=self.seed(missing=True)
        for invalid in ('25',True,-1,1441,12.5,10**30):self.seed(minutes=invalid)
        self.seed(raw='{"productionStopped":true,"downtimeMinutes":4,"downtimeMinutes":40}')
        self.seed(raw='{"productionStopped":true,')
        self.seed(raw='[]')
        summary=self.summary()
        self.assertEqual((summary['totalOrders'],summary['totals']['unknownDowntimeCount']),(8,7))
        values={row['id']:row['reportedDowntimeMinutes'] for row in self.orders()['orders']}
        self.assertEqual(values[zero],0);self.assertIsNone(values[missing])
        self.assertEqual(sum(value is None for value in values.values()),7)

    def test_scope_applies_to_groups_counts_totals_hydration_and_existing_detail_permissions(self):
        mine=self.seed(people=('m1',));foreign=self.seed(people=('e1',),line='خط خارجی')
        for actor in ('sm','m1'):
            self.assertEqual(self.summary(actor)['totalOrders'],1)
            self.assertEqual([row['id'] for row in self.orders(actor)['orders']],[mine])
            self.assertEqual(self.orders(actor,line='خط خارجی')['total'],0)
            with self.assertRaises(ApiError):self.work.get(self.users[actor],foreign)
        for actor in ('head','planner','manager'):
            self.assertEqual(self.summary(actor)['totalOrders'],2)
        with self.assertRaises(ApiError):self.summary('admin')
        with self.assertRaises(ApiError):self.report.summary(User('x',role='head',active=False),'2026-10-01','2026-10-31')

    def test_validation_and_read_only_storage_and_shared_reporting_delegate(self):
        self.seed()
        before=self.fingerprint()
        report=ReportingService(self.db,self.work)
        self.assertEqual(report.em_downtime(self.users['head'],'2026-10-01','2026-10-31')['totalOrders'],1)
        self.assertEqual(report.em_downtime_orders(self.users['head'],'2026-10-01','2026-10-31','سالن یک','خط یک')['total'],1)
        self.assertEqual(self.fingerprint(),before)
        for changes in [dict(start=None),dict(start='2026-02-30'),dict(end='2026-09-30'),
                        dict(start='2025-01-01',end='2026-01-02'),dict(page=0),dict(page_size=101),dict(page=True)]:
            with self.subTest(changes=changes),self.assertRaises(ApiError):self.summary(**changes)
        for changes in [dict(hall=None),dict(line=None),dict(line='x'*301),dict(hall='\x00')]:
            with self.assertRaises(ApiError):self.orders(**changes)

    def test_api_binds_scoped_registration_routes_and_rejects_missing_group_and_unauthorized_actor(self):
        import test_api as routes
        from pak_maintenance.api import Request
        fixture=routes.RouteTests();fixture.setUp()
        fixture.services.reporting=ReportingService(self.db,self.work)
        self.seed(people=('m1',));self.seed(people=('e1',),line='خط خارجی')
        base={'from':'2026-10-01','to':'2026-10-31','page':'1','pageSize':'10'}
        for actor,total in [('m1',1),('sm',1),('head',2),('planner',2)]:
            fixture.auth.authenticate.return_value=self.users[actor]
            result=fixture.api.dispatch(Request('GET','/api/reports/em-downtime',base)).payload
            self.assertEqual(result['totalOrders'],total)
            result=fixture.api.dispatch(Request('GET','/api/reports/em-downtime/orders',{**base,'hall':'سالن یک','line':'خط خارجی'})).payload
            self.assertEqual(result['total'],0 if actor in ('m1','sm') else 1)
        fixture.auth.authenticate.return_value=self.users['head']
        with self.assertRaises(ApiError):fixture.api.dispatch(Request('GET','/api/reports/em-downtime/orders',base))
        for actor in (self.users['admin'],None):
            fixture.auth.authenticate.return_value=actor
            with self.assertRaises(ApiError):fixture.api.dispatch(Request('GET','/api/reports/em-downtime',base))

    def test_hydration_preserves_operational_parts_and_workers_but_never_broadens_grades(self):
        body=dict(type='EM',assetId='asset' if 'asset' in getattr(self,'asset_ids',()) else 'a1',
                  title='تعمیر آزمایشی',description='تعویض قطعه و کنترل عملکرد',workDate='2026-10-10',
                  startTime='08:00',endTime='09:00',participants=[dict(personId='m1',minutes=60)],
                  parts=[],checklist=[],productionStopped=True,downtimeMinutes=20)
        # Native integration fixtures and offline fixtures name their base asset differently.
        with self.db.connect() as c:
            body['assetId']=c.scalar('SELECT id FROM assets ORDER BY id LIMIT 1')
            part=c.scalar('SELECT id FROM parts ORDER BY id LIMIT 1')
        if part:body['parts']=[dict(partId=part,quantity=2)]
        row=self.work.save(self.users['m1'],body)
        row=self.work.submit(self.users['m1'],row['id'],row['version'])
        row=self.work.decision(self.users['sm'],row['id'],dict(version=row['version'],decision='approve',grades=[dict(personId='m1',coefficient=2)]))
        row=self.work.decision(self.users['head'],row['id'],dict(version=row['version'],decision='approve',participantScores=[dict(personId='m1',coefficient=3)]))
        with self.db.connect() as c:
            asset=c.one('SELECT hall,line FROM assets WHERE id=:id',dict(id=body['assetId']))
        for viewer in ('m1','planner','head'):
            result=self.orders(viewer,hall=asset['hall'],line=asset['line'])
            self.assertEqual(result['total'],1)
            order=result['orders'][0]
            self.assertEqual(order['participants'][0]['minutes'],60)
            self.assertEqual(order['reportedDowntimeMinutes'],20)
            if part:self.assertEqual(order['parts'][0]['quantity'],2)
            if viewer!='head':
                self.assertEqual(order['participantHeadScores'],[])
                self.assertIsNone(order.get('score'))
            else:self.assertEqual(order['participantHeadScores'][0]['coefficient'],3)


class EmDowntimePostgresqlTests(EmDowntimeTests):
    """Exactly the same acceptance against guarded native PostgreSQL storage."""
    password = "Synthetic-EM-Only-123!"
    def setUp(self):
        import test_api_integration as integration
        integration.ApiIntegrationTests.setUpClass.__func__(type(self))
        integration.ApiIntegrationTests.setUp(self)
        self.db=self.services.db;self.work=self.services.work
        self.report=EmDowntimeReport(self.db,self.work)
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES('manager','manager','مدیر آزمون','manager','',1,'',0)")
            self.users={row['id']:User.from_row(row) for row in c.query('SELECT * FROM users')}

    def tearDown(self):
        import test_api_integration as integration
        integration.ApiIntegrationTests.tearDown(self)


if __name__=='__main__':unittest.main()
