"""Independent local acceptance-copy setup tests; synthetic data only.

Real PostgreSQL 17+, ASGI/httpx, OS file locks and loopback sockets are used.
The browser readiness tests inject only Uvicorn's run loop, never an actual
browser. No company files, accounts, listeners or server settings are changed.
"""
import asyncio
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from pak_maintenance import local_test
from pak_maintenance.common import ApiError
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig
from pg_test_support import fresh_database, native_tool_paths
from pak_maintenance.security import verify_password

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "starlette", "httpx", "uvicorn"))
if AVAILABLE:
    import httpx

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT / "tests" / ".local-test-fixtures"
PASSWORD = "Synthetic-local-test-123!"


class SyntheticFixture:
    def setUp(self):
        self.directory = FIXTURE_ROOT / uuid4().hex
        self.root = self.directory / "package"
        self.data = self.root / "test-data"
        self.frontend = self.root / "frontend"
        self.payload = self.root / "test-payload"
        self.seed = self.payload / "seed"
        for path in (self.frontend, self.seed):
            path.mkdir(parents=True, mode=0o755)
        (self.frontend / "index.html").write_text("<!doctype html><html lang='fa'><body>رابط ساختگی اصلی</body></html>", encoding="utf-8")
        (self.frontend / "synthetic.css").write_text("body{color:#fff;background:#10202d}", encoding="utf-8")
        self.seed_file = self.seed / "synthetic-pm.json"
        self.seed_file.write_text('{"title":"برگهٔ ساختگی"}', encoding="utf-8")
        self.source = self.payload / "acceptance-source.dump"
        self.source.write_bytes(b"PGDMPsynthetic-manifest-fixture")
        for name in ("pg_dump.exe", "pg_restore.exe") if os.name == "nt" else ("pg_dump", "pg_restore"):
            path = self.root / "postgres/bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-never-executed")
        self.manifest_path = self.payload / "acceptance-data.json"
        self.manifest = {"databaseProvider": "postgresql", "databaseFormat": "postgresql-custom", "schemaVersion": 8, "databaseFile": self.source.name, "databaseSha256": local_test.digest(self.source),
            "authentication": {"passwordMarker": local_test.PASSWORD_MARKER},
            "seedFiles": [{"path": self.seed_file.name, "bytes": self.seed_file.stat().st_size, "sha256": local_test.digest(self.seed_file)}]}
        self.manifest_path.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")
        self.original_hash = local_test.digest(self.source)

    def tearDown(self):
        self.assertEqual(local_test.digest(self.source), self.original_hash, "Test changed its bundled source database")
        target = self.directory.resolve()
        if target == FIXTURE_ROOT.resolve() or not target.is_relative_to(FIXTURE_ROOT.resolve()):
            raise RuntimeError("Refusing to remove files outside the owned synthetic fixture")
        shutil.rmtree(target)
        if FIXTURE_ROOT.exists() and not any(FIXTURE_ROOT.iterdir()):
            FIXTURE_ROOT.rmdir()

    def prepare(self):
        return local_test.prepare_copy(self.root, self.data)

    def make_app(self):
        self.prepare()
        database, owner = fresh_database(self, prefix="localsetup")
        with owner.transaction() as c:
            for identifier, role, active in (("admin", "admin", 1), ("head", "head", 1), ("tech", "technician", 1), ("inactive", "technician", 0)):
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:name,:role,:specialty,:active,:marker,7)",
                    {"id": identifier, "name": "نام ساختگی " + identifier, "role": role, "specialty": "مکانیک" if role == "technician" else "", "active": active, "marker": local_test.PASSWORD_MARKER})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','SYN-A','دستگاه ساختگی','سالن ساختگی','','{}')")
            c.execute("INSERT INTO sessions(id,user_id,epoch,expires) VALUES('old-session','tech',7,'2099-01-01T00:00:00+00:00')")
        config = AppConfig(database=database, listen_url="http://127.0.0.1:5918",
            frontend_directory=str(self.frontend), seed_directory=str(self.seed), backup_directory=str(self.data / "backups"),
            development=True, scheduling_enabled=False, automatic_backup_enabled=False, local_test_mode=True, **native_tool_paths())
        return local_test.TestSetupApp(config, self.data, instance_id="synthetic-instance")


