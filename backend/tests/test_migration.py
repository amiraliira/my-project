"""Synthetic, disposable snapshot tests; never access company data or a server."""
from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.database import Connection, Database, DatabaseConfig, SchemaMismatchError
from pak_maintenance.migration import MigrationError, TABLES, migrate_snapshot, snapshot_sha256, verify_schema8_snapshot_target
from pak_maintenance.local_postgres import _create_private_directory


SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "fixtures" / "schema8"
SCHEMA = (SCHEMA_DIRECTORY / "sqlite-v6.sql").read_text(encoding="utf-8")
CURRENT_SCHEMA = (SCHEMA_DIRECTORY / "sqlite-v8.sql").read_text(encoding="utf-8")
GUARDS = "\n".join((SCHEMA_DIRECTORY / name).read_text(encoding="utf-8") for name in
                   ("sqlite-ledger-guards.sql", "sqlite-pm-guards.sql", "sqlite-evidence-guards.sql", "sqlite-archive-guards.sql"))
BODY = '{"عنوان":"آزمایش پمپ","quantity":0.123456,"untouched":true}'


@contextmanager
def fixture_directory():
    # Original PM names are SHA-256 based. A deeply nested source checkout can
    # exceed native Windows MAX_PATH during the explicit offline copy tests.
    root = Path(tempfile.gettempdir()) / ("pak-mig-" + uuid4().hex[:12])
    _create_private_directory(root)
    child = root / uuid4().hex
    child.mkdir(mode=0o755)
    try:
        yield child
    finally:
        if child.resolve().parent != root.resolve() or child.is_symlink():
            raise RuntimeError("Refusing fixture cleanup outside the migration test root.")
        shutil.rmtree(child)
        try:
            root.rmdir()
        except OSError:
            pass


