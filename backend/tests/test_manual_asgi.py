"""Settings and conventional ASGI entry point; no network or database fixtures."""
from importlib import import_module
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).absolute().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import settings


class ManualSettingsCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pak-manual-settings-", dir=BACKEND / "tests")
        self.directory = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def values(self, **changes):
        result = {
            "PAK_PUBLIC_URL": "https://192.168.12.92",
            "PAK_DB_NAME": "pak_manual_test",
            "PAK_DB_USER": "admin",
            "PAK_DB_PASSWORD": "Synthetic!Literal:$#Only",
            "PAK_DB_SSLMODE": "disable",
            "PAK_DB_ALLOW_LOOPBACK_PLAINTEXT": "true",
            "PAK_PM_SEED_DIR": str(self.directory / "seed"),
            "PAK_BACKUP_DIR": str(self.directory / "backups"),
        }
        result.update(changes)
        return result

    def write(self, content, *, name=".env"):
        path = self.directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_literal_password_punctuation_has_no_expansion(self):
        literal = r"Synthetic!:#$()back\slash=and spaces"
        path = self.write('# comment\nPAK_DB_PASSWORD="' + literal + '"\n')
        self.assertEqual(settings.read_env_file(path), {"PAK_DB_PASSWORD": literal})
        self.assertFalse((self.directory / "unexpected").exists())

    def test_unquoted_hash_and_equal_are_literal(self):
        path = self.write("PAK_DB_PASSWORD=alpha#beta=gamma\n")
        self.assertEqual(settings.read_env_file(path)["PAK_DB_PASSWORD"], "alpha#beta=gamma")

    def test_invalid_line_duplicate_and_unclosed_quote_do_not_disclose_values(self):
        for content in (
            "UNKNOWN=SyntheticPrivateValue\n",
            "PAK_DB_PASSWORD=SyntheticPrivateValue\nPAK_DB_PASSWORD=second\n",
            'PAK_DB_PASSWORD="SyntheticPrivateValue\n',
            "PAK_DB_PASSWORD=SyntheticPrivateValue\x00\n",
        ):
            with self.subTest(content=content[:12]):
                path = self.write(content)
                with self.assertRaises(ValueError) as raised:
                    settings.read_env_file(path)
                self.assertNotIn("SyntheticPrivateValue", str(raised.exception))

    def test_oversize_and_non_utf8_file_are_rejected(self):
        path = self.write("#" * (settings.MAX_ENV_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            settings.read_env_file(path)
        path.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            settings.read_env_file(path)

    def test_required_settings_and_placeholder_errors_name_only(self):
        for key in ("PAK_DB_NAME", "PAK_DB_USER", "PAK_DB_PASSWORD", "PAK_PM_SEED_DIR", "PAK_BACKUP_DIR"):
            values = self.values()
            values.pop(key)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                settings.load_config(values, backend_directory=self.directory)
        with self.assertRaisesRegex(ValueError, "PAK_DB_PASSWORD"):
            settings.load_config(self.values(PAK_DB_PASSWORD="CHANGE_ME_PASSWORD"), backend_directory=self.directory)

    def test_process_environment_overrides_default_file(self):
        self.write("PAK_DB_PASSWORD=SyntheticFilePassword\nPAK_SCHEDULING_ENABLED=true\n")
        config = settings.load_config(self.values(PAK_SCHEDULING_ENABLED="false"), backend_directory=self.directory)
        self.assertEqual(config.database.password, "Synthetic!Literal:$#Only")
        self.assertFalse(config.scheduling_enabled)
        self.assertFalse(config.development)
        self.assertFalse(config.database.development)
        self.assertTrue(config.database.allow_loopback_plaintext)
        self.assertFalse(config.database.allow_lan_plaintext)
        self.assertEqual(config.database.host, "127.0.0.1")
        self.assertFalse(config.serve_frontend)
        self.assertIsNone(config.frontend_directory)
        self.assertTrue(config.reverse_proxy)
        self.assertFalse(config.allow_insecure_http)
        self.assertFalse(config.automatic_backup_enabled)
        self.assertNotIn(config.database.password, repr(config))

    def test_explicit_env_file_and_no_environment_mutation(self):
        selected = self.write("PAK_COMPANY_NAME=Example Company\n", name="private.env")
        values = self.values(PAK_ENV_FILE=str(selected))
        before = dict(values)
        config = settings.load_config(values, backend_directory=self.directory)
        self.assertEqual(config.company_name, "Example Company")
        self.assertEqual(values, before)
        self.assertFalse((self.directory / "seed").exists())
        self.assertFalse((self.directory / "backups").exists())

    def test_explicit_missing_env_file_is_not_silently_ignored(self):
        with self.assertRaises(FileNotFoundError):
            settings.load_config(self.values(PAK_ENV_FILE=str(self.directory / "missing.env")), backend_directory=self.directory)

    def test_boolean_and_integer_errors_do_not_print_values(self):
        for key, value in (("PAK_SCHEDULING_ENABLED", "not-a-boolean"), ("PAK_DB_PORT", "secret-invalid-port")):
            with self.subTest(key=key), self.assertRaises(ValueError) as raised:
                settings.load_config(self.values(**{key: value}), backend_directory=self.directory)
            self.assertIn(key, str(raised.exception))
            self.assertNotIn(value, str(raised.exception))

    def test_loopback_plaintext_flag_does_not_allow_remote_database(self):
        with self.assertRaises(ValueError):
            settings.load_config(self.values(PAK_DB_HOST="192.168.12.92"), backend_directory=self.directory)

    def test_private_lan_plaintext_requires_its_own_explicit_flag(self):
        config = settings.load_config(self.values(
            PAK_DB_HOST="192.168.12.92",
            PAK_DB_ALLOW_LOOPBACK_PLAINTEXT="false",
            PAK_DB_ALLOW_LAN_PLAINTEXT="true",
        ), backend_directory=self.directory)
        self.assertEqual(config.database.host, "192.168.12.92")
        self.assertEqual(config.database.sslmode, "disable")
        self.assertTrue(config.database.allow_lan_plaintext)
        self.assertFalse(config.database.allow_loopback_plaintext)
        self.assertFalse(config.database.development)
        self.assertFalse(config.development)

    def test_private_lan_plaintext_rejects_unflagged_remote_and_public_address(self):
        for changes in (
            dict(PAK_DB_HOST="192.168.12.92", PAK_DB_ALLOW_LOOPBACK_PLAINTEXT="false"),
            dict(PAK_DB_HOST="8.8.8.8", PAK_DB_ALLOW_LAN_PLAINTEXT="true"),
        ):
            with self.subTest(host=changes["PAK_DB_HOST"]), self.assertRaises(ValueError):
                settings.load_config(self.values(**changes), backend_directory=self.directory)
    def test_public_http_rejects_unflagged_production_and_keeps_explicit_development(self):
        with self.assertRaises(ValueError):
            settings.load_config(self.values(PAK_PUBLIC_URL="http://192.168.12.92"), backend_directory=self.directory)
        config = settings.load_config(self.values(PAK_PUBLIC_URL="http://192.168.12.92", PAK_DEVELOPMENT="true"), backend_directory=self.directory)
        self.assertTrue(config.development)

    def test_public_http_explicit_exception_preserves_production_validation(self):
        config = settings.load_config(self.values(
            PAK_PUBLIC_URL="http://192.168.12.92",
            PAK_ALLOW_INSECURE_HTTP="true",
        ), backend_directory=self.directory)
        self.assertTrue(config.allow_insecure_http)
        self.assertTrue(config.reverse_proxy)
        self.assertFalse(config.development)
        self.assertFalse(config.database.development)
        self.assertFalse(config.serve_frontend)

    def test_explicit_http_flag_is_supported_in_env_file(self):
        self.write("PAK_PUBLIC_URL=http://192.168.12.92\nPAK_ALLOW_INSECURE_HTTP=true\n")
        values = self.values()
        values.pop("PAK_PUBLIC_URL")
        config = settings.load_config(values, backend_directory=self.directory)
        self.assertEqual(config.listen_url, "http://192.168.12.92:80")
        self.assertTrue(config.allow_insecure_http)
        self.assertFalse(config.development)
    def test_desktop_runtime_paths_expand_current_user_home_only(self):
        def expanded(path):
            return self.directory.joinpath(*path.parts[1:])
        with patch("app.settings.Path.expanduser", autospec=True, side_effect=expanded):
            config = settings.load_config(self.values(
                PAK_PM_SEED_DIR="~/Desktop/PAK-data/seed",
                PAK_BACKUP_DIR="~/Desktop/PAK-data/backups",
                PAK_DB_PASSWORD="~/literal$Password!"), backend_directory=self.directory)
        self.assertEqual(config.seed_directory, str(self.directory/"Desktop/PAK-data/seed"))
        self.assertEqual(config.backup_directory, str(self.directory/"Desktop/PAK-data/backups"))
        self.assertEqual(config.database.password,"~/literal$Password!")
        self.assertFalse((self.directory/"Desktop").exists())

    def test_runtime_paths_reject_other_user_tilde_and_shell_variables(self):
        for value in ("~someone/Desktop/seed", "$HOME/Desktop/seed", "Desktop/seed"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings.load_config(self.values(PAK_PM_SEED_DIR=value), backend_directory=self.directory)

    def test_unavailable_home_is_reported_without_echoing_path_value(self):
        with patch("app.settings.Path.expanduser", side_effect=RuntimeError("synthetic-private-path")):
            with self.assertRaisesRegex(ValueError,"PAK_PM_SEED_DIR") as raised:
                settings.load_config(self.values(PAK_PM_SEED_DIR="~/Desktop/seed"), backend_directory=self.directory)
        self.assertNotIn("synthetic-private-path",str(raised.exception))

    def test_conventional_main_exposes_factory_result(self):
        token = object()
        config = object()
        sys.modules.pop("app.main", None)
        try:
            with patch("app.settings.load_config", return_value=config) as load:
                with patch("pak_maintenance.application.create_app", return_value=token) as factory:
                    module = import_module("app.main")
            self.assertIs(module.app, token)
            load.assert_called_once_with()
            factory.assert_called_once_with(config)
        finally:
            sys.modules.pop("app.main", None)


if __name__ == "__main__":
    unittest.main()