class LocalCopyTests(SyntheticFixture, unittest.TestCase):
    def test_manifest_checkpoint_preserves_immutable_dump_and_does_not_create_file_database(self):
        self.prepare()
        self.assertEqual(local_test.digest(self.source), self.original_hash)
        self.assertFalse((self.data / "acceptance.db").exists())
        marker = local_test.read_json(self.data / "copy.json")
        self.assertEqual(marker["databaseProvider"], "postgresql")
        self.prepare()
        self.assertEqual(local_test.read_json(self.data / "copy.json"), marker)

    def test_legacy_or_wrong_format_manifest_is_rejected_before_mutation(self):
        for field, value in (("databaseProvider", "sqlite"), ("databaseFormat", "sql"), ("schemaVersion", 6)):
            manifest = {**self.manifest, field: value}
            self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires an explicit PostgreSQL"):
                self.prepare()
            self.assertFalse(self.data.exists())

    def test_database_manifest_hash_mismatch_is_rejected_before_copy(self):
        self.manifest["databaseSha256"] = "0" * 64
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            self.prepare()
        self.assertFalse((self.data / "acceptance.db").exists())

    def test_seed_hash_and_payload_path_escape_are_rejected(self):
        self.seed_file.write_text("changed synthetic file", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "PM file is incomplete or changed"):
            self.prepare()
        for path in ("../outside.db", "/outside.db", "a//b", "a\\b", "C:/outside.db"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                local_test.checked_child(self.payload, path)

    def test_different_payload_identity_and_legacy_data_never_overwrite(self):
        self.prepare()
        self.manifest["syntheticRevision"] = 2
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different package"):
            self.prepare()
        (self.data / "copy.json").unlink()
        target = self.data / "acceptance.db"
        target.write_bytes(b"unrecognized old data")
        with self.assertRaisesRegex(ValueError, "explicit verified PostgreSQL migration"):
            self.prepare()
        self.assertEqual(target.read_bytes(), b"unrecognized old data")

    def test_actual_instance_lock_refuses_duplicate_then_releases(self):
        with local_test.instance_lock(self.data) as first:
            self.assertTrue(first)
            with local_test.instance_lock(self.data) as second:
                self.assertFalse(second)
        with local_test.instance_lock(self.data) as next_instance:
            self.assertTrue(next_instance)

    def test_launcher_rejects_outside_root_or_payload_data_before_any_mutation(self):
        outside = self.directory / "outside-root"
        inside_payload = self.payload / "mutable-child"
        with patch.object(local_test, "instance_lock", side_effect=AssertionError("Invalid path reached instance lock")):
            for path in (outside, inside_payload):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "inside this extracted application"):
                    local_test.launch_test(self.root, path, browser=False)
                self.assertFalse(path.exists())
                self.assertFalse((path / "instance.lock").exists())

    def test_actual_loopback_port_conflict_falls_back_without_touching_listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            preferred = occupied.getsockname()[1]
            listener = local_test.bind_listener(preferred)
            try:
                self.assertEqual(listener.getsockname()[0], "127.0.0.1")
                self.assertNotEqual(listener.getsockname()[1], preferred)
                self.assertEqual(occupied.getsockname()[1], preferred)
            finally:
                listener.close()


