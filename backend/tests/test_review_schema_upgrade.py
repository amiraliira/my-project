"""Explicit schema 9 -> 10 preserves history and validates correction evidence."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pak_maintenance.core_schema_migration import preflight_postgresql_v9, upgrade_postgresql_v9, upgrade_postgresql_v8
from pak_maintenance.database import Database, SchemaMismatchError
from pak_maintenance.common import dumps, loads
from pak_maintenance import pm_archive
from pg_test_support import ROOT, assert_database_constraints, fresh_database, initialize_schema8, initialize_schema9, qa_settings
import test_core_schema_upgrade as old_fixtures
import test_review_correction as review_fixtures

SCRIPT = ROOT / 'schema/postgresql-upgrade-9-to-10.sql'


class ReviewSchemaUpgradePostgresqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_settings()

    def setUp(self):
        self.config, self.owner = fresh_database(self, prefix='reviewup', initialize=False)
        initialize_schema9(self.owner, self.config.user, optional_oee=True)
        self.runtime = Database(self.config)
        old_fixtures.CoreSchemaUpgradePostgresqlTests.seed_history(self)
        with self.runtime.transaction() as c:
            c.execute("INSERT INTO participant_grading_policies VALUES('pm-old',1,'tech','mechanical','technician','two-stage-v1','2026-10-02')")

    snapshot = old_fixtures.CoreSchemaUpgradePostgresqlTests.snapshot

    def direct_sql(self, runtime_user=None, database=None):
        with (database or self.owner).transaction() as c:
            c.execute("SELECT set_config('pak.runtime_user',:role,true)", {'role': runtime_user or self.config.user})
            c.raw.execute(SCRIPT.read_text(encoding='utf-8'))

    def assert_old9(self, rows):
        self.assertEqual(self.snapshot(), rows)
        with self.owner.transaction(write=False) as c:
            self.assertEqual(c.query('SELECT version FROM schema_version'), [{'version': 9}])
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pg_trigger WHERE tgname='guard_pm_archive_correction_insert'"), 0)
        self.assertEqual(preflight_postgresql_v9(self.owner, runtime_user=self.config.user)['schemaVersion'], 9)

    def test_native_upgrade_preserves_every_row_and_optional_history_and_reapplies(self):
        before = self.snapshot()
        self.assertEqual(preflight_postgresql_v9(self.owner, runtime_user=self.config.user),
                         dict(schemaVersion=9, alreadyApplied=False, optionalOeePresent=True))
        with self.assertRaises(SchemaMismatchError):
            self.runtime.verify_schema()
        result = upgrade_postgresql_v9(self.owner, runtime_user=self.config.user)
        self.assertEqual(result['schemaVersion'], 10)
        self.assertFalse(result['alreadyApplied'])
        self.assertFalse(result['historicalArchivesRewritten'])
        self.assertEqual(self.snapshot(), before)
        self.runtime.verify_schema()
        assert_database_constraints(self, self.owner)
        self.assertTrue(upgrade_postgresql_v9(self.owner, runtime_user=self.config.user)['alreadyApplied'])
        self.direct_sql()
        self.assertEqual(self.snapshot(), before)
        for query in ('DELETE FROM pm_submission_archive', 'UPDATE participant_grading_policies SET policy=policy',
                      'DELETE FROM oee_sheet_revisions', 'DELETE FROM audit'):
            with self.subTest(query=query), self.assertRaises(Exception) as rejected:
                with self.runtime.transaction() as c:
                    c.execute(query)
            self.assertEqual(rejected.exception.sqlstate, '23514')

    def test_explicit_schema8_to_9_to_10_chain_preserves_existing_company_shape(self):
        # The deployed baseline predates rc.11. Prove the two reviewed scripts
        # compose without treating schema 9 as the application's current one.
        self.config, self.owner = fresh_database(self, prefix='reviewchain', initialize=False)
        initialize_schema8(self.owner, self.config.user, optional_oee=True)
        self.runtime = Database(self.config)
        old_fixtures.CoreSchemaUpgradePostgresqlTests.seed_history(self)
        baseline = self.snapshot()
        first = upgrade_postgresql_v8(self.owner, runtime_user=self.config.user)
        self.assertEqual(first['schemaVersion'], 9)
        middle = self.snapshot()
        self.assertEqual({table: middle[table] for table in baseline}, baseline)
        self.assertEqual(middle['participant_grading_policies'], [])
        self.assertEqual(middle['pm_responsibility_transfers'], [])
        last = upgrade_postgresql_v9(self.owner, runtime_user=self.config.user)
        self.assertEqual(last['schemaVersion'], 10)
        self.assertEqual(self.snapshot(), middle)
        self.runtime.verify_schema()
        assert_database_constraints(self, self.owner)

    def test_failure_after_new_ddl_rolls_back_guards_constraint_marker_and_all_rows(self):
        before = self.snapshot()
        source = SCRIPT.read_text(encoding='utf-8')
        failing = source.replace(' UPDATE schema_version SET version=10 WHERE version=9;',
                                 " RAISE EXCEPTION 'injected after review DDL';\n UPDATE schema_version SET version=10 WHERE version=9;")
        self.assertNotEqual(source, failing)
        with tempfile.TemporaryDirectory(prefix='pak-review-upgrade-') as temporary:
            path = Path(temporary)
            (path / SCRIPT.name).write_text(failing, encoding='utf-8')
            with self.assertRaisesRegex(Exception, 'injected after review DDL'):
                upgrade_postgresql_v9(self.owner, runtime_user=self.config.user, schema_directory=path)
        self.assert_old9(before)
        self.assertFalse(upgrade_postgresql_v9(self.owner, runtime_user=self.config.user)['alreadyApplied'])

    def test_wrong_roles_missing_crud_or_excess_privileges_cannot_publish_marker(self):
        before = self.snapshot()
        for role in (self.owner.config.user, 'missing_synthetic_role'):
            with self.subTest(role=role), self.assertRaises(SchemaMismatchError):
                upgrade_postgresql_v9(self.owner, runtime_user=role)
            with self.assertRaises(Exception):
                self.direct_sql(role)
            self.assert_old9(before)
        with self.assertRaises(SchemaMismatchError):
            upgrade_postgresql_v9(self.runtime, runtime_user=self.config.user)
        for alter, restore in ((f'REVOKE UPDATE ON assets FROM "{self.config.user}"', f'GRANT UPDATE ON assets TO "{self.config.user}"'),
                               (f'GRANT TRUNCATE ON assets TO "{self.config.user}"', f'REVOKE TRUNCATE ON assets FROM "{self.config.user}"')):
            with self.owner.transaction() as c:
                c.execute(alter)
            try:
                with self.assertRaises(SchemaMismatchError):
                    upgrade_postgresql_v9(self.owner, runtime_user=self.config.user)
                with self.assertRaises(Exception):
                    self.direct_sql()
                self.assertEqual(self.snapshot(), before)
            finally:
                with self.owner.transaction() as c:
                    c.execute(restore)
        self.assert_old9(before)

    def test_missing_guard_duplicate_marker_and_incomplete_table_block_upgrade(self):
        before = self.snapshot()
        for alter, restore in (("ALTER TABLE audit DISABLE TRIGGER guard_audit_history", "ALTER TABLE audit ENABLE TRIGGER guard_audit_history"),
                               ("INSERT INTO schema_version VALUES(9)", "DELETE FROM schema_version; INSERT INTO schema_version VALUES(9)")):
            with self.owner.transaction() as c:
                c.raw.execute(alter)
            try:
                with self.assertRaises(SchemaMismatchError):
                    upgrade_postgresql_v9(self.owner, runtime_user=self.config.user)
                with self.assertRaises(Exception):
                    self.direct_sql()
                self.assertEqual(self.snapshot(), before)
            finally:
                with self.owner.transaction() as c:
                    c.raw.execute(restore)
        self.assert_old9(before)


class PmArchiveCorrectionGuardTests(unittest.TestCase):
    setUp = review_fixtures.PmReviewCorrectionTests.setUp
    tearDown = review_fixtures.PmReviewCorrectionTests.tearDown
    custom = review_fixtures.PmReviewCorrectionTests.custom
    activate = review_fixtures.PmReviewCorrectionTests.activate
    order_id = review_fixtures.PmReviewCorrectionTests.order_id
    seed_review = review_fixtures.PmReviewCorrectionTests.seed_review
    correction = review_fixtures.PmReviewCorrectionTests.correction

    def reject_snapshot(self, mutate):
        original_insert = pm_archive._insert
        with self.db.connect() as c:
            before = {table: c.query('SELECT * FROM ' + table) for table in
                      ('orders', 'participants', 'order_parts', 'participant_grading_policies', 'pm_weekly_occurrences', 'pm_submission_archive', 'audit')}
        def tampered_insert(c, record):
            changed = dict(record)
            snapshot = loads(record['body'])
            mutate(snapshot, changed)
            changed['body'] = dumps(snapshot)
            changed['body_sha256'] = hashlib.sha256(changed['body'].encode()).hexdigest()
            original_insert(c, changed)
        with patch.object(pm_archive, '_insert', side_effect=tampered_insert):
            with self.assertRaises(Exception) as rejected:
                self.correction()
        if not self.db.is_sqlite:
            self.assertEqual(rejected.exception.sqlstate, '23514')
        with self.db.connect() as c:
            self.assertEqual(before, {table: c.query('SELECT * FROM ' + table) for table in before})

    def test_clock_dates_duration_and_occurrence_stamps_cannot_be_forged_with_a_new_hash(self):
        for field, value in (('startTime', '07:00'), ('endTime', '09:00'), ('durationMinutes', 89), ('workDate', '2026-09-25')):
            with self.subTest(field=field):
                self.reject_snapshot(lambda body, record, field=field, value=value: body['execution'].__setitem__(field, value))
        for field in ('executed_at', 'submitted_at'):
            with self.subTest(field=field):
                self.reject_snapshot(lambda body, record, field=field: record.__setitem__(field, '2026-09-25T00:00:00Z'))

    def test_consumption_quantity_ids_duplicates_missing_rows_and_labels_must_match_current_lines(self):
        from decimal import Decimal
        mutations = [lambda b, r: b['execution']['parts'][0].__setitem__('quantity', Decimal('2.250001')),
                     lambda b, r: b['execution']['parts'][0].__setitem__('quantity', Decimal('2.250000000000000001')),
                     lambda b, r: b['execution']['parts'][0].__setitem__('partId', 'part'),
                     lambda b, r: b['execution'].__setitem__('parts', []),
                     lambda b, r: b['execution']['parts'].append(deepcopy(b['execution']['parts'][0])),
                     lambda b, r: b['execution']['parts'][0].__setitem__('name', 'Forged label')]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                self.reject_snapshot(mutate)


class PmArchiveCorrectionGuardPostgresqlTests(PmArchiveCorrectionGuardTests):
    setUp = review_fixtures.PmReviewCorrectionPostgresqlTests.setUp
    tearDown = review_fixtures.PmReviewCorrectionPostgresqlTests.tearDown


def load_tool():
    spec = importlib.util.spec_from_file_location('upgrade_review_tool', ROOT / 'tools/upgrade_review_schema.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


class ReviewUpgradeToolTests(unittest.TestCase):
    def config(self, **changes):
        result = dict(database=object(), backup_directory='private-backups', postgresql_dump_executable='pg_dump.exe', postgresql_restore_executable='pg_restore.exe')
        result.update(changes)
        return SimpleNamespace(**result)

    def test_read_only_preflight_then_verified_backup_precede_upgrade(self):
        tool, calls = load_tool(), []
        snapshot = SimpleNamespace(path=Path('native.dump'), size=123, sha256='a' * 64)
        def preflight(*args, **kwargs):
            calls.append('preflight')
            return dict(schemaVersion=9)
        def backup():
            self.assertEqual(calls, ['preflight'])
            calls.append('backup')
            return snapshot
        def upgrade(*args, **kwargs):
            self.assertEqual(calls, ['preflight', 'backup'])
            calls.append('upgrade')
            return dict(schemaVersion=10)
        with patch.object(tool.AppConfig, 'load', return_value=self.config()), patch.object(tool, 'Database'), \
             patch.object(tool, 'preflight_postgresql_v9', side_effect=preflight), patch.object(tool, 'BackupService') as service, \
             patch.object(tool, 'upgrade_postgresql_v9', side_effect=upgrade), patch('builtins.print') as output:
            service.return_value.create_snapshot.side_effect = backup
            self.assertEqual(tool.main(['--owner-config', 'owner.toml', '--runtime-user', 'limited_app']), 0)
        self.assertEqual(calls, ['preflight', 'backup', 'upgrade'])
        self.assertNotIn('password', str(output.call_args).lower())

    def test_missing_tools_failed_preflight_or_backup_cannot_mutate(self):
        tool = load_tool()
        for failure in ('tools', 'preflight', 'backup'):
            with self.subTest(failure=failure), patch.object(tool.AppConfig, 'load', return_value=self.config(postgresql_dump_executable=None) if failure == 'tools' else self.config()), \
                 patch.object(tool, 'Database'), patch.object(tool, 'preflight_postgresql_v9') as preflight, \
                 patch.object(tool, 'BackupService') as backup, patch.object(tool, 'upgrade_postgresql_v9') as upgrade:
                if failure == 'preflight': preflight.side_effect = RuntimeError('invalid source')
                if failure == 'backup': backup.return_value.create_snapshot.side_effect = RuntimeError('verification failed')
                with self.assertRaises((ValueError, RuntimeError)):
                    tool.main(['--owner-config', 'owner.toml', '--runtime-user', 'limited_app'])
                upgrade.assert_not_called()
                if failure in ('tools', 'preflight'): backup.assert_not_called()
