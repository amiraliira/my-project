"""Isolated source-selection regressions; no settings or database connection."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BACKEND = Path(__file__).absolute().parents[1]


class ManualBootstrapCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bootstrap-", dir=BACKEND / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "app").mkdir()
        (self.root / "src/pak_maintenance").mkdir(parents=True)
        shutil.copyfile(BACKEND / "app/__init__.py", self.root / "app/__init__.py")
        shutil.copyfile(BACKEND / "src/pak_maintenance/__init__.py", self.root / "src/pak_maintenance/__init__.py")

    def run_python(self, code):
        return subprocess.run([sys.executable, "-B", "-c", code], cwd=self.root,
                              capture_output=True, text=True, timeout=20)

    def test_delivered_source_import_needs_no_settings_or_database(self):
        result = self.run_python("import app, pak_maintenance, sys; "
            "assert pak_maintenance.__version__ == '2.0.0rc12'; "
            "assert 'app.settings' not in sys.modules; "
            "assert 'pak_maintenance.database' not in sys.modules; "
            "print(pak_maintenance.__file__)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.root / "src/pak_maintenance/__init__.py"), result.stdout)

    def test_missing_init_has_actionable_failure_before_domain_import(self):
        (self.root / "src/pak_maintenance/__init__.py").unlink()
        result = self.run_python("import app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete PAK backend", result.stderr)
        self.assertIn("Do not create an empty", result.stderr)

    def test_preloaded_foreign_package_is_rejected(self):
        result = self.run_python("import types, sys; "
            "sys.modules['pak_maintenance'] = types.ModuleType('pak_maintenance'); import app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loaded from another location", result.stderr)

    def test_empty_init_does_not_masquerade_as_complete_source(self):
        (self.root / "src/pak_maintenance/__init__.py").write_text("", encoding="utf-8")
        result = self.run_python("import app")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete PAK package version", result.stderr)


if __name__ == "__main__":
    unittest.main()
