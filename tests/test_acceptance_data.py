"""Real guarded disposable copies; never read the user's actual company data."""
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from pak_maintenance.database import Database, DatabaseConfig
from pak_maintenance.migration import snapshot_sha256, verify_schema8_snapshot_target
from test_migration import create_database, fixture_directory, SCHEMA_DIRECTORY

_SPEC = importlib.util.spec_from_file_location("prepare_acceptance_data", Path(__file__).resolve().parents[1] / "tools/prepare_acceptance_data.py")
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)


class AcceptanceDataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_directory()
        self.root = self.fixture.__enter__()
        self.backups, self.seed = self.root / "backups", self.root / "seed"
        self.backups.mkdir()
        self.seed.mkdir()
        self.originals = self.seed / "pm-originals"
        self.originals.mkdir()
        self.source = self.backups / "pak-auto-20261015T113218011Z-fixture.db"
        create_database(self.source, populated=True)
        original = b"Synthetic original document bytes; only a disposable test."
        self.original_digest = hashlib.sha256(original).hexdigest()
        self.original_path = self.originals / (self.original_digest + ".docx")
        self.original_path.write_bytes(original)
        form = dict(id="template", title="Synthetic PM", source=dict(path="C:\\source\\test.docx", sha256=self.original_digest), tables=[])
        self.import_path = self.seed / "pm-import.json"
        self.import_path.write_text(json.dumps(dict(templates=[form])), encoding="utf-8")
        # These files must never be copied into the acceptance dataset.
        (self.seed / "master-data.json").write_text('"sentinel-master-data"')
        (self.seed / "first-login.txt").write_text("sentinel-actual-credential")
        with closing(sqlite3.connect(self.source)) as connection, connection:
            body = json.dumps(form)
            connection.execute("INSERT INTO pm_templates VALUES('template','Synthetic PM','مکانیک','فرایند','ماهانه',1,'pending',?)", (body,))
            connection.execute("INSERT INTO pm_template_revisions VALUES('template',1,?,'import','head','2026-10-15')", (body,))
            connection.execute("INSERT INTO pm_weekly_plans VALUES('plan','template',NULL,NULL,'مکانیک','فرایند',1,0,0,'2026-09-23',16866,'2026-09-23','{}')")
        self.write_manifest()
        self.destination = self.root / "acceptance-inputs"
        self.evidence_path = self.root / "evidence.json"

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def write_manifest(self):
        manifest = dict(application="PakMaintenance", kind="sqlite-online-snapshot", file=self.source.name,
            bytes=self.source.stat().st_size, sha256=snapshot_sha256(self.source), integrityChecked=True, createdAt="2026-10-15T11:32:18+00:00")
        Path(str(self.source) + ".json").write_text(json.dumps(manifest))

    def prepare(self):
        return acceptance.prepare(self.backups, self.seed, self.destination, schema_directory=SCHEMA_DIRECTORY, evidence_path=self.evidence_path)

    def test_guarded_copy_preserves_legacy_27_tables_and_initializes_two_empty_new_tables(self):
        before = snapshot_sha256(self.source)
        source_bytes, original_bytes = self.source.read_bytes(), self.original_path.read_bytes()
        result = self.prepare()
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(snapshot_sha256(self.source), before)
        self.assertFalse(Path(str(self.source) + "-wal").exists())
        self.assertEqual(result["reconciledTables"], 30)
        self.assertEqual(result["schemaVersion"], 8)
        self.assertEqual(result["sourceSchemaVersion"], 6)
        self.assertTrue(result["sourceUnchanged"])
        self.assertEqual(result["copiedOriginalDocuments"], 1)
        self.assertEqual(result["inactivePlansPreserved"], 1)
        self.assertEqual(result["unconfirmedPlansPreserved"], 1)
        self.assertEqual(result["authentication"]["passwordHashesRemoved"], 3)
        self.assertEqual(result["authentication"]["discardedSessions"], 1)
        target = self.destination / "data/acceptance.db"
        database = Database(DatabaseConfig(provider="sqlite", database=str(target), development=True))
        verify_schema8_snapshot_target(database, require_guards=True)
        with database.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM users WHERE password_hash=:marker", {"marker": acceptance.PASSWORD_MARKER}), 3)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM sessions"), 0)
            self.assertEqual(connection.scalar("SELECT session_epoch FROM users WHERE id='person'"), 5)
            self.assertEqual(connection.scalar("SELECT minutes FROM participants"), 60)
            self.assertEqual(connection.scalar("SELECT quantity_units FROM part_consumptions"), 123456)
            self.assertEqual(connection.scalar("SELECT score_raw FROM completed_scores"), "3.125")
            self.assertEqual(connection.scalar("SELECT active FROM pm_weekly_plans"), 0)
            self.assertEqual(connection.scalar("SELECT mapping_confirmed FROM pm_weekly_plans"), 0)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM participant_head_scores"), 0)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM pm_change_requests"), 0)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM completed_scores WHERE head_coefficient_exact IS NOT NULL"), 0)
        target_bytes = target.read_bytes()
        self.assertNotIn(b"legacy-pbkdf-hash", target_bytes)
        self.assertNotIn(b"legacy-supervisor-hash", target_bytes)
        self.assertNotIn(b"legacy-head-hash", target_bytes)
        copied = self.destination / "seed/pm-originals" / self.original_path.name
        self.assertEqual(copied.read_bytes(), original_bytes)
        self.assertEqual(self.original_path.read_bytes(), original_bytes)
        self.assertEqual({p.name for p in (self.destination / "seed").iterdir()}, {"pm-import.json", "pm-originals"})
        manifest = json.loads((self.destination / "acceptance-data.json").read_text())
        self.assertEqual(manifest["databaseSha256"], hashlib.sha256(target_bytes).hexdigest())
        self.assertEqual(len(manifest["tableCounts"]), 30)
        self.assertEqual(len(manifest["seedFiles"]), 2)
        evidence_text = self.evidence_path.read_text()
        self.assertNotIn("legacy-pbkdf-hash", evidence_text)
        self.assertNotIn("sentinel-actual-credential", evidence_text)
        self.assertNotIn("003", evidence_text)

    def test_bad_newest_manifest_rejects_without_falling_back_or_creating_output(self):
        latest = self.backups / "pak-auto-20990101T000000000Z-bad.db"
        latest.write_bytes(self.source.read_bytes())
        Path(str(latest) + ".json").write_text(json.dumps(dict(application="PakMaintenance", kind="sqlite-online-snapshot", file=latest.name, integrityChecked=True, bytes=latest.stat().st_size, sha256="0"*64)))
        with self.assertRaisesRegex(acceptance.AcceptanceDataError, "SHA-256"):
            self.prepare()
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.evidence_path.exists())

    def test_auto_and_pre_release_backups_are_ordered_by_utc_timestamp(self):
        older = self.backups / "pak-before-1.9-20261001T000000Z-old.db"
        older.write_bytes(self.source.read_bytes())
        # The old release backup has no manifest; it must never supersede the
        # newer auto backup just because 'before' sorts after 'auto'.
        source, digest, manifest = acceptance.verify_backup(self.backups)
        self.assertEqual(source, self.source)
        self.assertEqual(digest, snapshot_sha256(self.source))

    def test_missing_or_tampered_original_is_rejected_and_no_copy_is_published(self):
        self.original_path.write_bytes(b"tampered")
        with self.assertRaisesRegex(acceptance.AcceptanceDataError, "declared bytes"):
            self.prepare()
        self.assertFalse(self.destination.exists())
        self.original_path.unlink()
        with self.assertRaisesRegex(acceptance.AcceptanceDataError, "missing"):
            self.prepare()
        self.assertFalse(self.destination.exists())

    def test_invalid_extension_and_unknown_original_file_are_rejected(self):
        document = json.loads(self.import_path.read_text())
        document["templates"][0]["source"]["path"] = "malware.exe"
        self.import_path.write_text(json.dumps(document))
        with self.assertRaisesRegex(acceptance.AcceptanceDataError, "extension"):
            self.prepare()
        document["templates"][0]["source"]["path"] = "test.docx"
        self.import_path.write_text(json.dumps(document))
        (self.originals / "first-login.txt").write_text("must-not-copy")
        with self.assertRaisesRegex(acceptance.AcceptanceDataError, "unexpected"):
            self.prepare()
        self.assertFalse(self.destination.exists())

    def test_live_sidecars_and_live_directory_are_rejected(self):
        Path(str(self.source) + "-wal").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "sidecars"):
            self.prepare()
        with self.assertRaisesRegex(acceptance.AcceptanceDataError, "live data"):
            acceptance.verify_backup(self.root / "data")

    def test_existing_destination_is_never_modified(self):
        self.destination.mkdir()
        sentinel = self.destination / "sentinel"
        sentinel.write_bytes(b"existing-copy")
        with self.assertRaisesRegex(acceptance.AcceptanceDataError, "new and separate"):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"existing-copy")

    def test_source_input_change_during_copy_fails_closed_and_cleans_owned_staging(self):
        original_redaction = acceptance._redact_passwords
        def alter_input_after_migration(target):
            original_redaction(target)
            self.import_path.write_text('{"templates":[]}')
        with patch.object(acceptance, "_redact_passwords", side_effect=alter_input_after_migration):
            with self.assertRaisesRegex(acceptance.AcceptanceDataError, "changed"):
                self.prepare()
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.evidence_path.exists())
        self.assertEqual(list(self.root.glob(".ap-*")), [])


if __name__ == "__main__":
    unittest.main()