def create_database(path: Path, *, populated=False, guards=False, version=6) -> None:
    raw = sqlite3.connect(path)
    try:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.executescript(CURRENT_SCHEMA if version == 8 else (SCHEMA_DIRECTORY / "sqlite-v7.sql").read_text(encoding="utf-8") if version == 7 else SCHEMA)
        if guards:
            raw.executescript(GUARDS if version == 8 else "\n".join((SCHEMA_DIRECTORY / name).read_text(encoding="utf-8") for name in ("sqlite-ledger-guards.sql", "sqlite-pm-guards.sql", "sqlite-evidence-guards.sql")))
        if populated:
            raw.execute("INSERT INTO users VALUES('person','001','تکنسین','technician','مکانیک',1,'legacy-pbkdf-hash',4)")
            raw.execute("INSERT INTO users VALUES('supervisor','002','سرپرست','supervisor','مکانیک',1,'legacy-supervisor-hash',4)")
            raw.execute("INSERT INTO users VALUES('head','003','رئیس','head','',1,'legacy-head-hash',4)")
            raw.execute("INSERT INTO assets VALUES('asset','A01','پمپ','فرایند','یک',?,1)", (BODY,))
            raw.execute("INSERT INTO parts VALUES('part','P001','قطعه','مشخصات','عدد',12345.67,2)")
            raw.execute("INSERT INTO orders VALUES('order','R001',4,1,'EM','completed','asset','person','person','2026-10-11',NULL,?,'2026-10-11','2026-10-11',60,'تعمیر','شرح')", (BODY,))
            raw.execute("INSERT INTO sessions VALUES('discarded','person',4,'2099-01-01T00:00:00.0000000+00:00')")
            raw.execute("INSERT INTO approvals VALUES('supervisor-approval','order',1,'supervisor','مکانیک','approve',1.25,'','supervisor','2026-10-11')")
            raw.execute("INSERT INTO approvals VALUES('approval','order',1,'head','','approve',2.5,'','head','2026-10-11')")
            raw.execute("INSERT INTO participants VALUES('order','person',60,'مکانیک')")
            raw.execute("INSERT INTO order_parts VALUES('order','part',0.123456,123456)")
            raw.execute("INSERT INTO participant_scores VALUES('order',1,'person','مکانیک',1.25,'supervisor','supervisor-approval','2026-10-11')")
            raw.execute("INSERT INTO completed_scores(order_id,revision,person_id,supervisor_coefficient,head_coefficient,score_raw,score_cents,source) VALUES('order',1,'person',1.25,2.5,'3.125',312,'individual')")
            raw.execute("INSERT INTO part_consumptions VALUES('consumption','order','part','approval',1,123456,'P001','قطعه','عدد','R001','2026-10-11','2026-10-11','head','رئیس')")
            raw.execute("INSERT INTO part_consumption_totals VALUES('part',123456,1)")
            raw.execute("INSERT INTO audit VALUES('audit','person','تکنسین','approve','order','order',?,'2026-10-11')", (BODY,))
        raw.commit()
    finally:
        raw.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_directory()
        self.root = self.fixture.__enter__()
        self.source = self.root / "frozen.db"
        self.target = self.root / "target.db"
        create_database(self.source, populated=True)
        create_database(self.target, guards=True, version=8)
        self.expected = snapshot_sha256(self.source)
        self.db = Database(DatabaseConfig(provider="sqlite", database=str(self.target), development=True))

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def run_migration(self):
        return migrate_snapshot(self.source, self.db, expected_sha256=self.expected)

    def assert_target_empty(self, *, guards=True):
        with self.db.connect() as c:
            for spec in TABLES:
                self.assertEqual(c.scalar(f"SELECT COUNT(*) FROM {spec.name}"), 1 if spec.name == "schema_version" else 0, spec.name)
        if guards:
            verify_schema8_snapshot_target(self.db)

    def test_copy_preserves_source_hash_history_json_and_exact_units(self):
        report = self.run_migration()
        self.assertEqual(snapshot_sha256(self.source), self.expected)
        self.assertEqual(report.source_sha256, self.expected)
        self.assertEqual(report.target_provider, "sqlite-test")
        self.assertEqual(report.schema_version, 8)
        self.assertEqual(report.source_schema_version, 6)
        self.assertEqual(report.discarded_sessions, 1)
        self.assertEqual(len(report.tables), 28)
        self.assertTrue(all(len(table.sha256) == 64 for table in report.tables))
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT session_epoch FROM users WHERE id='person'"), 5)
            self.assertEqual(c.scalar("SELECT password_hash FROM users WHERE id='person'"), "legacy-pbkdf-hash")
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM sessions"), 0)
            self.assertEqual(c.scalar("SELECT body FROM orders"), BODY)
            self.assertEqual(c.scalar("SELECT body FROM assets"), BODY)
            self.assertEqual(c.scalar("SELECT details FROM audit"), BODY)
            self.assertEqual(c.scalar("SELECT quantity_units FROM order_parts"), 123456)
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumptions"), 123456)
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), 123456)
            self.assertEqual(c.scalar("SELECT score_raw FROM completed_scores"), "3.125")
            self.assertEqual(c.scalar("SELECT version FROM schema_version"), 8)
            self.assertIsNone(c.scalar("SELECT head_coefficient_exact FROM completed_scores"))
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM participant_head_scores"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_change_requests"), 0)

    def test_schema8_snapshot_preserves_individual_chief_and_decided_pm_history(self):
        from decimal import Decimal
        self.run_migration()
        exact = Decimal("0.3333333333333333333333333333")
        # Freeze schema-8 historical facts through its own evidence guards.
        # The current WorkService requires schema 10 and must not execute on
        # this deliberately old snapshot or add current-only policy tables.
        with self.db.transaction() as c:
            c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,created_at,updated_at,duration_minutes,title,description)
