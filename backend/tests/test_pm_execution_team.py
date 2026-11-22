"""Explicit PM owner/team routing without rewriting technical or issued identity."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pak_maintenance.common import ApiError, User, dumps, loads
from pak_maintenance.database import Database, DatabaseConfig, SchemaMismatchError
from pak_maintenance.pm_archive import PmArchiveService
from pak_maintenance.pm_service import PmService
from pak_maintenance import pm_form_info as info
from pak_maintenance.pm_routing_migration import upgrade_postgresql_v10
from pak_maintenance.work_service import WorkService
from test_pm_service import INSTANT
from pg_test_support import ROOT, fresh_database, LEGACY_SCRIPTS, qa_settings


def seed_team_entities(test):
    identities = [('head','head',''),('planner','planner',''),
        ('utilities','supervisor','تاسیسات'),('energy','supervisor','انرژی'),
        ('outside','supervisor','برق'),('utility-tech','technician','تاسیسات'),
        ('energy-tech','technician','انرژی'),('outside-tech','technician','برق'),
        ('energy-other','supervisor','انرژی')]
    test.users = {i:User(i,role=r,specialty=s) for i,r,s in identities}
    with test.db.transaction() as c:
        for identifier,role,specialty in identities:
            c.execute('INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) '
                      'VALUES(:id,:id,:id,:role,:specialty,1,\'synthetic-unchanged-password-hash\',0)',
                      dict(id=identifier,role=role,specialty=specialty))
        c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('boiler','UTILITY-01','دیگ بخار','انرژی','تولید بخار','{}')")
        c.execute("INSERT INTO parts(id,code,name,unit,specification) VALUES('oil','001','روغن','لیتر','')")
    test.work = WorkService(test.db)
    test.pm = PmService(test.db,test.work)
    test.work.pm_service = test.pm
    test.archive = PmArchiveService(test.db)


class EnergyWorkflowScenarios:
    def plan(self, owner='utilities'):
        template = self.pm.create_template(self.users['head'],dict(requestKey='energy-routing-plan-request-001',
            title='بازدید دیگ بخار',hall='انرژی',specialty='تاسیسات',period='ماهانه',
            activities=['کنترل نشتی','کنترل شیر اطمینان']))
        plan = self.pm.edit_plan(self.users['head'],template['planId'],dict(version=1,
            reason='انتخاب مسئول واقعی انجام',assetId='boiler',supervisorId=owner,
            startDate='2026-09-23',slots=[dict(month=7,week=1)],active=True,mappingConfirmed=True),INSTANT)
        return template,plan

    def issue(self, owner='utilities'):
        template,plan = self.plan(owner)
        self.assertEqual(self.pm.generate(INSTANT),1)
        with self.db.transaction(write=False) as c:
            identifier = c.scalar('SELECT order_id FROM pm_weekly_occurrences')
        return template,plan,identifier

    def deny(self, status, callback):
        with self.assertRaises(ApiError) as error:
            callback()
        self.assertEqual(error.exception.status,status)

    def submit(self, identifier, supervisor='energy', performer='energy-tech'):
        current = self.work.get(self.users[supervisor],identifier)
        assigned = self.pm.assign(self.users[supervisor],identifier,
            dict(version=current['version'],assigneeId=performer))
        body = deepcopy(assigned)
        body.update(description='بازدید کامل انجام شد',workDate='2026-09-26',startTime='08:00',endTime='09:00',
            participants=[dict(personId=performer,minutes=60)],parts=[dict(partId='oil',quantity='1')],checklist=[],
            pmResults={cell['id']:'سالم' for cell in info.cells(assigned['pmSheet']) if cell.get('editable')})
        saved = self.work.save(self.users[performer],body,identifier)
        return self.work.submit(self.users[performer],identifier,saved['version'])

    def archive_rows(self):
        with self.db.transaction(write=False) as c:
            return c.query('SELECT * FROM pm_submission_archive ORDER BY workflow_revision')

    def test_routing_review_transfers_only_selected_owner_and_converges_without_rewriting_plan(self):
        _, plan, identifier = self.issue()
        head = self.users['head']
        filters = dict(target_id='energy', source_id='utilities', specialty='تأسیسات')
        review = self.pm.routing_review(head, **filters)
        self.assertEqual(review['counts'], {'plans': 1, 'issued': 1, 'history': 0})
        self.assertEqual(review['target']['code'], 'energy')
        self.assertEqual(review['items'][0]['supervisorCode'], 'utilities')
        self.deny(403, lambda: self.pm.routing_review(self.users['utilities'], **filters))
        self.deny(400, lambda: self.pm.routing_review(head, target_id='energy-tech'))
        with self.db.transaction(write=False) as connection:
            before = connection.one('SELECT * FROM pm_weekly_plans WHERE id=:id', {'id': plan['id']})
            issued_before = connection.one(
                'SELECT * FROM pm_weekly_occurrences WHERE order_id=:id', {'id': identifier})
        change = dict(version=review['items'][0]['version'], previousSupervisorId='utilities',
                      supervisorId='energy', reason='اصلاح ارجاع به واحد انرژی')
        self.deny(403, lambda: self.pm.transfer_plan_responsibility(self.users['planner'], plan['id'], change))
        self.deny(409, lambda: self.pm.transfer_plan_responsibility(
            head, plan['id'], {**change, 'previousSupervisorId': 'energy-other'}))
        self.pm.transfer_plan_responsibility(head, plan['id'], change)
        self.deny(409, lambda: self.pm.transfer_plan_responsibility(head, plan['id'], change))
        after = self.pm.routing_review(head, section='issued', **filters)
        self.assertEqual(after['counts'], {'plans': 0, 'issued': 1, 'history': 0})
        self.assertTrue(after['items'][0]['canReviewTransfer'])
        self.pm.transfer_responsibility(head, identifier, dict(
            version=self.work.get(head, identifier)['version'], supervisorId='energy',
            reason='اصلاح برگهٔ باز برای واحد انرژی'))
        self.assertEqual(self.pm.routing_review(head, **filters)['counts'],
                         {'plans': 0, 'issued': 0, 'history': 0})
        with self.db.transaction(write=False) as connection:
            current = connection.one('SELECT * FROM pm_weekly_plans WHERE id=:id', {'id': plan['id']})
            issued_after = connection.one(
                'SELECT * FROM pm_weekly_occurrences WHERE order_id=:id', {'id': identifier})
            revision = connection.one('SELECT body,reason FROM pm_plan_revisions WHERE plan_id=:id '
                                      'AND revision=:revision', dict(id=plan['id'], revision=current['version']))
        self.assertEqual(current, {**before, 'version': before['version'] + 1, 'supervisor_id': 'energy',
                                   'body': dumps({**loads(before['body']), 'version': before['version'] + 1,
                                                  'supervisorId': 'energy'})})
        self.assertEqual(issued_before, issued_after)
        self.assertEqual(revision, dict(body=current['body'], reason=change['reason']))

    def test_new_pm_accepts_a_technical_unit_outside_the_old_defaults(self):
        head = self.users['head']
        template = self.pm.create_template(head, dict(requestKey='new-instrumentation-pm-01',
            title='بازدید حسگرها', hall='انرژی', specialty='ابزار دقیق',
            period='ماهانه', activities=['بازدید حسگر فشار']))
        self.assertEqual(template['specialty'], 'ابزار دقیق')
        plan = self.pm.edit_plan(head, template['planId'], dict(version=1,
            reason='زمان‌بندی واحد ابزار دقیق', assetId='boiler', supervisorId='energy',
            startDate='2026-09-23', slots=[dict(month=7, week=1)], active=True,
            mappingConfirmed=True), INSTANT)
        self.assertEqual(plan['supervisorId'], 'energy')
        self.assertEqual(plan['specialty'], 'ابزار دقیق')
        self.assertEqual(self.pm.generate(INSTANT), 1)

    def test_future_utility_plan_can_be_owned_and_issued_by_energy_supervisor(self):
        template,plan,identifier = self.issue('energy')
        self.assertEqual(self.pm.tasks(self.users['energy'])['total'],1)
        self.assertEqual(self.pm.tasks(self.users['utilities'])['total'],0)
        self.assertEqual(self.pm.template(self.users['energy'],template['id'])['specialty'],'تاسیسات')
        self.assertEqual(self.pm.plan(self.users['energy'],plan['id'])['supervisorId'],'energy')
        self.pm.history(self.users['energy'],plan['id'],template=False)
        self.deny(403,lambda:self.pm.plan(self.users['outside'],plan['id']))
        self.deny(403,lambda:self.work.get(self.users['energy-other'],identifier))
        self.deny(403,lambda:self.pm.assign(self.users['energy-other'],identifier,
            dict(version=1,assigneeId='energy-tech')))
        result = self.submit(identifier)
        self.assertEqual(result['status'],'supervisor')
        self.assertEqual(result['pmSpecialty'],'تاسیسات')
        self.assertEqual(result['participants'][0]['specialty'],'انرژی')

    def test_issued_transfer_uses_new_team_and_preserves_original_identity(self):
        template,plan,identifier = self.issue()
        assigned = self.pm.assign(self.users['utilities'],identifier,dict(version=1,assigneeId='utility-tech'))
        with self.db.transaction(write=False) as c:
            original = c.one('SELECT * FROM pm_weekly_occurrences WHERE order_id=:id',dict(id=identifier))
        moved = self.pm.transfer_responsibility(self.users['head'],identifier,
            dict(version=assigned['version'],supervisorId='energy',reason='اصلاح مسئول انجام به تیم انرژی'))
        self.assertIsNone(moved['assigneeId'])
        self.assertEqual((moved['pmOriginalSupervisorId'],moved['pmEffectiveSupervisorId']),('utilities','energy'))
        self.assertEqual(moved['pmSpecialty'],'تاسیسات')
        self.assertEqual(self.pm.tasks(self.users['energy'])['total'],1)
        self.assertEqual(self.pm.tasks(self.users['utilities'])['total'],0)
        self.deny(403,lambda:self.work.get(self.users['utilities'],identifier))
        self.deny(403,lambda:self.pm.assign(self.users['utilities'],identifier,dict(version=moved['version'],assigneeId='utility-tech')))
        self.deny(400,lambda:self.pm.assign(self.users['energy'],identifier,dict(version=moved['version'],assigneeId='utility-tech')))
        self.deny(409,lambda:self.pm.transfer_responsibility(self.users['head'],identifier,
            dict(version=assigned['version'],supervisorId='outside',reason='درخواست همزمان قدیمی')))
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.one('SELECT * FROM pm_weekly_occurrences WHERE order_id=:id',dict(id=identifier)),original)
            self.assertEqual(c.scalar('SELECT supervisor_id FROM pm_weekly_plans WHERE id=:id',dict(id=plan['id'])),'utilities')
        submitted = self.submit(identifier)
        first = self.archive_rows()[0]
        self.assertEqual((first['specialty'],first['supervisor_id']),('تاسیسات','utilities'))
        frozen = loads(first['body'])
        self.assertEqual(frozen['responsibility']['effectiveSupervisorId'],'energy')
        self.assertEqual(frozen['execution']['participants'][0]['specialty'],'انرژی')
        for actor in ('utilities','outside','energy-other'):
            self.deny(403,lambda actor=actor:self.work.decision(self.users[actor],identifier,
                dict(version=submitted['version'],decision='approve',grades=[dict(personId='energy-tech',coefficient=2)])))
        self.deny(409,lambda:self.pm.transfer_responsibility(self.users['head'],identifier,
            dict(version=submitted['version'],supervisorId='outside',reason='کار انجام شده قابل انتقال نیست')))
        corrected = self.work.review_correct(self.users['energy'],identifier,dict(version=submitted['version'],
            reason='اصلاح زمان واقعی و مقدار مصرف',endTime='10:00',
            participants=[dict(personId='energy-tech',minutes=120)],parts=[dict(partId='oil',quantity='2.5')],
            grades=[dict(personId='energy-tech',coefficient=Decimal('2'))]))
        self.assertEqual(corrected['status'],'head')
        rows = self.archive_rows()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0],first)
        self.assertEqual(loads(rows[1]['body'])['responsibility'],frozen['responsibility'])
        done = self.work.decision(self.users['head'],identifier,dict(version=corrected['version'],
            decision='approve',participantScores=[dict(personId='energy-tech',coefficient=Decimal('3'))]))
        self.assertEqual(done['status'],'completed')
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar('SELECT quantity_units FROM part_consumptions WHERE order_id=:id',dict(id=identifier)),2500000)
        with self.assertRaises(Exception):
            with self.db.transaction() as c:
                c.execute('UPDATE pm_submission_archive SET title=title WHERE id=:id',dict(id=first['id']))
        self.assertEqual(self.archive_rows()[0],first)


class EnergyRoutingOfflineTests(EnergyWorkflowScenarios,unittest.TestCase):
    def setUp(self):
        directory = ROOT/'.build'
        directory.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='energy-offline-',dir=directory)
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)/'test.db'
        with sqlite3.connect(path) as raw:
            for script in (ROOT/'schema/sqlite-v12.sql',*sorted((ROOT/'schema').glob('sqlite-*-guards.sql')),ROOT/'schema/sqlite-pm-routing-v11.sql'):
                raw.executescript(script.read_text(encoding='utf-8-sig'))
        raw.close()
        self.db = Database(DatabaseConfig(provider='sqlite',database=str(path),development=True))
        seed_team_entities(self)


class EnergyRoutingPostgresqlTests(EnergyWorkflowScenarios,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_settings()

    def setUp(self):
        self.config,self.owner = fresh_database(self,prefix='energy')
        self.db = Database(self.config)
        seed_team_entities(self)


class RoutingSchemaUpgradePostgresqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_settings()

    def setUp(self):
        self.config,self.owner = fresh_database(self,prefix='routingup',initialize=False)
        self.runtime = Database(self.config)
        with self.owner.transaction() as c:
            for name in LEGACY_SCRIPTS:
                c.raw.execute((ROOT/'schema'/name).read_text(encoding='utf-8-sig'))
            c.raw.execute((ROOT/'schema/postgresql-oee-v1.sql').read_text(encoding='utf-8-sig'))
            c.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
            c.execute(f'GRANT USAGE ON SCHEMA public TO "{self.config.user}"')
            c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{self.config.user}"')
            c.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{self.config.user}"')

    def snapshot(self):
        with self.owner.transaction(write=False) as c:
            tables = [r['table_name'] for r in c.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name<>'schema_version' ORDER BY table_name")]
            return {n:c.query('SELECT to_jsonb(t)::text AS row FROM '+n+' t ORDER BY to_jsonb(t)::text') for n in tables}

    def test_filled_core10_and_oee_upgrade_preserves_every_row_and_reapplies(self):
        from test_core_schema_upgrade import CoreSchemaUpgradePostgresqlTests
        CoreSchemaUpgradePostgresqlTests.seed_history(SimpleNamespace(runtime=self.runtime))
        before = self.snapshot()
        with self.assertRaises(SchemaMismatchError):self.runtime.verify_schema()
        result = upgrade_postgresql_v10(self.owner,runtime_user=self.config.user)
        self.assertFalse(result['alreadyApplied'])
        self.assertEqual(result['schemaVersion'],11)
        self.assertEqual(self.snapshot(),before)
        with self.runtime.transaction(write=False) as c:
            self.runtime._verify_schema_connection(c, expected_version=11)
        self.assertTrue(upgrade_postgresql_v10(self.owner,runtime_user=self.config.user)['alreadyApplied'])
        self.assertEqual(self.snapshot(),before)
        with self.owner.transaction() as c:
            c.execute("SELECT set_config('pak.runtime_user',:role,true)",dict(role=self.config.user))
            c.raw.execute((ROOT/'schema/postgresql-upgrade-10-to-11.sql').read_text())
        self.assertEqual(self.snapshot(),before)

    def test_existing_core10_archive_can_be_corrected_without_forging_legacy_metadata(self):
        from pak_maintenance import pm_archive
        fixture = SimpleNamespace(db=self.runtime)
        seed_team_entities(fixture)
        # Reuse the same real API scenario, with the original same-team role.
        for method in ('plan','issue','submit'):
            setattr(fixture,method,getattr(EnergyWorkflowScenarios,method).__get__(fixture))
        fixture.assertEqual = self.assertEqual
        _,_,identifier = fixture.issue()
        insert = pm_archive._insert
        def insert_legacy(connection, record):
            body = loads(record['body']);body.pop('responsibility',None);record['body']=dumps(body)
            return insert(connection,record)
        with patch('pak_maintenance.pm_archive._insert',side_effect=insert_legacy):
            submitted = fixture.submit(identifier,'utilities','utility-tech')
        before = self.snapshot()
        upgrade_postgresql_v10(self.owner,runtime_user=self.config.user)
        self.assertEqual(self.snapshot(),before)
        with self.runtime.transaction(write=False) as c:
            original = c.one('SELECT * FROM pm_submission_archive WHERE order_id=:id',dict(id=identifier))
        self.assertNotIn('responsibility',loads(original['body']))
        corrected = fixture.work.review_correct(fixture.users['utilities'],identifier,dict(version=submitted['version'],
            reason='اصلاح مستند قدیمی پس از ارتقا',endTime='10:00',
            participants=[dict(personId='utility-tech',minutes=120)],
            grades=[dict(personId='utility-tech',coefficient=2)]))
        self.assertEqual(corrected['status'],'head')
        with self.runtime.transaction(write=False) as c:
            self.assertEqual(c.one('SELECT * FROM pm_submission_archive WHERE id=:id',dict(id=original['id'])),original)
            self.assertEqual(c.scalar('SELECT COUNT(*) FROM pm_submission_archive WHERE order_id=:id',dict(id=identifier)),2)

    def test_invalid_marker_is_rejected_without_publishing_guards(self):
        with self.owner.transaction() as c:c.execute('UPDATE schema_version SET version=9')
        before = self.snapshot()
        with self.assertRaises(SchemaMismatchError):upgrade_postgresql_v10(self.owner,runtime_user=self.config.user)
        self.assertEqual(self.snapshot(),before)
        with self.owner.transaction(write=False) as c:self.assertEqual(c.scalar('SELECT version FROM schema_version'),9)


if __name__=='__main__':
    unittest.main()
