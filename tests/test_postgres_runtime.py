"""PostgreSQL-only application boundary and native-backup fault injection.

These deliberately do not claim to replace real PostgreSQL dump/restore QA.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pak_maintenance.backup import BackupService
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig, DatabaseConfigurationError, DatabaseDependencyError
from pak_maintenance.postgres_backup import create_archive, _passfile, _run
from test_database import fixture_directory, postgres_config


class PostgreSQLConfigurationTests(unittest.TestCase):
    def arguments(self, directory):
        return dict(database=postgres_config(), listen_url="http://127.0.0.1:5187",
                    frontend_directory=str(directory), seed_directory=str(directory),
                    backup_directory=str(directory / "backups"), development=True)

    def test_sqlite_is_rejected_in_every_application_mode(self):
        with fixture_directory() as directory:
            for local_test in (False, True):
                options = self.arguments(directory)
                options.update(database=DatabaseConfig("sqlite", str(directory / "offline.db"), development=True),
                               local_test_mode=local_test)
                with self.subTest(local_test=local_test), self.assertRaisesRegex(ValueError, "PostgreSQL 17"):
                    AppConfig(**options)
            config = AppConfig(**self.arguments(directory), local_test_mode=True)
            self.assertEqual(config.database.provider, "postgresql")

    def test_local_test_rejects_remote_database_even_when_private_tls_is_valid(self):
        with fixture_directory() as directory:
            ca = directory / "ca.pem"
            ca.write_text("fixture only", encoding="utf-8")
            options = self.arguments(directory)
            options["database"] = postgres_config(host="192.168.1.20", sslmode="verify-full", sslrootcert=str(ca))
            with self.assertRaisesRegex(ValueError, "loopback"):
                AppConfig(**options, local_test_mode=True)

    def test_automatic_postgres_backups_require_explicit_existing_tools(self):
        with fixture_directory() as directory:
            with self.assertRaisesRegex(ValueError, "pg_dump"):
                AppConfig(**self.arguments(directory), automatic_backup_enabled=True)
            tool = directory / "offline-tool.exe"
            tool.write_bytes(b"fixture executable path validation only")
            config = AppConfig(**self.arguments(directory), automatic_backup_enabled=True,
                               postgresql_dump_executable=str(tool), postgresql_restore_executable=str(tool))
            self.assertTrue(config.automatic_backup_enabled)
            self.assertEqual(config.postgresql_dump_executable, str(tool))
            with self.assertRaisesRegex(ValueError, "both"):
                AppConfig(**self.arguments(directory), postgresql_dump_executable=str(tool))

    def test_unconfigured_manual_backup_returns_actionable_status_without_creating_files(self):
        from pak_maintenance.common import ApiError
        with fixture_directory() as directory:
            service = BackupService(SimpleNamespace(config=postgres_config()), directory / "backups")
            self.assertFalse(service.status()["manualAvailable"])
            with self.assertRaises(ApiError) as error:
                service.create_snapshot()
            self.assertEqual(error.exception.status, 503)
            self.assertIn("pg_dump", error.exception.message)
            self.assertFalse(service.directory.exists())


class PostgreSQLBackupBoundaryTests(unittest.TestCase):
    def tools(self, directory):
        paths = [directory / name for name in ("pg_dump.exe", "pg_restore.exe")]
        for path in paths:
            path.write_bytes(b"fault-injection fixture; never executed")
        return paths

    def database(self):
        config = postgres_config(password="synthetic:secret\\password")
        @contextmanager
        def connect():
            yield SimpleNamespace(raw=SimpleNamespace(info=SimpleNamespace(server_version=180006)))
        return SimpleNamespace(config=config, connect=connect)

    def version_result(self, arguments, **kwargs):
        output = (Path(arguments[0]).stem + " (PostgreSQL) 18.6").encode("ascii")
        return SimpleNamespace(returncode=0, stdout=output)

    def test_password_is_absent_from_argv_and_protected_passfile_is_removed(self):
        with fixture_directory() as directory:
            dump, restore = self.tools(directory)
            database = self.database()
            calls = []
            testcase = self
            class Process:
                def __init__(self, arguments, **kwargs):
                    calls.append((arguments, kwargs))
                    testcase.assertNotIn(database.config.password, str(arguments))
                    testcase.assertIs(kwargs["stdout"], subprocess.DEVNULL)
                    testcase.assertIs(kwargs["stderr"], subprocess.DEVNULL)
                    if Path(arguments[0]) == dump:
                        passfile = Path(kwargs["env"]["PGPASSFILE"])
                        testcase.assertEqual(passfile.parent.parent, directory / "backups")
                        testcase.assertIn(r"synthetic\:secret\\password", passfile.read_text(encoding="utf-8"))
                        destination = next(value[7:] for value in arguments if value.startswith("--file="))
                        Path(destination).write_bytes(b"PGDMPsynthetic archive for fault injection")
                    else:
                        testcase.assertNotIn("PGPASSFILE", kwargs["env"])
                        testcase.assertIn("--file=" + os.devnull, arguments)
                def wait(self, timeout=None):
                    return 0
                def poll(self):
                    return 0
            service = BackupService(database, directory / "backups", automatic_enabled=True,
                                    dump_executable=str(dump), restore_executable=str(restore))
            with patch("pak_maintenance.postgres_backup.subprocess.run", side_effect=self.version_result), \
                 patch("pak_maintenance.postgres_backup.subprocess.Popen", Process):
                snapshot = service.run_automatic_if_due()
                self.assertIsNone(service.run_automatic_if_due())
            self.assertEqual(snapshot.path.suffix, ".dump")
            self.assertEqual(len(calls), 2)
            self.assertEqual(list(service.directory.glob(".pak-pgpass-*")), [])
            self.assertEqual(list(service.directory.glob("*.partial")), [])
            self.assertEqual(service.status()["method"], "postgresql-custom-archive")
            self.assertEqual(service.status()["registeredAutomaticSnapshots"], 1)

    def test_inherited_libpq_or_tls_redirection_is_rejected_before_tool_start(self):
        with fixture_directory() as directory:
            dump, restore = self.tools(directory)
            for key in ("PGSERVICE", "PGHOSTADDR", "PGPASSFILE", "OPENSSL_CONF", "SSL_CERT_DIR"):
                with self.subTest(key=key), patch.dict(os.environ, {key: "untrusted"}), \
                     patch("pak_maintenance.postgres_backup.subprocess.run") as run:
                    with self.assertRaises(DatabaseConfigurationError):
                        create_archive(self.database(), directory / "never.dump", dump_executable=str(dump),
                                       restore_executable=str(restore), deadline=time.monotonic() + 10)
                    run.assert_not_called()
            self.assertEqual(list(directory.glob(".pak-pgpass-*")), [])

    def test_passfile_is_removed_when_tool_raises_and_backup_is_not_published(self):
        with fixture_directory() as directory:
            dump, restore = self.tools(directory)
            service = BackupService(self.database(), directory / "backups", dump_executable=str(dump), restore_executable=str(restore))
            with patch("pak_maintenance.postgres_backup.subprocess.run", side_effect=self.version_result), \
                 patch("pak_maintenance.postgres_backup.subprocess.Popen", side_effect=OSError("injected tool start error")):
                with self.assertRaises(OSError):
                    service.create_snapshot()
            self.assertEqual(list(service.directory.iterdir()), [])
            self.assertIsNotNone(service.status()["lastError"])
            self.assertFalse(service.status()["running"])

    def test_old_or_mismatched_tool_versions_fail_before_opening_source(self):
        with fixture_directory() as directory:
            dump, restore = self.tools(directory)
            database = self.database()
            for version in ("16.9", "17.11"):
                with self.subTest(version=version), patch("pak_maintenance.postgres_backup.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=("pg_dump (PostgreSQL) " + version).encode())), \
                    patch("pak_maintenance.postgres_backup.subprocess.Popen") as popen:
                    with self.assertRaises(DatabaseDependencyError):
                        create_archive(database, directory / "never.dump", dump_executable=str(dump), restore_executable=str(restore),
                                       deadline=time.monotonic() + 10)
                    popen.assert_not_called()
            self.assertFalse((directory / "never.dump").exists())

    def test_cancelled_process_is_killed_and_waited(self):
        process = unittest.mock.Mock()
        process.poll.return_value = None
        with patch("pak_maintenance.postgres_backup.subprocess.Popen", return_value=process):
            with self.assertRaises(TimeoutError):
                _run(["fixture.exe"], {}, time.monotonic() + 10, lambda: True)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with()

    def test_protected_passfile_is_removed_even_when_context_fails(self):
        with fixture_directory() as directory:
            with self.assertRaisesRegex(LookupError, "synthetic"):
                with _passfile(directory, postgres_config()) as path:
                    self.assertTrue(path.is_file())
                    raise LookupError("synthetic")
            self.assertEqual(list(directory.iterdir()), [])

    def test_private_directory_collision_preserves_existing_unknown_files(self):
        with fixture_directory() as directory:
            existing = directory / ".pak-pgpass-fixed-id"
            existing.mkdir(mode=0o755)
            evidence = existing / "pgpass"
            evidence.write_bytes(b"unrelated existing file must survive")
            with patch("pak_maintenance.postgres_backup.uuid4", return_value=SimpleNamespace(hex="fixed-id")):
                with self.assertRaises(FileExistsError):
                    with _passfile(directory, postgres_config()):
                        self.fail("An existing credential directory was reused")
            self.assertEqual(evidence.read_bytes(), b"unrelated existing file must survive")


if __name__ == "__main__":
    unittest.main()
