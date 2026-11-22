"""Atomic owner-run 8 -> 9 upgrades on generated, isolated PostgreSQL history."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pak_maintenance.core_schema_migration import preflight_postgresql_v8, upgrade_postgresql_v8
from pak_maintenance.database import Database, SchemaMismatchError
from pg_test_support import ROOT, assert_database_constraints, fresh_database, initialize_schema8, qa_settings


SCRIPT = ROOT / "schema" / "postgresql-upgrade-8-to-9.sql"


class CoreSchemaUpgradePostgresqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qa_settings()

    def setUp(self):
        self.config, self.owner = fresh_database(self, prefix="upgrade", initialize=False)
        initialize_schema8(self.owner, self.config.user, optional_oee=True)
        self.runtime = Database(self.config)
        self.seed_history()

    def seed_history(self, *, include_oee=True):
        # All rows are generated test data, inserted with the reviewed guards on.
        with self.runtime.transaction() as c:
            for person, role in (("tech", "technician"), ("super", "supervisor"), ("chief", "head")):
                c.execute("INSERT INTO users VALUES(:id,:id,:id,:role,'mechanical',1,'synthetic-hash',7)", dict(id=person, role=role))
            c.execute("INSERT INTO assets VALUES('asset','A01','Synthetic pump','QA hall','QA line','{}',3)")
            c.execute("INSERT INTO parts VALUES('part','P01','Synthetic bearing','','each',12.345,4)")
            c.execute("INSERT INTO orders VALUES('old','OLD-001',5,1,'EM','supervisor','asset','tech','tech','2026-10-01',NULL,'{\"description\":\"old work\"}','2026-10-01','2026-10-01',60,'Old work','Old work')")
            c.execute("INSERT INTO participants VALUES('old','tech',60,'mechanical')")
            c.execute("INSERT INTO order_parts VALUES('old','part',1.25,1250000)")
            c.execute("INSERT INTO approvals VALUES('sup-approval','old',1,'supervisor','mechanical','approve',NULL,'','super','2026-10-01')")
            c.execute("INSERT INTO participant_scores VALUES('old',1,'tech','mechanical',2.5,'super','sup-approval','2026-10-01')")
            c.execute("UPDATE orders SET status='head' WHERE id='old'")
            c.execute("INSERT INTO approvals VALUES('head-approval','old',1,'head','','approve',NULL,'','chief','2026-10-01')")
            c.execute("INSERT INTO participant_head_scores VALUES('old',1,'tech','mechanical','2.5','chief','head-approval','2026-10-01')")
            c.execute("UPDATE orders SET status='completed' WHERE id='old'")
            c.execute("INSERT INTO completed_scores(order_id,revision,person_id,supervisor_coefficient,head_coefficient,score_raw,score_cents,source,head_coefficient_exact) VALUES('old',1,'tech',2.5,2.5,'6.25',625,'individual','2.5')")
            c.execute("INSERT INTO part_consumptions VALUES('consumption','old','part','head-approval',1,1250000,'P01','Synthetic bearing','each','OLD-001','2026-10-01','2026-10-01','chief','Synthetic chief')")
            c.execute("INSERT INTO part_consumption_totals VALUES('part',1250000,1)")
            c.execute("INSERT INTO request_claims VALUES('tech','old-request','old','synthetic')")
            c.execute("INSERT INTO audit VALUES('audit','chief','Synthetic chief','approve','order','old','{\"retained\":true}','2026-10-01')")
            c.execute("INSERT INTO sessions VALUES('old-session','tech',7,'2028-01-01')")
            c.execute("INSERT INTO legacy_records VALUES('legacy','{\"raw\":\"preserve\"}','2026-10-01')")
            c.execute("INSERT INTO pm_templates VALUES('template','Synthetic PM','mechanical','QA hall','monthly',1,'confirmed','{}')")
            c.execute("INSERT INTO pm_template_revisions VALUES('template',1,'{}','Synthetic revision','chief','2026-10-01')")
            c.execute("INSERT INTO pm_weekly_plans VALUES('plan','template','asset','super','mechanical','QA hall',1,1,1,'2026-10-01',7,'2026-10-01','{}')")
            c.execute("INSERT INTO pm_plan_revisions VALUES('plan',1,'{}','Synthetic revision','chief','2026-10-01')")
            c.execute("INSERT INTO orders VALUES('pm-old','OLD-PM',4,1,'PM','supervisor','asset','chief','tech','2026-10-02',NULL,'{\"pmResults\":{},\"startTime\":\"08:00\",\"endTime\":\"09:00\",\"durationMinutes\":60}','2026-10-01','2026-10-02',60,'Historical PM','Historical PM')")
            c.execute("INSERT INTO participants VALUES('pm-old','tech',60,'mechanical')")
            c.execute("INSERT INTO pm_weekly_occurrences VALUES('plan',1405,7,1,'pm-old',1,'template',1,'2026-10-01','2026-10-07','mechanical','super','2026-10-02','2026-10-02')")
            c.execute("INSERT INTO pm_weekly_assets VALUES('pm-old','asset')")
            body = '{"results":{},"execution":{"workDate":"2026-10-02","startTime":"08:00","endTime":"09:00","durationMinutes":60}}'
            c.execute("INSERT INTO pm_submission_archive VALUES('archive','pm-old',1,'template',1,'plan',1,'asset','tech','super','tech','OLD-PM','Historical PM','QA hall','A01','Synthetic pump','Synthetic technician','mechanical','2026-10-02','2026-10-02','2026-10-02',:body,:digest,'submission-snapshot')", dict(body=body,digest=hashlib.sha256(body.encode()).hexdigest()))
            if include_oee:
                c.execute("INSERT INTO oee_lines VALUES('line','QA-L1','Synthetic line','asset',1,1,'{}')")
                c.execute("INSERT INTO oee_sheets VALUES('sheet','line','2026-10-02','morning',1,'final',480,960,'{\"retained\":true}','{}','synthetic','chief','2026-10-02')")
                c.execute("INSERT INTO oee_sheet_revisions VALUES('sheet',1,'{\"retained\":true}','chief','2026-10-02')")

    def snapshot(self):
        with self.owner.transaction(write=False) as c:
            tables = [r['table_name'] for r in c.query("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name<>'schema_version' ORDER BY table_name")]
            return {table: c.query("SELECT to_jsonb(t)::text AS row FROM " + table + " t ORDER BY to_jsonb(t)::text") for table in tables}

    def direct_sql(self, runtime_user=None, database=None):
        with (database or self.owner).transaction() as c:
            c.execute("SELECT set_config('pak.runtime_user',:role,true)", dict(role=runtime_user or self.config.user))
            c.raw.execute(SCRIPT.read_text(encoding="utf-8"))

    def assert_old8(self, expected):
        self.assertEqual(self.snapshot(), expected)
        with self.owner.transaction(write=False) as c:
            self.assertEqual(c.query("SELECT version FROM schema_version"), [{"version":8}])
            self.assertIsNone(c.scalar("SELECT to_regclass('public.participant_grading_policies')"))
            self.assertIsNone(c.scalar("SELECT to_regclass('public.pm_responsibility_transfers')"))
        self.assertEqual(preflight_postgresql_v8(self.owner, runtime_user=self.config.user)['schemaVersion'], 8)

    def test_native_upgrade_preserves_every_historical_row_oee_and_reapplies_without_changes(self):
        before = self.snapshot()
        self.assertEqual(preflight_postgresql_v8(self.owner, runtime_user=self.config.user),
                         dict(schemaVersion=8, alreadyApplied=False, optionalOeePresent=True))
        with self.assertRaises(SchemaMismatchError):
            self.runtime.verify_schema()
        result = upgrade_postgresql_v8(self.owner, runtime_user=self.config.user)
        self.assertEqual(result['schemaVersion'], 9)
        self.assertFalse(result['alreadyApplied'])
        self.assertFalse(result['historicalPoliciesBackfilled'])
        self.assertFalse(result['issuedPmResponsibilitiesBackfilled'])
        after = self.snapshot()
        self.assertEqual({table:after[table] for table in before}, before)
        self.assertEqual(after['participant_grading_policies'], [])
        self.assertEqual(after['pm_responsibility_transfers'], [])
        with self.runtime.transaction(write=False) as c:
            self.runtime._verify_schema_connection(c, expected_version=9)
        assert_database_constraints(self, self.owner)
        self.assertTrue(upgrade_postgresql_v8(self.owner, runtime_user=self.config.user)['alreadyApplied'])
        self.direct_sql()  # Reviewed raw SQL is also safe to reapply.
        self.assertEqual(self.snapshot(), after)
        for statement in ("UPDATE participant_head_scores SET coefficient='3'", "DELETE FROM pm_submission_archive", "DELETE FROM oee_sheet_revisions", "UPDATE pm_weekly_occurrences SET supervisor_id='chief'"):
            with self.subTest(statement=statement), self.assertRaises(Exception) as rejected:
                with self.runtime.transaction() as c:
                    c.execute(statement)
            self.assertEqual(rejected.exception.sqlstate, '23514')

    def test_ddl_failure_after_new_objects_rolls_back_schema_marker_guards_and_history(self):
        before = self.snapshot()
        source = SCRIPT.read_text(encoding="utf-8")
        failing = source.replace(" UPDATE schema_version SET version=9 WHERE version=8;", " RAISE EXCEPTION 'injected after DDL';\n UPDATE schema_version SET version=9 WHERE version=8;")
        self.assertNotEqual(failing, source)
        with tempfile.TemporaryDirectory(prefix='pak-upgrade-') as temporary:
            directory = Path(temporary)
            (directory / SCRIPT.name).write_text(failing, encoding='utf-8')
            with self.assertRaisesRegex(Exception, 'injected after DDL'):
                upgrade_postgresql_v8(self.owner, runtime_user=self.config.user, schema_directory=directory)
        self.assert_old8(before)
        self.assertFalse(upgrade_postgresql_v8(self.owner, runtime_user=self.config.user)['alreadyApplied'])

    def test_upgraded_guard_blocks_self_head_grade_after_account_promotion(self):
        upgrade_postgresql_v8(self.owner, runtime_user=self.config.user)
        with self.runtime.transaction() as c:
            c.execute("INSERT INTO orders VALUES('self-work','SELF-001',1,1,'EM','supervisor','asset','super','super','2026-10-03',NULL,'{}','2026-10-03','2026-10-03',60,'Supervisor work','Supervisor work')")
            c.execute("INSERT INTO participants VALUES('self-work','super',60,'mechanical')")
            c.execute("INSERT INTO participant_grading_policies VALUES('self-work',1,'super','mechanical','supervisor','head-only-v1','2026-10-03')")
            c.execute("UPDATE orders SET status='head' WHERE id='self-work'")
            c.execute("UPDATE users SET role='head' WHERE id='super'")
        with self.assertRaises(Exception) as rejected:
            with self.runtime.transaction() as c:
                c.execute("INSERT INTO approvals VALUES('self-head','self-work',1,'head','','approve',NULL,'','super','2026-10-03')")
                c.execute("INSERT INTO participant_head_scores VALUES('self-work',1,'super','mechanical','9','super','self-head','2026-10-03')")
        self.assertEqual(rejected.exception.sqlstate, '23514')
        with self.runtime.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM approvals WHERE id='self-head'"), 0)

    def test_runtime_role_and_incomplete_privileges_are_rejected_before_any_change(self):
        before = self.snapshot()
        for role in (self.owner.config.user, 'missing_synthetic_role'):
            with self.subTest(role=role), self.assertRaises(SchemaMismatchError):
                upgrade_postgresql_v8(self.owner, runtime_user=role)
            with self.subTest(direct_role=role), self.assertRaises(Exception):
                self.direct_sql(role)
            self.assert_old8(before)
        with self.assertRaises(SchemaMismatchError):
            upgrade_postgresql_v8(self.runtime, runtime_user=self.config.user)
        with self.assertRaises(Exception):
            self.direct_sql(database=self.runtime)
        for revoke, restore in ((f'REVOKE UPDATE ON assets FROM "{self.config.user}"', f'GRANT UPDATE ON assets TO "{self.config.user}"'),
                                (f'GRANT TRUNCATE ON assets TO "{self.config.user}"', f'REVOKE TRUNCATE ON assets FROM "{self.config.user}"')):
            with self.owner.transaction() as c:
                c.execute(revoke)
            try:
                with self.assertRaises(SchemaMismatchError):
                    upgrade_postgresql_v8(self.owner, runtime_user=self.config.user)
                with self.assertRaises(Exception):
                    self.direct_sql()
                self.assertEqual(self.snapshot(), before)
            finally:
                with self.owner.transaction() as c:
                    c.execute(restore)
        self.assert_old8(before)

    def test_missing_history_guard_and_duplicate_marker_refuse_partial_upgrade(self):
        before = self.snapshot()
        for alter, restore in (("ALTER TABLE audit DISABLE TRIGGER guard_audit_history", "ALTER TABLE audit ENABLE TRIGGER guard_audit_history"),
                               ("INSERT INTO schema_version VALUES(8)", "DELETE FROM schema_version; INSERT INTO schema_version VALUES(8)")):
            with self.owner.transaction() as c:
                c.raw.execute(alter)
            try:
                with self.assertRaises(SchemaMismatchError):
                    upgrade_postgresql_v8(self.owner, runtime_user=self.config.user)
                with self.assertRaises(Exception):
                    self.direct_sql()
                self.assertEqual(self.snapshot(), before)
            finally:
                with self.owner.transaction() as c:
                    c.raw.execute(restore)
        self.assert_old8(before)


def load_tool():
    spec = importlib.util.spec_from_file_location('upgrade_core_tool', ROOT / 'tools' / 'upgrade_core_schema.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


class CoreUpgradeToolTests(unittest.TestCase):
    def config(self, **changes):
        result = dict(database=object(), backup_directory='private-backups', postgresql_dump_executable='pg_dump.exe', postgresql_restore_executable='pg_restore.exe')
        result.update(changes)
        return SimpleNamespace(**result)

    def test_preflight_then_verified_backup_must_precede_upgrade(self):
        tool, calls = load_tool(), []
        snapshot = SimpleNamespace(path=Path('native.dump'), size=123, sha256='a'*64)
        def preflight(*args, **kwargs):
            calls.append('preflight')
            return dict(schemaVersion=8)
        def backup():
            self.assertEqual(calls, ['preflight'])
            calls.append('backup')
            return snapshot
        def upgrade(*args, **kwargs):
            self.assertEqual(calls, ['preflight', 'backup'])
            calls.append('upgrade')
            return dict(schemaVersion=9)
        with patch.object(tool.AppConfig,'load',return_value=self.config()), patch.object(tool,'Database'), \
             patch.object(tool,'preflight_postgresql_v8',side_effect=preflight), patch.object(tool,'BackupService') as service, \
             patch.object(tool,'upgrade_postgresql_v8',side_effect=upgrade), patch('builtins.print') as output:
            service.return_value.create_snapshot.side_effect=backup
            self.assertEqual(tool.main(['--owner-config','owner.toml','--runtime-user','limited_app']),0)
            self.assertEqual(calls,['preflight','backup','upgrade'])
            self.assertNotIn('password',str(output.call_args).lower())

    def test_missing_native_tools_or_failed_preflight_or_backup_never_upgrades(self):
        tool=load_tool()
        for failure in ('tools','preflight','backup'):
            with self.subTest(failure=failure), patch.object(tool.AppConfig,'load',return_value=self.config(postgresql_dump_executable=None) if failure=='tools' else self.config()), \
                 patch.object(tool,'Database'), patch.object(tool,'preflight_postgresql_v8') as preflight, \
                 patch.object(tool,'BackupService') as backup, patch.object(tool,'upgrade_postgresql_v8') as upgrade:
                if failure=='preflight': preflight.side_effect=RuntimeError('invalid source')
                if failure=='backup': backup.return_value.create_snapshot.side_effect=RuntimeError('verification failed')
                with self.assertRaises((ValueError,RuntimeError)):
                    tool.main(['--owner-config','owner.toml','--runtime-user','limited_app'])
                upgrade.assert_not_called()
                if failure in ('tools','preflight'): backup.assert_not_called()
