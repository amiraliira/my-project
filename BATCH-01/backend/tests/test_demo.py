"""The legacy command uses the current PG-only launcher with no side effects."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from pak_maintenance import demo, local_test

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / "tests/.demo-fixtures"


class DemoCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.root = FIXTURES / uuid4().hex
        self.root.mkdir(mode=0o755, parents=True)

    def tearDown(self):
        if self.root.resolve().parent != FIXTURES.resolve() or self.root.is_symlink():
            raise RuntimeError("Demo compatibility fixture escaped its test root")
        shutil.rmtree(self.root)
        if FIXTURES.exists() and not any(FIXTURES.iterdir()):
            FIXTURES.rmdir()

    def test_old_entrypoint_help_exposes_current_noninteractive_browser_launcher(self):
        self.assertIs(demo.main, local_test.main)
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as finished:
            demo.main(["--help"])
        self.assertEqual(finished.exception.code, 0)
        self.assertIn("--no-browser", output.getvalue())
        self.assertIn("--data-directory", output.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_legacy_file_database_payload_is_rejected_without_password_prompt_or_database_creation(self):
        frontend = self.root / "frontend"; frontend.mkdir()
        (frontend / "index.html").write_text("<html>synthetic unchanged UI</html>", encoding="ascii")
        payload = self.root / "test-payload"; payload.mkdir()
        original = payload / "old.db"; original.write_bytes(b"synthetic legacy source never opened")
        manifest = {"databaseProvider": "sqlite", "databaseFormat": "sqlite", "schemaVersion": 7,
            "databaseFile": "old.db", "databaseSha256": local_test.digest(original)}
        (payload / "acceptance-data.json").write_text(json.dumps(manifest), encoding="ascii")
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(demo.main(["--root", str(self.root), "--no-browser"]), 1)
        self.assertEqual(original.read_bytes(), b"synthetic legacy source never opened")
        self.assertFalse((self.root / "demo-data").exists())
        self.assertFalse((self.root / "test-data/acceptance.db").exists())
        self.assertFalse((self.root / "test-data/postgresql").exists())
        self.assertNotIn("password:", output.getvalue().lower())
        self.assertNotIn("Traceback", output.getvalue())

    def test_incomplete_package_is_rejected_without_fabricated_data(self):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(demo.main(["--root", str(self.root), "--no-browser"]), 1)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
