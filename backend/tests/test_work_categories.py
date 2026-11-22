"""Exercise the new work categories and preserve records across the owner upgrade."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest

from pak_maintenance.common import ApiError, User
from pak_maintenance.database import Database, DatabaseConfig, SchemaMismatchError
from pak_maintenance.master_data import CatalogService
from pak_maintenance.work_category_contract import verify_work_category_contract
from pak_maintenance.work_category_migration import preflight_postgresql_v11, upgrade_postgresql_v11
from pak_maintenance.work_service import WorkService
from pg_test_support import ROOT, SCRIPTS, fresh_database, qa_settings


TECHNICIAN = User('tech', role='technician', specialty='mechanical', session_epoch=3)
SECOND_TECHNICIAN = User('other-tech', role='technician', specialty='mechanical')


def seed(db):
    with db.transaction() as connection:
        connection.execute("INSERT INTO users VALUES('tech','T-1','Synthetic technician','technician','mechanical',1,'preserve-fixture-hash',3)")
        connection.execute("INSERT INTO users VALUES('other-tech','T-2','Second synthetic technician','technician','mechanical',1,'fixture-hash',0)")
        connection.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('pump','P-1','Synthetic pump','QA hall','QA line','{}')")


def report(kind, *, technician=TECHNICIAN, **fields):
    return dict(type=kind, title='Synthetic activity', description='Synthetic activity details',
                workDate='2026-01-20', startTime='08:00', endTime='09:00',
                participants=[dict(personId=technician.id, minutes=60)], **fields)


class WorkCategoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / '.build'
        directory.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='work-categories-', dir=directory)
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'fixture.db'
        with sqlite3.connect(path) as connection:
            for script in (ROOT/'schema/sqlite-v12.sql', *sorted((ROOT/'schema').glob('sqlite-*-guards.sql')), ROOT/'schema/sqlite-pm-routing-v11.sql'):
                connection.executescript(script.read_text(encoding='utf-8-sig'))
        connection.close()
        self.db = Database(DatabaseConfig(provider='sqlite', database=str(path), development=True))
        self.work = WorkService(self.db)
        seed(self.db)

    def test_general_is_hall_only_and_cm_catalog_and_validation_exclude_logical_targets(self):
        catalog = CatalogService(self.db)
        self.db.verify_schema()
        for invalid in (report('CM', hall='QA hall'), report('GENERAL', hall='unknown'),
                        report('GENERAL', assetId='pump', hall='QA hall')):
            with self.subTest(invalid=invalid['type']), self.assertRaises(ApiError) as error:
                self.work.save(TECHNICIAN, invalid)
            self.assertEqual(error.exception.status, 400)

        general = self.work.save(TECHNICIAN, report('GENERAL', hall='QA hall'), request_key='hall-activity')
        replay = self.work.save(TECHNICIAN, report('GENERAL', hall='QA hall'), request_key='hall-activity')
        self.assertEqual(replay['id'], general['id'])
        logical_id = general['assetId']
        self.assertEqual((general['type'], general['hall'], general['line']), ('GENERAL', 'QA hall', ''))
        self.assertEqual(general['assetName'], 'امور محوله')
        with self.assertRaises(ApiError):
            self.work.save(TECHNICIAN, report('CM', assetId=logical_id))
        with self.assertRaises(ApiError):
            self.work.save(TECHNICIAN, report('GENERAL', assetId=logical_id, hall='another hall'))

        all_assets = catalog.catalog(TECHNICIAN, 'assets')['items']
        self.assertTrue(next(a for a in all_assets if a['id'] == logical_id)['isGeneralWork'])
        cm_assets = catalog.catalog(TECHNICIAN, 'assets', {'workType': 'CM'})['items']
        self.assertEqual([a['id'] for a in cm_assets], ['pump'])
        correction = self.work.save(TECHNICIAN, report('CM', assetId='pump'))
        self.assertEqual(correction['type'], 'CM')
        self.assertEqual(self.work.list_page(TECHNICIAN, type='CM')['total'], 1)
        for draft in (general, correction):
            sent = self.work.submit(TECHNICIAN, draft['id'], draft['version'])
            self.assertEqual(sent['type'], draft['type'])
            self.assertEqual(sent['status'], 'supervisor')

    def test_assigned_work_reuses_a_legacy_named_hall_target(self):
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO assets(id,code,name,hall,line,body) VALUES('legacy-target','OLD-A','امور عمومی','QA hall','','{}')"
            )
        work = self.work.save(TECHNICIAN, report('GENERAL', hall='QA hall'))
        self.assertEqual(work['assetId'], 'legacy-target')
        self.assertEqual(work['assetName'], 'امور عمومی')
        cm_assets = CatalogService(self.db).catalog(TECHNICIAN, 'assets', {'workType': 'CM'})['items']
        self.assertEqual([asset['id'] for asset in cm_assets], ['pump'])

    def test_rejected_general_report_does_not_leave_a_logical_asset(self):
        body = report('GENERAL', hall='QA hall')
        body['title'] = ''
        with self.assertRaises(ApiError):
            self.work.save(TECHNICIAN, body)
        with self.db.transaction(write=False) as connection:
            self.assertEqual(connection.scalar('SELECT COUNT(*) FROM assets'), 1)


class WorkCategoryConstraintTests(unittest.TestCase):
    def test_schema_admission_rejects_different_category_literals(self):
        for category in ('cm', 'C M'):
            with self.subTest(category=category), closing(sqlite3.connect(':memory:')) as raw:
                raw.execute("CREATE TABLE orders(type TEXT CONSTRAINT ck_orders_work_type "
                            f"CHECK(type IN ('PM','EM','{category}','PROJECT','GENERAL')))")
                class Metadata:
                    is_sqlite = True

                    def scalar(self, sql):
                        return raw.execute(sql).fetchone()[0]

                with self.assertRaisesRegex(SchemaMismatchError, 'work-type constraint'):
                    verify_work_category_contract(Metadata())


class WorkCategoryMigrationPostgresqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_settings()

    def setUp(self):
        self.config, self.owner = fresh_database(self, prefix='categup', initialize=False)
        self.runtime = Database(self.config)
        self.work = WorkService(self.runtime)
        with self.owner.transaction() as connection:
            for script in ('postgresql-v11.sql', *SCRIPTS[1:], 'postgresql-oee-v1.sql'):
                connection.raw.execute((ROOT/'schema'/script).read_text(encoding='utf-8-sig'))
            connection.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
            connection.execute(f'GRANT USAGE ON SCHEMA public TO "{self.config.user}"')
            connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO "{self.config.user}"')
            connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO "{self.config.user}"')
        seed(self.runtime)
        with self.owner.transaction() as connection:
            connection.execute("INSERT INTO oee_lines VALUES('line','QA-L1','Synthetic line','pump',1,1,'{}')")
            connection.execute("INSERT INTO oee_sheets VALUES('sheet','line','2026-01-20','morning',1,'final',480,960,'{\"retained\":true}','{}','synthetic','tech','2026-01-20')")
            connection.execute("INSERT INTO oee_sheet_revisions VALUES('sheet',1,'{\"retained\":true}','tech','2026-01-20')")
        self.project = self.work.save(TECHNICIAN, report('PROJECT', assetId='pump'), request_key='legacy-project')

    def snapshot(self):
        with self.owner.transaction(write=False) as connection:
            tables = connection.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name<>'schema_version'")
            return {name: connection.query('SELECT to_jsonb(t)::text AS row FROM ' + name + ' t ORDER BY to_jsonb(t)::text')
                    for row in tables for name in (row['table_name'],)}

    def test_upgrade_preserves_rows_and_concurrent_general_saves_reuse_hall_target(self):
        self.assertEqual(preflight_postgresql_v11(self.owner, runtime_user=self.config.user)['schemaVersion'], 11)
        before = self.snapshot()
        result = upgrade_postgresql_v11(self.owner, runtime_user=self.config.user)
        self.assertEqual((result['schemaVersion'], result['alreadyApplied']), (12, False))
        self.assertTrue(result['optionalOeePreserved'])
        self.assertEqual(self.snapshot(), before)
        self.runtime.verify_schema()
        self.assertTrue(upgrade_postgresql_v11(self.owner, runtime_user=self.config.user)['alreadyApplied'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.work.get(TECHNICIAN, self.project['id'])['type'], 'PROJECT')

        together = Barrier(2, timeout=10)
        def save(technician):
            together.wait()
            return self.work.save(technician, report('GENERAL', technician=technician, hall='QA hall'), request_key='concurrent')

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(save, (TECHNICIAN, SECOND_TECHNICIAN))
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['assetId'], second['assetId'])
        self.assertEqual(self.work.save(TECHNICIAN, report('CM', assetId='pump'))['type'], 'CM')
        with self.runtime.transaction(write=False) as connection:
            self.assertEqual(connection.scalar('SELECT COUNT(*) FROM assets'), 2)

    def test_forged_target_marker_does_not_pass_read_only_schema_admission(self):
        with self.owner.transaction() as connection:
            connection.execute('UPDATE schema_version SET version=12')
        with self.assertRaisesRegex(SchemaMismatchError, 'work-type constraint'):
            self.runtime.verify_schema()
        with self.assertRaisesRegex(SchemaMismatchError, 'work-type constraint'):
            preflight_postgresql_v11(self.owner, runtime_user=self.config.user)
