"""Offline release reset over synthetic, frozen snapshots; no company files."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.database import Database, DatabaseConfig, Connection
from pak_maintenance.maintenance_reset import EMPTY_TABLES, PASSWORD_MARKER, prepare_reset_copy
from pak_maintenance.migration import snapshot_sha256
from test_migration import create_database

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceResetTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "tests/.reset-fixtures" / uuid4().hex
        self.directory.mkdir(parents=True)
        self.source = self.directory / "frozen.db"
        self.target = self.directory / "clean.db"
        create_database(self.source, populated=True)
        self.body = json.dumps({"id": "form", "title": "فرم ساختگی", "period": "ماهانه", "activities": ["کنترل نمونه"]}, ensure_ascii=False)
        plan = json.dumps({"id": "plan-form", "version": 1, "active": True, "mappingConfirmed": True, "startDate": "2026-09-23", "slots": [{"month": month, "week": 1} for month in range(1, 13)], "approvedAt": "2026-09-24T00:00:00Z", "activatedAt": "2026-09-24T00:00:00Z", "effectiveFromUtc": "2026-09-24T00:00:00Z", "issueFromDate": "2026-09-23", "setupRequestKey": "old-request-key"}, ensure_ascii=False)
        with closing(sqlite3.connect(self.source)) as c, c:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("INSERT INTO pm_templates VALUES('form','فرم ساختگی','مکانیک','فرایند','ماهانه',1,'confirmed',?)", (self.body,))
            c.execute("INSERT INTO pm_template_revisions VALUES('form',1,?,'original','head','2026-09-23')", (self.body,))
            c.execute("INSERT INTO pm_weekly_plans VALUES('plan-form','form','asset','supervisor','مکانیک','فرایند',1,1,1,'2026-09-23',140511,'2027-01-01',?)", (plan,))
            c.execute("INSERT INTO pm_plan_revisions VALUES('plan-form',1,?,'original','head','2026-09-23')", (plan,))
            c.execute("INSERT INTO plans VALUES('legacy-plan','asset','2026-10-10',30,1,2,'{\"title\":\"legacy\",\"active\":true}')")
            c.execute("INSERT INTO audit VALUES('local-test-credentials-v1','head','fixture','initialize-local-test','local-test','credentials-v1','{}','2026-09-23')")
            c.execute("INSERT INTO audit VALUES('config-audit','head','fixture','edit','asset','asset','{}','2026-09-23')")
        self.expected = snapshot_sha256(self.source)

    def tearDown(self):
        if self.directory.resolve().parent != (ROOT / "tests/.reset-fixtures").resolve():
            raise RuntimeError("Unexpected fixture directory")
        shutil.rmtree(self.directory)

    def reset(self, **extra):
        return prepare_reset_copy(self.source, self.target, expected_sha256=self.expected, schema_directory=ROOT / "schema", operator_code="003", **extra)

    def test_preserves_forms_schedules_passwords_and_checkpoint_but_clears_all_work(self):
        report = self.reset()
        self.assertTrue(report["sourceUnchanged"])
        self.assertEqual(snapshot_sha256(self.source), self.expected)
        database = Database(DatabaseConfig("sqlite", str(self.target), development=True))
        database.verify_schema()
        with database.connect() as c:
            for table in EMPTY_TABLES:
                self.assertEqual(c.scalar("SELECT COUNT(*) FROM " + table), 0, table)
            self.assertEqual(c.scalar("SELECT body FROM pm_templates WHERE id='form'"), self.body)
            self.assertEqual(c.scalar("SELECT body FROM pm_template_revisions WHERE template_id='form'"), self.body)
            plan = c.one("SELECT * FROM pm_weekly_plans")
            body = json.loads(plan["body"])
            self.assertEqual((plan["active"], plan["version"], plan["cursor_month"], plan["next_scan"]), (0, 2, 140507, "2026-09-23"))
            self.assertEqual(len(body["slots"]), 12)
            self.assertTrue(body["mappingConfirmed"])
            for key in ("activatedAt", "approvedAt", "effectiveFromUtc", "issueFromDate", "setupRequestKey"):
                self.assertNotIn(key, body)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM pm_plan_revisions"), 2)
            self.assertEqual(c.one("SELECT active,version FROM plans"), {"active": 0, "version": 3})
            self.assertEqual(c.scalar("SELECT password_hash FROM users WHERE id='person'"), "legacy-pbkdf-hash")
            self.assertEqual(c.scalar("SELECT session_epoch FROM users WHERE id='person'"), 5)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE id='local-test-credentials-v1'"), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE entity='order'"), 0)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE id='config-audit'"), 1)
        self.assertTrue(self.target.with_suffix(".db.reset.json").is_file())

    def test_distribution_copy_redacts_credentials_without_stranding_first_setup(self):
        self.reset(redact_credentials=True)
        with closing(sqlite3.connect(self.target)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users WHERE password_hash=?", (PASSWORD_MARKER,)).fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE id='local-test-credentials-v1'").fetchone()[0], 0)
        data = self.target.read_bytes()
        for secret in (b"legacy-pbkdf-hash", b"legacy-supervisor-hash", b"legacy-head-hash"):
            self.assertNotIn(secret, data)

    def test_bad_backup_hash_or_existing_target_never_overwrites(self):
        with self.assertRaisesRegex(ValueError, "hash"):
            prepare_reset_copy(self.source, self.target, expected_sha256="0" * 64, schema_directory=ROOT / "schema", operator_code="003")
        self.assertFalse(self.target.exists())
        self.target.write_bytes(b"preserve existing target")
        before = self.target.read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrites"):
            self.reset()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(snapshot_sha256(self.source), self.expected)

    def test_failure_rolls_back_configuration_copy_and_never_touches_source(self):
        original = Connection.execute
        def fail(connection, sql, params=None):
            if sql.startswith("INSERT INTO audit"):
                raise RuntimeError("Synthetic destination failure")
            return original(connection, sql, params)
        with patch.object(Connection, "execute", fail), self.assertRaisesRegex(RuntimeError, "Synthetic"):
            self.reset()
        with closing(sqlite3.connect(self.target)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(snapshot_sha256(self.source), self.expected)