SELECT 'exact-order','R002',1,1,type,'head',asset_id,created_by,assignee_id,work_date,due_date,body,created_at,updated_at,duration_minutes,title,description FROM orders WHERE id='order'""")
            c.execute("INSERT INTO participants VALUES('exact-order','person',60,'مکانیک')")
            c.execute("INSERT INTO approvals VALUES('exact-supervisor','exact-order',1,'supervisor','مکانیک','approve',NULL,'','supervisor','2026-10-11')")
            c.execute("INSERT INTO participant_scores VALUES('exact-order',1,'person','مکانیک',1.25,'supervisor','exact-supervisor','2026-10-11')")
            c.execute("INSERT INTO approvals VALUES('exact-head','exact-order',1,'head','','approve',NULL,'','head','2026-10-11')")
            c.execute("INSERT INTO participant_head_scores VALUES('exact-order',1,'person','مکانیک',:exact,'head','exact-head','2026-10-11')", {'exact': str(exact)})
            c.execute("UPDATE orders SET status='completed' WHERE id='exact-order'")
            c.execute("""INSERT INTO completed_scores(order_id,revision,person_id,supervisor_coefficient,head_coefficient,head_coefficient_exact,score_raw,score_cents,source)
VALUES('exact-order',1,'person',1.25,:exact,:exact,'0.4166666666666666666666666666',42,'individual')""", {'exact': str(exact)})
            c.execute("""INSERT INTO pm_change_requests(id,request_key,requester_id,operation,entity_id,base_version,payload,payload_hash,status,version,decision_reason,created_at)