@unittest.skipUnless(AVAILABLE, "Real local setup framework dependencies are unavailable")
class LocalSetupHttpTests(SyntheticFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = self.make_app()
        self.lifecycle = self.app.application.router.lifespan_context(self.app.application)
        await self.lifecycle.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 4567)), base_url=self.app.config.listen_url)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)

    async def initialize(self, password=PASSWORD):
        return await self.client.post("/api/test-setup/initialize", json={"password": password, "passwordRepeat": password}, headers={"x-app-request": "1", "origin": self.app.config.listen_url})

    async def test_first_setup_assets_state_and_no_normal_auth_before_initialize(self):
        page = await self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("رابط ساختگی اصلی", page.text)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        stylesheet = await self.client.get("/synthetic.css")
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("css", stylesheet.headers["content-type"])
        health = await self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["localTestMode"])
        self.assertFalse(health.json()["localTestInitialized"])
        self.assertEqual((await self.client.get("/api/auth/me")).status_code, 401)
        self.assertEqual((await self.client.get("/api/bootstrap")).status_code, 409)
        self.assertEqual((await self.client.get("/synthetic-missing.css")).status_code, 404)
        state = await self.client.get("/api/test-setup/state")
        self.assertEqual(state.json(), {"initialized": False, "busy": False, "instanceId": "synthetic-instance"})
        self.assertEqual(state.headers["cache-control"], "no-store")
        self.assertEqual((await self.client.get("/api/test-setup/accounts")).status_code, 409)
        self.assertEqual((await self.client.post("/api/auth/login", json={"code": "admin", "password": PASSWORD}, headers={"x-app-request": "1"})).status_code, 409)

    async def test_initialize_credentials_accounts_and_normal_api_without_production_changes(self):
        self.assertEqual((await self.initialize()).status_code, 200)
        with self.app.services.db.connect() as connection:
            users = connection.query("SELECT password_hash,session_epoch FROM users")
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM sessions"), 0)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM audit WHERE id='local-test-credentials-v1'"), 1)
        self.assertTrue(all(verify_password(PASSWORD, row["password_hash"]) and row["session_epoch"] == 8 for row in users))
        self.assertEqual(len({row["password_hash"] for row in users}), len(users))
        metadata = (await self.client.get("/api/test-setup/accounts")).json()["accounts"]
        self.assertEqual([row["code"] for row in metadata], ["head", "admin", "tech"])
        self.assertTrue(all(set(row) == {"code", "name", "role", "specialty"} for row in metadata))
        self.assertNotIn(PASSWORD, json.dumps(metadata))
        health = await self.client.get("/api/health")
        self.assertTrue(health.json()["localTestMode"])
        self.assertTrue(health.json()["localTestInitialized"])
        self.assertFalse(health.json()["productionAccepted"])
        login = await self.client.post("/api/auth/login", json={"code": "admin", "password": PASSWORD}, headers={"x-app-request": "1"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual((await self.client.get("/api/auth/me")).json()["user"]["code"], "admin")
        bootstrap = await self.client.get("/api/bootstrap?catalogMode=paged")
        self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
        self.assertIn("رابط ساختگی اصلی", (await self.client.get("/")).text)
        reset = await self.initialize()
        self.assertEqual(reset.status_code, 409)

    async def test_credentials_audit_checkpoint_survives_missing_informational_file_and_restart(self):
        with patch.object(local_test, "atomic_json", side_effect=OSError("Synthetic disk-full metadata failure")):
            response = await self.initialize()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((self.data / "credentials-ready.json").exists())
        copy_hash = local_test.digest(self.source)
        restarted = local_test.TestSetupApp(self.app.config, self.data)
        self.assertTrue(restarted.initialized)
        with self.assertRaises(ApiError) as denied:
            restarted.initialize({"password": PASSWORD, "passwordRepeat": PASSWORD})
        self.assertEqual(denied.exception.status, 409)
        self.assertEqual(local_test.digest(self.source), copy_hash)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted, client=("127.0.0.1", 4567)), base_url=self.app.config.listen_url) as client:
            login = await client.post("/api/auth/login", json={"code": "tech", "password": PASSWORD}, headers={"x-app-request": "1"})
            self.assertEqual(login.status_code, 200, login.text)

    async def test_setup_csrf_external_host_origin_and_remote_peers_denied_without_writes(self):
        payload = {"password": PASSWORD, "passwordRepeat": PASSWORD}
        for headers, expected in (({}, 403), ({"x-app-request": "1", "origin": "https://external.invalid"}, 403), ({"x-app-request": "1", "sec-fetch-site": "cross-site"}, 403), ({"x-app-request": "1", "host": "external.invalid"}, 400)):
            response = await self.client.post("/api/test-setup/initialize", json=payload, headers=headers)
            self.assertEqual(response.status_code, expected, response.text)
        for peer in ("192.168.1.9", "8.8.8.8"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=(peer, 4567)), base_url=self.app.config.listen_url) as remote:
                response = await remote.get("/api/test-setup/state", headers={"x-forwarded-for": "127.0.0.1"})
                self.assertEqual(response.status_code, 403)
        self.assertFalse(self.app.initialized)
        with self.app.services.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM users WHERE password_hash=:marker", {"marker": local_test.PASSWORD_MARKER}), 4)

    async def test_invalid_password_duplicate_json_and_wrong_content_type_are_rejected(self):
        for payload in ({"password": "short", "passwordRepeat": "short"}, {"password": PASSWORD, "passwordRepeat": PASSWORD + "x"}, {"password": PASSWORD, "passwordRepeat": PASSWORD, "unexpected": True}):
            response = await self.client.post("/api/test-setup/initialize", json=payload, headers={"x-app-request": "1"})
            self.assertEqual(response.status_code, 400, response.text)
        duplicate = await self.client.post("/api/test-setup/initialize", content='{"password":"one","password":"two","passwordRepeat":"two"}', headers={"x-app-request": "1", "content-type": "application/json"})
        self.assertEqual(duplicate.status_code, 400)
        wrong_type = await self.client.post("/api/test-setup/initialize", content="not JSON", headers={"x-app-request": "1", "content-type": "text/plain"})
        self.assertEqual(wrong_type.status_code, 415)
        self.assertFalse(self.app.initialized)

    async def test_setup_transaction_rolls_back_if_any_copied_password_marker_is_invalid(self):
        with self.app.services.db.transaction() as connection:
            connection.execute("UPDATE users SET password_hash='synthetic-invalid-marker' WHERE id='tech'")
        response = await self.initialize()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(self.app.initialized)
        with self.app.services.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM users WHERE password_hash=:marker", {"marker": local_test.PASSWORD_MARKER}), 3)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM users WHERE session_epoch=7"), 4)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM audit WHERE id='local-test-credentials-v1'"), 0)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM sessions"), 1)

    async def test_concurrent_first_setup_commits_exactly_once(self):
        responses = await asyncio.gather(self.initialize(), self.initialize())
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        with self.app.services.db.connect() as connection:
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM audit WHERE id='local-test-credentials-v1'"), 1)
            self.assertEqual(connection.scalar("SELECT COUNT(*) FROM users WHERE session_epoch=8"), 4)

    async def test_normal_server_has_no_local_setup_wrapper(self):
        with self.assertRaisesRegex(ValueError, "forbidden in normal server"):
            local_test.TestSetupApp(replace(self.app.config, local_test_mode=False), self.data)


