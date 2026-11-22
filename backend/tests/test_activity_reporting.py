"""Completed work categories, bounded SQL totals and role-safe drilldowns."""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.activity_reporting import ActivityReport, TYPE_ORDER
from pak_maintenance.common import ApiError, User, dumps, now
from pak_maintenance.database import Connection, Database, DatabaseConfig
from pak_maintenance.reporting_service import ReportingService
from pak_maintenance.work_service import WorkService

ROOT = Path(__file__).absolute().parents[1]


class ActivityReportingTests(unittest.TestCase):
    def setUp(self):
        root = ROOT/'.build'
        root.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='activity-report-',dir=root)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'synthetic.db'
        with sqlite3.connect(self.path) as raw:
            for script in (ROOT/'schema/sqlite-v12.sql',*sorted((ROOT/'schema').glob('sqlite-*-guards.sql')),ROOT/'schema/sqlite-pm-routing-v11.sql'):
                raw.executescript(script.read_text(encoding='utf-8-sig'))
        raw.close()
        self.db = Database(DatabaseConfig(provider='sqlite',database=str(self.path),development=True))
        self.users = {user.id:user for user in (
            User('head',role='head'),User('planner',role='planner'),User('manager',role='manager'),
            User('admin',role='admin'),User('sm',role='supervisor',specialty='مکانیک'),
            User('se',role='supervisor',specialty='برق'),User('m1',role='technician',specialty='مکانیک'),
            User('m2',role='technician',specialty='مکانیک'),User('e1',role='technician',specialty='برق'))}
        with self.db.transaction() as c:
            for user in self.users.values():
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:id,:role,:spec,1,'',0)",
                          dict(id=user.id,role=user.role,spec=user.specialty))
        self.work = WorkService(self.db)
        self.report = ActivityReport(self.db,self.work)

    def seed(self, *, hall='سالن یک',line='خط یک',kind='EM',work_minutes=60,
             work_date='2026-10-10',status='completed',people=('m1','m2'),
             stopped=False,minutes=25,missing=False,raw=None):
        identifier = uuid4().hex
        asset = 'asset-'+identifier
        body = dict(type=kind,title='فعالیت آزمایشی',description='بازدید و تعویض قطعه',
            workDate=work_date,startTime='08:00',endTime='09:00',productionStopped=stopped,
            downtimeMinutes=minutes,participants=[],parts=[])
        if missing:body.pop('downtimeMinutes')
        with self.db.transaction() as c:
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES(:id,:id,'تجهیز آزمایشی',:hall,:line,'{}')",dict(id=asset,hall=hall,line=line))
            c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,
assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
VALUES(:id,:id,1,1,:kind,'draft',:asset,:creator,:creator,:day,NULL,:body,'فعالیت آزمایشی','بازدید و تعویض قطعه',:minutes,:stamp,:stamp)""",
                dict(id=identifier,kind=kind,asset=asset,creator=people[0],day=work_date,
                     body=dumps(body) if raw is None else raw,minutes=work_minutes,stamp=now()))
            for index,person in enumerate(people):
                c.execute('INSERT INTO participants(order_id,person_id,minutes,specialty) VALUES(:id,:person,:minutes,:spec)',
                    dict(id=identifier,person=person,minutes=30+index*30,spec=self.users[person].specialty))
            c.execute('UPDATE orders SET status=:status WHERE id=:id',dict(status=status,id=identifier))
        return identifier

    def summary(self,viewer='head',**changes):
        args=dict(start='2026-10-01',end='2026-10-31')
        args.update(changes)
        return self.report.summary(self.users[viewer],**args)

    def orders(self,viewer='head',**changes):
        args=dict(start='2026-10-01',end='2026-10-31',hall='سالن یک')
        args.update(changes)
        return self.report.orders(self.users[viewer],**args)

    def fingerprint(self):
        if self.db.is_sqlite:return sha256(self.path.read_bytes()).hexdigest()
        with self.db.transaction(write=False) as c:
            data={table:sorted(c.query('SELECT * FROM '+table),key=dumps) for table in
                ('orders','participants','order_parts','audit','completed_scores','schema_version')}
        return sha256(dumps(data).encode()).hexdigest()

    def test_all_categories_have_distinct_filter_and_one_aggregate_without_join_multiplication(self):
        identifiers={kind:self.seed(kind=kind,stopped=kind in {'EM','CM'},minutes=15 if kind=='EM' else 45) for kind in TYPE_ORDER}
        total=self.summary()
        self.assertEqual(total['metric'],'completed-work-activities-v1')
        self.assertEqual((total['type'],total['groupBy'],total['selectedMetric']),('ALL','hall','activityCount'))
        self.assertEqual(total['typeCounts'],{kind:1 for kind in TYPE_ORDER})
        self.assertEqual(total['totals'],dict(activityCount=5,workMinutes=300,personMinutes=450,
            downtimeMinutes=60,unknownDowntimeCount=0,stoppedActivityCount=2))
        self.assertEqual((total['totalGroups'],total['totalOrders']),(1,5))
        self.assertEqual(self.orders()['totals'],total['totals'])
        for kind,identifier in identifiers.items():
            with self.subTest(kind=kind):
                result=self.summary(kind=kind)
                self.assertEqual(result['totalOrders'],1)
                self.assertEqual(result['typeCounts'][kind],1)
                self.assertEqual([row['id'] for row in self.orders(kind=kind)['orders']],[identifier])

    def test_only_completed_reports_with_inclusive_dates_are_counted(self):
        wanted={self.seed(work_date='2026-10-01'),self.seed(work_date='2026-10-31',kind='GENERAL')}
        for changes in (dict(work_date='2026-09-30'),dict(work_date='2026-11-01'),
                        dict(status='head'),dict(status='supervisor'),dict(status='draft'),dict(status='cancelled')):
            self.seed(**changes)
        self.assertEqual(self.summary()['totalOrders'],2)
        self.assertEqual({row['id'] for row in self.orders()['orders']},wanted)

    def test_hall_and_line_grouping_keep_exact_keys_and_unclassified_values(self):
        quoted=self.seed(hall="سالن '&= یک",line='خط | یک')
        self.seed(hall='سالن دو',line='خط | یک')
        empty=self.seed(line='');literal=self.seed(line='خط تعیین نشده')
        self.assertEqual(self.summary()['totalGroups'],3)
        self.assertEqual(self.summary(group_by='line')['totalGroups'],4)
        self.assertEqual([r['id'] for r in self.orders(hall="سالن '&= یک",group_by='line',line='خط | یک')['orders']],[quoted])
        self.assertEqual({r['id'] for r in self.orders()['orders']},{empty,literal})
        self.assertEqual([r['id'] for r in self.orders(group_by='line',line='')['orders']],[empty])
        self.assertEqual([r['id'] for r in self.orders(group_by='line',line='خط تعیین نشده')['orders']],[literal])
        self.assertEqual(self.orders(hall="' OR 1=1 --")['total'],0)

    def test_metric_sorting_is_server_side_and_paging_totals_cover_full_selection(self):
        for _ in range(3):self.seed(hall='بخش پرکار',work_minutes=30)
        self.seed(hall='بخش طولانی',work_minutes=500)
        count=self.summary(page_size=1)
        duration=self.summary(metric='workMinutes',page_size=1)
        self.assertEqual(count['items'][0]['hall'],'بخش پرکار')
        self.assertEqual(duration['items'][0]['hall'],'بخش طولانی')
        self.assertEqual(count['totals'],duration['totals'])
        self.assertEqual(count['totalGroups'],2)
        rows=[self.orders(hall='بخش پرکار',page=page,page_size=2) for page in (1,2)]
        self.assertEqual([len(row['orders']) for row in rows],[2,1])
        self.assertEqual({row['totals']['activityCount'] for row in rows},{3})
        groups=[self.summary(page=page,page_size=1)['items'][0]['hall'] for page in (1,2)]
        self.assertEqual(len(set(groups)),2)

    def test_work_time_person_time_and_measured_downtime_remain_distinct(self):
        zero=self.seed(stopped=True,minutes=0)
        missing=self.seed(stopped=True,missing=True)
        for invalid in ('25',True,-1,1441,12.5,10**30):self.seed(stopped=True,minutes=invalid)
        self.seed(stopped=False,minutes=999)
        self.seed(stopped='true',minutes=999)
        for raw in ('{"productionStopped":true,"downtimeMinutes":4,"downtimeMinutes":40}',
                    '{"productionStopped":true,','[]'):
            self.seed(raw=raw)
        result=self.summary()
        self.assertEqual(result['totalOrders'],13)
        self.assertEqual(result['totals']['stoppedActivityCount'],8)
        self.assertEqual(result['totals']['unknownDowntimeCount'],7)
        self.assertEqual(result['totals']['downtimeMinutes'],0)
        self.assertEqual(result['totals']['workMinutes'],13*60)
        self.assertEqual(result['totals']['personMinutes'],13*90)
        values={row['id']:row['reportedDowntimeMinutes'] for row in self.orders()['orders']}
        self.assertEqual(values[zero],0);self.assertIsNone(values[missing])

    def test_scope_applies_to_every_category_totals_groups_and_order_hydration(self):
        mine=self.seed(people=('m1',),kind='GENERAL')
        foreign=self.seed(people=('e1',),kind='CM',hall='سالن خارجی')
        for actor in ('m1','sm'):
            with self.subTest(actor=actor):
                self.assertEqual(self.summary(actor)['totalOrders'],1)
                self.assertEqual(self.summary(actor)['typeCounts']['CM'],0)
                self.assertEqual([row['id'] for row in self.orders(actor)['orders']],[mine])
                self.assertEqual(self.orders(actor,hall='سالن خارجی')['total'],0)
                with self.assertRaises(ApiError):self.work.get(self.users[actor],foreign)
        for actor in ('head','planner','manager'):self.assertEqual(self.summary(actor)['totalOrders'],2)
        for viewer in (self.users['admin'],None,User('head',role='head',active=False)):
            with self.assertRaises(ApiError):self.report.summary(viewer,'2026-10-01','2026-10-31')

    def test_validation_and_read_only_delegation(self):
        self.seed(kind='CM')
        before=self.fingerprint()
        service=ReportingService(self.db,self.work)
        self.assertEqual(service.activities(self.users['head'],'2026-10-01','2026-10-31',kind='CM')['totalOrders'],1)
        self.assertEqual(service.activities_orders(self.users['head'],'2026-10-01','2026-10-31','سالن یک',kind='CM')['total'],1)
        self.assertEqual(before,self.fingerprint())
        for changes in (dict(start=None),dict(start='2026-02-30'),dict(end='2026-09-30'),
            dict(start='2025-01-01',end='2026-01-02'),dict(page=0),dict(page_size=101),dict(page=True),
            dict(kind='em'),dict(kind="EM' OR 1=1--"),dict(group_by='hall;DROP TABLE orders'),
            dict(metric='work_minutes DESC;DROP TABLE users'),dict(kind=[]),dict(metric=1),dict(group_by=True)):
            with self.subTest(changes=changes),self.assertRaises(ApiError):self.summary(**changes)
        for changes in (dict(hall=None),dict(group_by='line'),dict(group_by='line',line=None),
            dict(line='خط یک'),dict(group_by='line',line='x'*301),dict(hall='\x00')):
            with self.subTest(changes=changes),self.assertRaises(ApiError):self.orders(**changes)

    def test_drilldown_keeps_parts_workers_and_does_not_broaden_grades(self):
        with self.db.transaction() as c:
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('real','R','پمپ','سالن یک','خط یک','{}')")
            c.execute("INSERT INTO parts(id,code,name,specification,unit) VALUES('activity-part','ACT-P','قطعه','','عدد')")
        body=dict(type='CM',assetId='real',title='اصلاح پمپ',description='تعویض قطعه و کنترل',
            workDate='2026-10-10',startTime='08:00',endTime='09:00',participants=[dict(personId='m1',minutes=60)],
            parts=[dict(partId='activity-part',quantity=2)],checklist=[],productionStopped=True,downtimeMinutes=20)
        row=self.work.save(self.users['m1'],body)
        row=self.work.submit(self.users['m1'],row['id'],row['version'])
        row=self.work.decision(self.users['sm'],row['id'],dict(version=row['version'],decision='approve',grades=[dict(personId='m1',coefficient=2)]))
        self.work.decision(self.users['head'],row['id'],dict(version=row['version'],decision='approve',participantScores=[dict(personId='m1',coefficient=3)]))
        for actor in ('m1','planner','head'):
            actual=self.orders(actor,kind='CM')['orders'][0]
            self.assertEqual(actual['participants'][0]['minutes'],60)
            self.assertEqual(actual['parts'][0]['quantity'],2)
            self.assertEqual(actual['reportedDowntimeMinutes'],20)
            if actor!='head':
                self.assertEqual(actual['participantHeadScores'],[])
                self.assertIsNone(actual.get('score'))
            else:self.assertEqual(actual['participantHeadScores'][0]['coefficient'],3)

    def test_query_count_does_not_grow_per_drilldown_record(self):
        for _ in range(6):self.seed()
        def count(size):
            calls=[]
            with ExitStack() as stack:
                for name in ('query','one'):
                    original=getattr(Connection,name)
                    def measured(connection,*args,_name=name,_original=original,**kwargs):
                        calls.append(_name)
                        return _original(connection,*args,**kwargs)
                    stack.enter_context(patch.object(Connection,name,measured))
                self.orders(page_size=size)
            return len(calls)
        self.assertEqual(count(1),count(6))


class ActivityReportingPostgresqlTests(ActivityReportingTests):
    password='Synthetic-Activity-Only-123!'

    def setUp(self):
        import test_api_integration as integration
        integration.ApiIntegrationTests.setUpClass.__func__(type(self))
        integration.ApiIntegrationTests.setUp(self)
        self.db,self.work=self.services.db,self.services.work
        self.report=ActivityReport(self.db,self.work)
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES('manager','manager','مدیر آزمون','manager','',1,'',0)")
            self.users={row['id']:User.from_row(row) for row in c.query('SELECT * FROM users')}

    def tearDown(self):
        import test_api_integration as integration
        integration.ApiIntegrationTests.tearDown(self)


if __name__=='__main__':unittest.main()
