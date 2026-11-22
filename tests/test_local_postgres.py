"""Local PostgreSQL ownership tests and an opt-in real 17+ dump restore.

The integration target is a newly created cluster containing synthetic rows;
the explicit QA server supplies only an unrelated new source database.
"""
import asyncio
from dataclasses import replace
import json
import io
import os
from pathlib import Path
import shutil
import socket
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from pak_maintenance import local_postgres, local_test
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import Database, SchemaMismatchError
from pak_maintenance.security import verify_password
from pg_test_support import fresh_database, native_tool_paths

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / ".build/local-postgres-tests"
PASSWORD = "Synthetic-Local-Postgres-2026!"


class LocalOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.directory = FIXTURES / uuid4().hex
        self.directory.mkdir(parents=True)
        self.binaries = self.directory / "postgres"
        self.dump = self.directory / "payload.dump"
        self.dump.write_bytes(b"PGDMPsynthetic-only")
        self.cluster = self.directory / "owned"
        self.local = local_postgres.LocalPostgres(self.binaries, self.cluster, self.dump,
            payload_sha256="a" * 64, port=55490)
        self.local.major = 17

    def tearDown(self):
        self.local.stop()
        target = self.directory.resolve()
        if not target.is_relative_to(FIXTURES.resolve()) or target == FIXTURES.resolve():
            raise RuntimeError("Unsafe synthetic cleanup target")
        shutil.rmtree(target)

    def test_new_private_state_encrypts_internal_passwords_and_reopens_same_identity(self):
        self.local._load_or_create_state()
        original = self.local.state["instanceId"]
        text = self.local.state_file.read_text(encoding="ascii")
        if os.name == "nt":
            self.assertEqual(self.local.state["credentials"]["protection"], "windows-user-dpapi")
            for value in self.local.credentials.values():
                self.assertNotIn(value, text)
        expected = dict(self.local.credentials)
        self.local._load_or_create_state()
        self.assertEqual(self.local.state["instanceId"], original)
        self.assertEqual(self.local.credentials, expected)
        self.assertEqual(self.local.database_config().provider, "postgresql")
        self.assertNotIn(expected["application"], repr(self.local.database_config()))

    @unittest.skipUnless(os.name == 'nt', 'Windows native path admission')
    def test_windows_non_ascii_paths_fail_before_files_or_processes_are_created(self):
        for changed in ('binaries','directory','dump_file'):
            paths={'binaries':self.binaries,'directory':self.cluster,'dump_file':self.dump}
            paths[changed]=self.directory/'پاک'
            local=local_postgres.LocalPostgres(**paths,payload_sha256='a'*64)
            with self.subTest(path=changed),patch.object(local,'_load_or_create_state') as initialize,patch.object(local_postgres,'_run') as run:
                with self.assertRaisesRegex(local_postgres.LocalPostgresPathError,'نام انگلیسی'):
                    local.start()
                initialize.assert_not_called();run.assert_not_called()
                self.assertFalse(paths[changed].exists())

    def test_path_failure_gives_an_actionable_persian_cli_message(self):
        output=io.StringIO()
        message='برنامه را در پوشه‌ای با نام انگلیسی مانند C:\\PAK اجرا کنید.'
        with patch.object(local_test,'launch_test',side_effect=local_postgres.LocalPostgresPathError(message)),patch('sys.stderr',output):
            result=local_test.main(['--root',str(self.directory),'--no-browser'])
        self.assertEqual(result,1)
        self.assertIn('نام انگلیسی',output.getvalue())
        self.assertFalse((self.directory/'test-data/postgresql').exists())

    def test_unrecognized_directory_never_adopted_or_overwritten(self):
        self.cluster.mkdir()
        unknown = self.cluster / "existing-important.txt"
        unknown.write_bytes(b"synthetic-existing-data")
        with self.assertRaisesRegex(local_postgres.LocalPostgresError, "unrecognized"):
            self.local._load_or_create_state()
        self.assertEqual(unknown.read_bytes(), b"synthetic-existing-data")
        self.assertFalse(self.local.state_file.exists())

    def test_mismatched_payload_path_or_major_never_reuses_cluster(self):
        self.local._load_or_create_state()
        for field, value in (("payloadSha256", "b" * 64), ("majorVersion", 18), ("dataDirectory", str(self.directory / "outside"))):
            state = {**self.local.state, field: value}
            local_postgres._atomic_state(self.local.state_file, state)
            with self.subTest(field=field), self.assertRaisesRegex(local_postgres.LocalPostgresError, "another package"):
                self.local._load_or_create_state()

    def test_fresh_cluster_configuration_is_loopback_scram_durable_and_changes_are_rejected(self):
        self.local._load_or_create_state()
        self.local.data.mkdir()
        self.local._write_cluster_configuration()
        text = (self.local.data / "postgresql.conf").read_text()
        self.assertIn("listen_addresses = '127.0.0.1'", text)
        self.assertIn("fsync = on", text)
        self.assertIn("synchronous_commit = on", text)
        self.assertIn("full_page_writes = on", text)
        hba = (self.local.data / "pg_hba.conf").read_text()
        self.assertIn("127.0.0.1/32 scram-sha-256", hba)
        self.assertNotIn("trust", hba)
        self.local._verify_configuration_files()
        (self.local.data / "pg_hba.conf").write_text("host all all 0.0.0.0/0 trust\n")
        with self.assertRaisesRegex(local_postgres.LocalPostgresError, "configuration changed"):
            self.local._verify_configuration_files()

    def test_occupied_database_port_is_not_reused_or_terminated(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0)); listener.listen(1)
            self.local.port = listener.getsockname()[1]
            with patch.object(self.local, "_verify_server_identity", side_effect=OSError("No owned server")), patch.object(local_postgres.subprocess, "Popen") as start:
                with self.assertRaisesRegex(local_postgres.LocalPostgresError, "no other process was stopped"):
                    self.local._start()
                start.assert_not_called()
                self.assertEqual(listener.getsockname()[1], self.local.port)

    def test_old_runtime_is_rejected_before_data_directory_creation(self):
        for path in self.local.executables.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-never-executed")
        with patch.object(local_postgres, "_run", return_value=SimpleNamespace(stdout=b"postgres (PostgreSQL) 16.13\n")):
            with self.assertRaisesRegex(local_postgres.LocalPostgresError, "17 or newer"):
                self.local.start()
        self.assertFalse(self.cluster.exists())

    def test_native_permission_denied_retains_only_a_verified_healthy_owned_server(self):
        self.local._load_or_create_state()
        self.local.started_here = self.local.identity_verified = True
        failure = local_postgres.LocalPostgresError("Native control denied", native_permission_denied=True)
        with patch.object(local_postgres, "_run", side_effect=failure), patch.object(self.local, "_verify_server_identity") as identity:
            self.local.stop()
        self.assertEqual(identity.call_count, 2)
        self.assertFalse(self.local.started_here)
        self.assertTrue(self.local.state["shutdownDeferred"])
        self.assertEqual(self.local.state["shutdownReason"], "native-control-unavailable")

    def test_other_shutdown_failures_are_not_silently_deferred(self):
        self.local._load_or_create_state()
        self.local.started_here = self.local.identity_verified = True
        try:
            with patch.object(local_postgres, "_run", side_effect=local_postgres.LocalPostgresError("Native timeout")), patch.object(self.local, "_verify_server_identity"):
                with self.assertRaisesRegex(local_postgres.LocalPostgresError, "timeout"):
                    self.local.stop()
            self.assertNotIn("shutdownDeferred", self.local.state)
        finally:
            self.local.started_here = False

    def test_ready_restart_only_verifies_schema_and_never_restores_grants_or_analyzes(self):
        self.local._load_or_create_state()
        self.local.state["phase"] = "ready"
        owner, runtime = Mock(), Mock()
        with patch.object(local_postgres, "Database", side_effect=(owner, runtime)), patch.object(self.local, "_connection", side_effect=AssertionError("Ready restart attempted native DDL")), patch.object(local_postgres, "_run", side_effect=AssertionError("Ready restart attempted pg_restore")):
            self.local._restore_and_grant()
        owner.verify_schema.assert_called_once_with(runtime_role=False)
        runtime.verify_schema.assert_called_once_with()

    def test_ready_restart_admission_failure_is_not_repaired(self):
        self.local._load_or_create_state()
        self.local.state["phase"] = "ready"
        owner, runtime = Mock(), Mock()
        runtime.verify_schema.side_effect = SchemaMismatchError("Runtime access was explicitly revoked")
        with patch.object(local_postgres, "Database", side_effect=(owner, runtime)), patch.object(self.local, "_connection", side_effect=AssertionError("Restart attempted permission repair")):
            with self.assertRaisesRegex(SchemaMismatchError, "explicitly revoked"):
                self.local._restore_and_grant()
        self.assertEqual(self.local.state["phase"], "ready")


