"""The explicit maintenance entry point never upgrades before verified backup."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load_tool():
    path = Path(__file__).resolve().parents[1] / "tools/upgrade_pm_archive.py"
    spec = importlib.util.spec_from_file_location("upgrade_archive_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpgradeArchiveToolTests(unittest.TestCase):
    def config(self, **changes):
        values = dict(database=object(), backup_directory="private-backups",
                      postgresql_dump_executable="pg_dump.exe", postgresql_restore_executable="pg_restore.exe")
        values.update(changes)
        return SimpleNamespace(**values)

    def test_backup_failure_prevents_schema_change(self):
        tool = load_tool()
        with patch.object(tool.AppConfig, "load", return_value=self.config()), \
             patch.object(tool, "Database"), patch.object(tool, "BackupService") as backup, \
             patch.object(tool, "upgrade_postgresql_v7") as upgrade:
            backup.return_value.create_snapshot.side_effect = RuntimeError("verification failed")
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                tool.main(["--owner-config", "owner.toml", "--runtime-user", "limited_app"])
            upgrade.assert_not_called()

    def test_missing_native_tools_prevents_backup_and_ddl(self):
        tool = load_tool()
        with patch.object(tool.AppConfig, "load", return_value=self.config(postgresql_dump_executable=None)), \
             patch.object(tool, "BackupService") as backup, patch.object(tool, "upgrade_postgresql_v7") as upgrade:
            with self.assertRaises(ValueError):
                tool.main(["--owner-config", "owner.toml", "--runtime-user", "limited_app"])
            backup.assert_not_called()
            upgrade.assert_not_called()

    def test_success_requires_verified_backup_before_upgrade(self):
        tool, calls = load_tool(), []
        snapshot = SimpleNamespace(path=Path("native.dump"), size=123, sha256="a" * 64)
        def backed_up():
            calls.append("backup")
            return snapshot
        def upgraded(database, *, runtime_user):
            self.assertEqual(calls, ["backup"])
            self.assertEqual(runtime_user, "limited_app")
            calls.append("upgrade")
            return {"schemaVersion": 8}
        with patch.object(tool.AppConfig, "load", return_value=self.config()), patch.object(tool, "Database"), \
             patch.object(tool, "BackupService") as backup, patch.object(tool, "upgrade_postgresql_v7", side_effect=upgraded), \
             patch("builtins.print") as output:
            backup.return_value.create_snapshot.side_effect = backed_up
            self.assertEqual(tool.main(["--owner-config", "owner.toml", "--runtime-user", "limited_app"]), 0)
            self.assertEqual(calls, ["backup", "upgrade"])
            self.assertNotIn("password", str(output.call_args).lower())
