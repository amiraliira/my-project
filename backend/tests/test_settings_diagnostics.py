"""Configuration provenance and secret-safe operator diagnostics, without DB I/O."""
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).absolute().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import settings as compatibility_settings
from pak_maintenance import settings


class SettingsDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="settings-diagnostics-", dir=BACKEND / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        seed = self.directory / "seed"
        (seed / "pm-originals").mkdir(parents=True)
        (seed / "pm-import.json").write_text("{}", encoding="utf-8")
        self.secret = r"SyntheticOnly!:$()back\slash=and spaces"
        self.values = {
            "PAK_PUBLIC_URL": "http://127.0.0.1:5187",
            "PAK_ALLOW_INSECURE_HTTP": "true",
            "PAK_DB_NAME": "pak_diagnostics_test",
            "PAK_DB_USER": "pak",
            "PAK_DB_PASSWORD": self.secret,
            "PAK_DB_SSLMODE": "disable",
            "PAK_DB_ALLOW_LOOPBACK_PLAINTEXT": "true",
            "PAK_PM_SEED_DIR": str(seed),
            "PAK_BACKUP_DIR": str(self.directory / "backups"),
        }
        self.private = self.directory / "backend.env"
        self.write()

    def write(self, *, omit=()):
        self.private.write_text("\n".join(
            key + '=\"' + value + '\"' for key, value in self.values.items() if key not in omit
        ) + "\n", encoding="utf-8")

    def run_preflight(self, **changes):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PAK_")}
        environment.update(PAK_ENV_FILE=str(self.private), PYTHONUTF8="1", **changes)
        return subprocess.run([sys.executable, "-B", str(BACKEND / "tools/check_installation.py"), "--config"],
            env=environment, cwd=BACKEND, capture_output=True, text=True, timeout=30)

    def test_compatibility_entry_reexports_same_implementation(self):
        self.assertIs(compatibility_settings.load_config, settings.load_config)
        self.assertIs(compatibility_settings.read_env_file, settings.read_env_file)
        self.assertIs(compatibility_settings.Path, settings.Path)
        self.assertEqual(settings.BACKEND_DIRECTORY, BACKEND)

    def test_domain_settings_do_not_depend_on_the_asgi_adapter(self):
        result = subprocess.run([sys.executable, "-B", "-c",
            "import sys; import pak_maintenance.settings; assert 'app' not in sys.modules"],
            cwd=BACKEND, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_reports_path_and_override_names_without_values(self):
        environment = {"PAK_ENV_FILE": str(self.private), "PAK_DB_USER": "admin", "PAK_DB_PASSWORD": self.secret}
        original = dict(environment)
        source = settings.configuration_source(environment)
        self.assertEqual(source.path, str(self.private))
        self.assertTrue(source.explicit)
        self.assertTrue(source.file_exists)
        self.assertEqual(source.environment_settings, ("PAK_DB_PASSWORD", "PAK_DB_USER"))
        self.assertNotIn(self.secret, repr(source))
        self.assertNotIn("admin", repr(source))
        self.assertEqual(environment, original)

    def test_default_location_and_environment_only_mode_remain_supported(self):
        source = settings.configuration_source({}, backend_directory=self.directory)
        self.assertEqual(source.path, str(self.directory / ".env"))
        self.assertFalse(source.explicit)
        self.assertFalse(source.file_exists)
        config = settings.load_config(self.values, backend_directory=self.directory)
        self.assertEqual(config.database.password, self.secret)
        self.assertFalse((self.directory / "backups").exists())

    def test_literal_password_and_environment_precedence_remain_unchanged(self):
        exported = self.secret + "_exported"
        config = settings.load_config({"PAK_ENV_FILE": str(self.private), "PAK_DB_PASSWORD": exported})
        self.assertEqual(config.database.password, exported)
        self.assertEqual(settings.read_env_file(self.private)["PAK_DB_PASSWORD"], self.secret)
        self.assertFalse((self.directory / "backups").exists())

    def test_preflight_shows_effective_database_login_and_override_names(self):
        result = self.run_preflight(PAK_DB_USER="admin", PAK_DB_PASSWORD=self.secret + "_exported")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Configuration file (PAK_ENV_FILE): " + str(self.private), result.stdout)
        self.assertIn("Process environment settings (override file): PAK_DB_PASSWORD, PAK_DB_USER", result.stdout)
        self.assertIn("Configured PostgreSQL login: admin", result.stdout)
        self.assertIn("not by this local preflight", result.stdout)
        self.assertIn("no database connection attempted", result.stdout)
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_missing_database_name_is_named_without_password_disclosure(self):
        self.write(omit=("PAK_DB_NAME",))
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Set PAK_DB_NAME", result.stderr)
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_parser_error_reports_line_without_private_contents(self):
        self.private.write_text("PAK_DB_PASSWORD=" + self.secret + "\nUNKNOWN=" + self.secret, encoding="utf-8")
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Invalid PAK environment setting at line 2", result.stderr)
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_missing_selected_file_has_safe_actionable_error(self):
        self.private.unlink()
        result = self.run_preflight()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Cannot read the selected configuration file", result.stderr)
        self.assertIn("PAK_ENV_FILE", result.stderr)

    def test_invalid_source_path_cannot_inject_extra_diagnostic_lines(self):
        with self.assertRaises(settings.SettingsError) as raised:
            settings.configuration_source({"PAK_ENV_FILE": str(self.private) + "\n" + self.secret})
        self.assertIn("PAK_ENV_FILE", str(raised.exception))
        self.assertNotIn(self.secret, str(raised.exception))

    def test_unexpected_validation_error_does_not_echo_private_input(self):
        spec = spec_from_file_location("pak_preflight_under_test", BACKEND / "tools/check_installation.py")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        environment = {"PAK_ENV_FILE": str(self.private)}
        stream = StringIO()
        with patch.dict(os.environ, environment, clear=True), redirect_stdout(stream):
            with patch("pak_maintenance.settings.load_config", side_effect=ValueError(self.secret)):
                errors = module.check(config=True)
        self.assertTrue(errors)
        self.assertNotIn(self.secret, "\n".join(errors) + stream.getvalue())


if __name__ == "__main__":
    unittest.main()