VALUES('review','one','supervisor','create-template','',NULL,'{}','synthetic','pending',1,'','2026-10-11')""")
            c.execute("UPDATE pm_change_requests SET status='approved',version=2,reviewer_id='head',decision_reason='تأیید شد',result='{}',decided_at='2026-10-11' WHERE id='review'")
        frozen = self.root / "current-frozen.db"
        with closing(sqlite3.connect(self.target)) as source, closing(sqlite3.connect(frozen)) as destination:
            source.backup(destination)
        current_sha = snapshot_sha256(frozen)
        empty = self.root / "current-target.db"
        create_database(empty, guards=True, version=8)
        destination = Database(DatabaseConfig(provider="sqlite", database=str(empty), development=True))
        result = migrate_snapshot(frozen, destination, expected_sha256=current_sha)
        self.assertEqual((result.source_schema_version, result.schema_version), (8, 8))
        self.assertEqual(len(result.tables), 28)
        self.assertEqual(snapshot_sha256(frozen), current_sha)
        verify_schema8_snapshot_target(destination)
        with destination.connect() as c:
            self.assertEqual(c.scalar("SELECT coefficient FROM participant_head_scores"), str(exact))
            self.assertEqual(c.scalar("SELECT head_coefficient_exact FROM completed_scores WHERE head_coefficient_exact IS NOT NULL"), str(exact))
            self.assertEqual(c.one("SELECT status,reviewer_id,decision_reason,result FROM pm_change_requests"), dict(status="approved",reviewer_id="head",decision_reason="تأیید شد",result="{}"))
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), 2)
            self.assertEqual(c.scalar("SELECT session_epoch FROM users WHERE id='person'"), 6)
            self.assertEqual(c.query("PRAGMA foreign_key_check"), [])

    def test_guard_restoration_failure_rolls_back_rows_and_trigger_ddl(self):
        original = Connection.execute
        def fail_restoring(connection, sql, params=None):
            if sql.startswith("CREATE TRIGGER") and "guard_approval_evidence_insert" in sql:
                raise sqlite3.OperationalError("Injected restoration failure")
            return original(connection, sql, params)
        with patch.object(Connection, "execute", fail_restoring):
            with self.assertRaisesRegex(sqlite3.OperationalError, "restoration"):
                self.run_migration()
        self.assert_target_empty()

    def test_explicit_migration_authority_keeps_full_schema_guard_verification(self):
        with patch("pak_maintenance.migration.verify_schema8_snapshot_target", wraps=verify_schema8_snapshot_target) as verify:
            self.run_migration()
        verify.assert_called_once_with(self.db, require_guards=True, runtime_role=False)

    def test_hash_is_independently_required_and_wrong_hash_never_writes(self):
        for value in ("", "xyz", "0" * 64, None, "a" * 65):
            with self.subTest(value=value), self.assertRaises(MigrationError):
                migrate_snapshot(self.source, self.db, expected_sha256=value)
        self.assert_target_empty()

    def test_nonempty_target_is_rejected_and_preserved(self):
        with self.db.transaction() as c:
            c.execute("INSERT INTO legacy_records VALUES('existing','{}','2026-10-11')")
        with self.assertRaisesRegex(MigrationError, "not empty"):
            self.run_migration()
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM legacy_records"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM users"), 0)

    def test_schema_version_or_unknown_table_or_column_is_rejected(self):
        for mutation in ("UPDATE schema_version SET version=5", "CREATE TABLE unknown(id TEXT)", "CREATE TABLE sqliteXunknown(id TEXT)", "ALTER TABLE users ADD COLUMN unexpected TEXT"):
            with self.subTest(mutation=mutation):
                self.source.unlink()
                create_database(self.source, populated=True)
                with closing(sqlite3.connect(self.source)) as raw, raw:
                    raw.execute(mutation)
                self.expected = snapshot_sha256(self.source)
                with self.assertRaises(MigrationError):
                    self.run_migration()
                self.assert_target_empty()

    def test_target_unknown_schema_is_rejected(self):
        with self.db.transaction() as c:
            c.execute("CREATE TABLE unknown(id TEXT)")
        with self.assertRaisesRegex(MigrationError, "unknown"):
            self.run_migration()
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM users"), 0)

    def test_sidecars_are_rejected_even_if_empty(self):
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.source) + suffix)
            sidecar.write_bytes(b"")
            try:
                with self.subTest(suffix=suffix), self.assertRaisesRegex(MigrationError, "sidecars"):
                    self.run_migration()
            finally:
                sidecar.unlink()
        self.assert_target_empty()

    def test_same_source_target_and_production_sqlite_are_rejected(self):
        same = Database(DatabaseConfig(provider="sqlite", database=str(self.source), development=True))
        with self.assertRaisesRegex(MigrationError, "different"):
            migrate_snapshot(self.source, same, expected_sha256=self.expected)
        production = Database(DatabaseConfig(provider="sqlite", database=str(self.target)))
        with self.assertRaisesRegex(MigrationError, "development"):
            migrate_snapshot(self.source, production, expected_sha256=self.expected)
        self.assert_target_empty()

    def test_constraint_failure_rolls_back_all_preceding_tables(self):
        original = Connection.executemany
        def fail_on_orders(connection, sql, rows):
            if sql.startswith("INSERT INTO orders"):
                raise sqlite3.IntegrityError("Injected synthetic target constraint failure")
            return original(connection, sql, rows)
        with patch.object(Connection, "executemany", fail_on_orders):
            with self.assertRaises(sqlite3.IntegrityError):
                self.run_migration()
        self.assert_target_empty()
        self.assertEqual(snapshot_sha256(self.source), self.expected)

    def test_content_verification_detects_mutation_and_rolls_back(self):
        original = Connection.executemany
        def tamper_with_copy(connection, sql, rows):
            rows = list(rows)
            if sql.startswith("INSERT INTO assets"):
                rows[0] = dict(rows[0], name="Corrupted synthetic copy")
            return original(connection, sql, rows)
        with patch.object(Connection, "executemany", tamper_with_copy):
            with self.assertRaisesRegex(MigrationError, "verification failed"):
                self.run_migration()
        self.assert_target_empty()

    def test_final_source_hash_change_rolls_back_before_commit(self):
        with patch("pak_maintenance.migration.snapshot_sha256", side_effect=[self.expected, "0" * 64]):
            with self.assertRaisesRegex(MigrationError, "changed"):
                self.run_migration()
        self.assert_target_empty()

    def test_primary_key_order_and_pages_are_stable_across_100_row_batches(self):
        with closing(sqlite3.connect(self.source)) as raw, raw:
            raw.executemany("INSERT INTO legacy_records VALUES(?,?,?)", [(f"{i:04d}-ت", BODY, "2026-10-11") for i in reversed(range(257))])
        self.expected = snapshot_sha256(self.source)
        original = Connection.executemany
        sizes = []
        def record_batch(connection, sql, rows):
            rows = list(rows)
            sizes.append(len(rows))
            return original(connection, sql, rows)
        with patch.object(Connection, "executemany", record_batch):
            report = self.run_migration()
        self.assertEqual(next(table.rows for table in report.tables if table.table == "legacy_records"), 257)
        self.assertEqual(max(sizes), 100)
        self.assertEqual(snapshot_sha256(self.source), self.expected)

    def test_broken_source_foreign_key_is_rejected(self):
        with closing(sqlite3.connect(self.source)) as raw, raw:
            raw.execute("UPDATE participants SET person_id='missing'")
        self.expected = snapshot_sha256(self.source)
        with self.assertRaisesRegex(MigrationError, "foreign-key"):
            self.run_migration()
        self.assert_target_empty()

    def test_unknown_source_trigger_is_rejected(self):
        with closing(sqlite3.connect(self.source)) as raw, raw:
            raw.execute("CREATE TRIGGER surprise AFTER INSERT ON legacy_records BEGIN SELECT 1; END")
        self.expected = snapshot_sha256(self.source)
        with self.assertRaisesRegex(MigrationError, "unknown"):
            self.run_migration()
        self.assert_target_empty()

    def test_known_legacy_source_trigger_is_admitted_as_readonly_metadata(self):
        with closing(sqlite3.connect(self.source)) as raw, raw:
            raw.execute("CREATE TRIGGER guard_consumption_history_update BEFORE UPDATE ON part_consumptions BEGIN SELECT RAISE(ABORT,'immutable legacy consumption'); END")
        self.expected = snapshot_sha256(self.source)
        self.run_migration()
        self.assertEqual(snapshot_sha256(self.source), self.expected)

    def test_missing_target_guard_fails_before_any_copy(self):
        with self.db.transaction() as c:
            c.execute("DROP TRIGGER guard_approval_evidence_insert")
        with self.assertRaises(SchemaMismatchError):
            self.run_migration()
        self.assert_target_empty(guards=False)

    def test_unknown_target_trigger_is_rejected_before_any_copy(self):
        with self.db.transaction() as c:
            c.execute("CREATE TRIGGER surprise AFTER INSERT ON legacy_records BEGIN SELECT 1; END")
        with self.assertRaisesRegex(MigrationError, "unknown"):
            self.run_migration()
        self.assert_target_empty()

    def test_session_epoch_overflow_is_rejected_and_rolled_back(self):
        with closing(sqlite3.connect(self.source)) as raw, raw:
            raw.execute("UPDATE users SET session_epoch=9223372036854775807")
        self.expected = snapshot_sha256(self.source)
        with self.assertRaisesRegex(MigrationError, "epoch"):
            self.run_migration()
        self.assert_target_empty()

    def test_hard_link_to_source_is_rejected_without_writing(self):
        alias = self.root / "alias.db"
        try:
            alias.hardlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("This fixture filesystem cannot create hard links.")
        same = Database(DatabaseConfig(provider="sqlite", database=str(alias), development=True))
        with self.assertRaisesRegex(MigrationError, "different"):
            migrate_snapshot(self.source, same, expected_sha256=self.expected)
        self.assertEqual(snapshot_sha256(self.source), self.expected)

    def test_non_numeric_numeric_column_fails_closed(self):
        with closing(sqlite3.connect(self.source)) as raw, raw:
            raw.execute("PRAGMA ignore_check_constraints=ON")
            raw.execute("UPDATE approvals SET coefficient='not-a-number'")
        self.expected = snapshot_sha256(self.source)
        with self.assertRaisesRegex(MigrationError, "NUMERIC"):
            self.run_migration()
        self.assert_target_empty()

    def test_exact_int64_units_do_not_pass_through_float(self):
        exact = 9223372036854775806
        with closing(sqlite3.connect(self.source)) as raw, raw:
            raw.execute("UPDATE part_consumption_totals SET quantity_units=?", (exact,))
        self.expected = snapshot_sha256(self.source)
        self.run_migration()
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT quantity_units FROM part_consumption_totals"), exact)


if __name__ == "__main__":
    unittest.main()
