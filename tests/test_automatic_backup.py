"""Opt-in local test backups: no server, company database, pruning or host jobs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.backup import BackupService
from pak_maintenance.database import Database, DatabaseConfig


class AutomaticBackupTests(unittest.TestCase):
    def setUp(self):
        self.fixture_root = Path(__file__).resolve().parent / ".backup-fixtures"
        self.directory = self.fixture_root / uuid4().hex
        self.directory.mkdir(parents=True, mode=0o755)
        self.source = self.directory / "synthetic-only.db"
        with closing(sqlite3.connect(self.source)) as connection:
            connection.executescript("CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id)); INSERT INTO parent VALUES(1),(2); INSERT INTO child VALUES(1,1),(2,2),(3,1);")
        self.db = Database(DatabaseConfig("sqlite", str(self.source), development=True))
        self.backup_directory = self.directory / "backups"
        self.service = BackupService(self.db, self.backup_directory, automatic_enabled=True)

    def tearDown(self):
        self.assertTrue(self.service.stop_automatic())
        resolved = self.directory.resolve()
        self.assertEqual(resolved.parent, self.fixture_root.resolve())
        shutil.rmtree(resolved)
        if self.fixture_root.exists() and not any(self.fixture_root.iterdir()):
            self.fixture_root.rmdir()

    def make_due(self):
        registry = json.loads(self.service._registry_path.read_text(encoding="utf-8"))
        registry["lastCompletedAt"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.service._registry_path.write_text(json.dumps(registry), encoding="utf-8")

    def damage_snapshot(self, snapshot, marker):
        # Simulate an external actor replacing a registered snapshot. Keeping
        # its byte count forces integrity checks to detect the changed hash,
        # rather than accepting/rejecting it solely on size. Stage a separate
        # file so this does not depend on platform sharing of SQLite handles.
        original = snapshot.path.read_bytes()
        self.assertGreater(len(original), len(marker))
        damaged = marker + original[len(marker):]
        self.assertNotEqual(damaged, original)
        staged = snapshot.path.with_name("damage-" + uuid4().hex + ".db")
        try:
            staged.write_bytes(damaged)
            staged.replace(snapshot.path)
        finally:
            if staged.exists():
                staged.unlink()
        self.assertEqual(snapshot.path.stat().st_size, len(original))
        self.assertEqual(snapshot.path.read_bytes(), damaged)
        return damaged

    def test_snapshot_keeps_source_bytes_and_rows_and_publishes_verified_copy(self):
        original = self.source.read_bytes()
        snapshot = self.service.run_automatic_if_due()
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(snapshot.sha256, hashlib.sha256(snapshot.path.read_bytes()).hexdigest())
        self.assertEqual(snapshot.size, snapshot.path.stat().st_size)
        with closing(sqlite3.connect(snapshot.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM parent").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM child").fetchone()[0], 3)
        self.assertEqual(list(self.backup_directory.glob("*.partial")), [])
        status = self.service.status()
        self.assertTrue(status["enabled"])
        self.assertFalse(status["running"])
        self.assertIsNone(status["lastError"])
        self.assertEqual(datetime.fromisoformat(status["nextDueAt"]) - datetime.fromisoformat(status["lastCompletedAt"]), timedelta(hours=24))

    def test_due_tick_creates_once_and_retention_only_prunes_own_verified_automatic_files(self):
        self.service = BackupService(self.db, self.backup_directory, automatic_enabled=True, retention_count=1)
        manual = self.service.create_snapshot()
        unknown = self.backup_directory / "unknown.db"
        unknown.write_bytes(b"Unregistered file must be kept")
        first = self.service.run_automatic_if_due()
        self.assertIsNone(self.service.run_automatic_if_due())
        self.make_due()
        second = self.service.run_automatic_if_due()
        self.assertNotEqual(first.path, second.path)
        self.assertFalse(first.path.exists())
        self.assertTrue(manual.path.is_file())
        self.assertEqual(unknown.read_bytes(), b"Unregistered file must be kept")
        self.assertEqual(self.service.status()["retentionCount"], 1)
        self.assertEqual(self.service.status()["registeredAutomaticSnapshots"], 1)
        self.assertIsNone(self.service.status()["lastCleanupError"])

    def test_concurrent_due_ticks_publish_only_one_snapshot(self):
        barrier = threading.Barrier(8)
        def tick(_):
            barrier.wait(timeout=5)
            return self.service.run_automatic_if_due()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(tick, range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(list(self.backup_directory.glob("*.db"))), 1)

    def test_manual_snapshot_is_separate_from_automatic_registry(self):
        snapshot = self.service.create_snapshot()
        self.assertEqual(self.service.status()["lastManualCompletedAt"], snapshot.created_at.isoformat())
        self.assertIsNone(self.service.status()["lastCompletedAt"])
        self.assertFalse(self.service._registry_path.exists())
        self.assertIsNotNone(self.service.run_automatic_if_due())
        registry = json.loads(self.service._registry_path.read_text(encoding="utf-8"))
        self.assertNotIn(snapshot.path.name, [entry["file"] for entry in registry["snapshots"]])

    def test_busy_copy_is_not_reported_as_failure_and_no_queue_accumulates(self):
        with self.service._lock:
            self.assertIsNone(self.service.run_automatic_if_due())
        self.assertIsNone(self.service.status()["lastError"])
        self.assertEqual(list(self.backup_directory.glob("*.db")), [])

    def test_error_status_is_safe_and_later_success_clears_it(self):
        with patch("pak_maintenance.backup.validate_file_path", side_effect=PermissionError("password=private; source-company-data")):
            with self.assertRaises(PermissionError):
                self.service.run_automatic_if_due()
        status = self.service.status()
        self.assertFalse(status["running"])
        self.assertNotIn("private", status["lastError"])
        self.assertIsNone(status["lastCompletedAt"])
        self.assertIsNotNone(self.service.run_automatic_if_due())
        self.assertIsNone(self.service.status()["lastError"])

    def test_worker_start_is_idempotent_and_shutdown_interrupts_startup_wait(self):
        self.assertTrue(self.service.start_automatic())
        self.assertFalse(self.service.start_automatic())
        self.assertTrue(self.service.status()["workerRunning"])
        self.assertTrue(self.service.stop_automatic(timeout=1))
        self.assertFalse(self.service.status()["workerRunning"])
        self.assertFalse(self.backup_directory.exists())

    def test_defaults_disable_automatic_work_and_postgres_is_not_file_copied(self):
        disabled = BackupService(self.db, self.backup_directory)
        self.assertFalse(disabled.start_automatic())
        self.assertIsNone(disabled.run_automatic_if_due())
        self.assertFalse(self.backup_directory.exists())
        for provider, development in (("postgresql", True), ("sqlite", False)):
            database = SimpleNamespace(config=SimpleNamespace(provider=provider, development=development))
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                BackupService(database, self.backup_directory, automatic_enabled=True)
        for options in ({"automatic_enabled": 1}, {"interval_hours": 0}, {"interval_hours": float("nan")}, {"interval_hours": True}, {"retention_count": 0}, {"retention_count": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                BackupService(self.db, self.backup_directory, **options)

    def test_restart_restores_registry_times_and_does_not_duplicate_recent_backup(self):
        self.service.run_automatic_if_due()
        previous = self.service.status()
        restarted = BackupService(self.db, self.backup_directory, automatic_enabled=True)
        self.assertEqual(restarted.status()["lastCompletedAt"], previous["lastCompletedAt"])
        self.assertEqual(restarted.status()["nextDueAt"], previous["nextDueAt"])
        self.assertIsNone(restarted.run_automatic_if_due())
        self.assertEqual(len(list(self.backup_directory.glob("*.db"))), 1)

    def test_damaged_latest_snapshot_does_not_delay_replacement_until_next_day(self):
        first = self.service.run_automatic_if_due()
        damaged = self.damage_snapshot(first, b"damaged")
        second = self.service.run_automatic_if_due()
        self.assertIsNotNone(second)
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.path.read_bytes(), damaged)

    def test_modified_registered_backup_is_preserved_and_cleanup_failure_survives_restart(self):
        self.service = BackupService(self.db, self.backup_directory, automatic_enabled=True, retention_count=1)
        first = self.service.run_automatic_if_due()
        damaged = self.damage_snapshot(first, b"Modified file must be kept")
        self.make_due()
        second = self.service.run_automatic_if_due()
        self.assertTrue(second.path.is_file())
        self.assertEqual(first.path.read_bytes(), damaged)
        status = self.service.status()
        self.assertEqual(status["registeredAutomaticSnapshots"], 2)
        self.assertIsNotNone(status["lastCleanupError"])
        self.assertEqual(status["lastError"], status["lastCleanupError"])
        restarted = BackupService(self.db, self.backup_directory, automatic_enabled=True, retention_count=1)
        self.assertEqual(restarted.status()["lastCleanupError"], status["lastCleanupError"])

    def test_malformed_registry_never_deletes_outside_or_unknown_files(self):
        first = self.service.run_automatic_if_due()
        outside = self.directory / "keep-outside.db"
        outside.write_bytes(b"outside")
        registry = json.loads(self.service._registry_path.read_text(encoding="utf-8"))
        registry["snapshots"][0]["file"] = "../keep-outside.db"
        self.service._registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.run_automatic_if_due()
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertTrue(first.path.is_file())
        self.assertIsNotNone(self.service.status()["lastError"])

    def test_independent_services_share_nonblocking_registry_lock(self):
        other = BackupService(self.db, self.backup_directory, automatic_enabled=True)
        with self.service._registry_exclusive():
            self.assertIsNone(other.run_automatic_if_due())
        self.assertEqual(list(self.backup_directory.glob("*.db")), [])
        self.assertIsNotNone(other.run_automatic_if_due())


if __name__ == "__main__":
    unittest.main()