@unittest.skipUnless(os.environ.get("PAK_POSTGRES_TEST_CONFIG"), "Explicit isolated PostgreSQL QA source is required")
class RealLocalPostgreSQLTests(unittest.TestCase):
    def test_native_dump_restore_restricted_runtime_browser_setup_and_restart(self):
        import httpx
        config, owner = fresh_database(self, prefix="localdump")
        with owner.transaction() as c:
            for identifier, role, active in (("head", "head", 1), ("admin", "admin", 1), ("tech", "technician", 1), ("inactive", "technician", 0)):
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:name,:role,'مکانیک',:active,:marker,7)",
                    {"id": identifier, "name": "نام ساختگی " + identifier, "role": role, "active": active, "marker": local_test.PASSWORD_MARKER})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','SYN-A','دستگاه ساختگی','سالن ساختگی','','{}')")
        directory = FIXTURES / uuid4().hex
        directory.mkdir(parents=True)
        dump = directory / "synthetic.dump"
        tools = native_tool_paths()
        binaries = Path(tools["postgresql_dump_executable"]).parents[1]
        environment = local_postgres._clean_environment(config.password)
        environment.update(PGSSLMODE=config.sslmode, PGSSLROOTCERT=config.sslrootcert)
        result = subprocess.run([tools["postgresql_dump_executable"], "--host", config.host, "--port", str(config.port),
            "--username", config.user, "--dbname", config.database, "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, "Native synthetic pg_dump failed")
        original_sha = local_test.digest(dump)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            default_port = reservation.getsockname()[1]
        local = local_postgres.LocalPostgres(binaries, directory / "owned", dump,
            payload_sha256="a" * 64, port=int(os.environ.get("PAK_LOCAL_POSTGRES_TEST_PORT", str(default_port))))
        try:
            with local.running() as runtime:
                db = Database(runtime)
                db.verify_schema()
                with db.connect() as c:
                    self.assertGreaterEqual(c.scalar("SELECT current_setting('server_version_num')::integer"), 170000)
                    self.assertEqual(c.scalar("SELECT COUNT(*) FROM users"), 4)
                    self.assertEqual(c.scalar("SELECT COUNT(*) FROM assets"), 1)
                    self.assertFalse(c.scalar("SELECT has_schema_privilege(current_user,'public','CREATE')"))
                frontend = directory / "frontend"; frontend.mkdir()
                (frontend / "index.html").write_text("<!doctype html><html lang='fa'>رابط اصلی ساختگی</html>", encoding="utf-8")
                app_config = AppConfig(database=runtime, listen_url="http://127.0.0.1:5918", development=True,
                    frontend_directory=str(frontend), seed_directory=str(directory), backup_directory=str(directory / "owned/backups"),
                    scheduling_enabled=False, automatic_backup_enabled=False, local_test_mode=True)
                app = local_test.TestSetupApp(app_config, directory)
                async def initialize():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 4567)), base_url=app_config.listen_url) as client:
                        self.assertIn("رابط اصلی", (await client.get("/")).text)
                        self.assertFalse((await client.get("/api/test-setup/state")).json()["initialized"])
                        response = await client.post("/api/test-setup/initialize", json={"password": PASSWORD, "passwordRepeat": PASSWORD}, headers={"x-app-request": "1", "origin": app_config.listen_url})
                        self.assertEqual(response.status_code, 200, response.text)
                asyncio.run(initialize())
                with db.connect() as c:
                    rows = c.query("SELECT password_hash,session_epoch FROM users")
                    self.assertTrue(all(verify_password(PASSWORD, row["password_hash"]) and row["session_epoch"] == 8 for row in rows))
                with db.transaction() as c:
                    c.execute("UPDATE assets SET name='تغییر ساختگی پس از راه‌اندازی' WHERE id='asset'")
                instance_id = local.state["instanceId"]
            self.assertFalse(local.started_here)
            reopened = local_postgres.LocalPostgres(binaries, directory / "owned", dump, payload_sha256="a" * 64, port=local.port)
            with reopened.running() as runtime:
                self.assertEqual(reopened.state["instanceId"], instance_id)
                db = Database(runtime)
                with db.connect() as c:
                    self.assertEqual(c.scalar("SELECT name FROM assets WHERE id='asset'"), "تغییر ساختگی پس از راه‌اندازی")
                    self.assertEqual(c.scalar("SELECT COUNT(*) FROM audit WHERE id='local-test-credentials-v1'"), 1)
                restarted = local_test.TestSetupApp(replace(app_config, database=runtime), directory)
                self.assertTrue(restarted.initialized)
                async def login():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted, client=("127.0.0.1", 4567)), base_url=app_config.listen_url) as client:
                        response = await client.post("/api/auth/login", json={"code": "head", "password": PASSWORD}, headers={"x-app-request": "1"})
                        self.assertEqual(response.status_code, 200, response.text)
                asyncio.run(login())
                # A ready reopen must respect a deliberately revoked runtime
                # privilege rather than silently granting it back at startup.
                with reopened._connection(role="owner", database=runtime.database) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute('REVOKE SELECT ON public.assets FROM "pak_local_app"')
                with self.assertRaises(SchemaMismatchError):
                    reopened._restore_and_grant()
                with reopened._connection(role="owner", database=runtime.database) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT has_table_privilege('pak_local_app','public.assets','SELECT')")
                        self.assertFalse(cursor.fetchone()[0])
                        # Explicit test-owner cleanup, never a launcher repair.
                        cursor.execute('GRANT SELECT ON public.assets TO "pak_local_app"')
                        cursor.execute('GRANT TRUNCATE ON public.assets TO "pak_local_app"')
                with self.assertRaises(SchemaMismatchError):
                    reopened._restore_and_grant()
                with reopened._connection(role="owner", database=runtime.database) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT has_table_privilege('pak_local_app','public.assets','TRUNCATE')")
                        self.assertTrue(cursor.fetchone()[0])
                        cursor.execute('REVOKE TRUNCATE ON public.assets FROM "pak_local_app"')
            self.assertEqual(local_test.digest(dump), original_sha)
        finally:
            local.stop()
        # Retain this explicitly synthetic cluster and native logs as evidence;
        # no live user files or other PostgreSQL process is ever removed.


if __name__ == "__main__":
    unittest.main()