@unittest.skipUnless(AVAILABLE, "Actual local launcher dependencies are unavailable")
class LocalLauncherTests(SyntheticFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.database = DatabaseConfig(provider="postgresql", host="127.0.0.1", port=55490,
            database="pak_synthetic", user="pak_synthetic_app", password="Synthetic-internal-db-credential", sslmode="disable", development=True)
        postgres = Mock()
        postgres.running.return_value.__enter__ = Mock(return_value=self.database)
        postgres.running.return_value.__exit__ = Mock(return_value=False)
        self.addCleanup(patch.stopall)
        patch.object(local_test, "LocalPostgres", return_value=postgres).start()
        patch.object(local_test, "TestSetupApp", return_value=SimpleNamespace(instance_id="controlled-instance")).start()

    def test_browser_opens_only_after_listener_readiness_and_identity_record(self):
        observations = []
        opened = threading.Event()
        class ControlledServer:
            def __init__(server, config):
                server.started = False
            def run(server, sockets):
                observations.append(("before", opened.is_set(), (self.data / "instance.json").exists()))
                time.sleep(0.15)
                self.assertFalse(opened.is_set())
                server.started = True
                self.assertTrue(opened.wait(3), "Ready listener did not open its browser")
                observations.append(("after", opened.is_set(), (self.data / "instance.json").exists()))
        def browser(url):
            record = local_test.read_json(self.data / "instance.json")
            self.assertEqual(url, f"http://127.0.0.1:{record['port']}")
            self.assertTrue(record["instanceId"])
            opened.set()
        import uvicorn
        with patch.object(uvicorn, "Server", ControlledServer), patch.object(local_test, "open_browser", side_effect=browser), patch("builtins.print"):
            port = local_test.launch_test(self.root, self.data, browser=True)
        self.assertGreater(port, 0)
        self.assertEqual(observations, [("before", False, False), ("after", True, True)])

    def test_no_browser_cli_and_failed_startup_never_open_browser(self):
        with patch.object(local_test, "launch_test", return_value=5187) as launch, patch.object(local_test, "open_browser") as browser:
            self.assertEqual(local_test.main(["--root", str(self.root), "--no-browser"]), 0)
            self.assertFalse(launch.call_args.kwargs["browser"])
            browser.assert_not_called()
        class ReadyServer:
            def __init__(server, config):
                server.started = False
            def run(server, sockets):
                server.started = True
                deadline = time.monotonic() + 3
                while not (self.data / "instance.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((self.data / "instance.json").is_file())
        class FailedServer:
            def __init__(server, config):
                server.started = False
            def run(server, sockets):
                return
        import uvicorn
        with patch.object(uvicorn, "Server", ReadyServer), patch.object(local_test, "open_browser") as browser, patch("builtins.print"):
            local_test.launch_test(self.root, self.data, browser=False)
            browser.assert_not_called()
        with patch.object(uvicorn, "Server", FailedServer), patch.object(local_test, "open_browser") as browser:
            with self.assertRaisesRegex(RuntimeError, "did not finish startup"):
                local_test.launch_test(self.root, self.data, browser=True)
            browser.assert_not_called()

    def test_existing_instance_identity_mismatch_never_opens_browser(self):
        self.data.mkdir(mode=0o755)
        local_test.atomic_json(self.data / "instance.json", {"port": 5918, "instanceId": "expected-instance"})
        response = SimpleNamespace(status=200, read=lambda size: b'{"instanceId":"wrong-instance"}')
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(local_test.http.client, "HTTPConnection", return_value=connection), patch.object(local_test, "open_browser") as browser:
            with self.assertRaisesRegex(ValueError, "identity could not be verified"):
                local_test.reopen_existing(self.data)
            browser.assert_not_called()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
