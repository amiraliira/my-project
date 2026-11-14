"""Software boundaries using only local disposable fixtures, no server setup."""
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from decimal import Decimal

from pak_maintenance.api import Api, Request, safe_error
from pak_maintenance.application import Scheduler, compose, create_app, static_path
from pak_maintenance.backup import BackupService
from pak_maintenance.common import ApiError, User, loads
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import Database, DatabaseConfig, DatabaseDependencyError
from pak_maintenance.exports import ExportStream, csv_line
from pak_maintenance.network_policy import NetworkPolicyError
from pak_maintenance.security import Authentication, COOKIE, hash_password, token_digest, verify_password
from pg_test_support import fresh_database, native_tool_paths, assert_database_constraints

ROOT = Path(__file__).resolve().parents[1]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "tests" / ".boundary-fixtures" / uuid4().hex
        self.directory.mkdir(parents=True, mode=0o755)
        database_config, self.owner = fresh_database(self, prefix="boundary")
        self.config = AppConfig(database_config,
            "http://127.0.0.1:5914", str(self.directory / "wwwroot"), str(self.directory / "seed"), str(self.directory / "backups"),
            development=True, scheduling_enabled=False, **native_tool_paths())
        self.db = Database(self.config.database)

    def tearDown(self):
        shutil.rmtree(self.directory)
        if not any(self.directory.parent.iterdir()):
            self.directory.parent.rmdir()


class RequestTests(unittest.TestCase):
    def test_strict_json_shape_encoding_precision_and_content_type(self):
        def request(body, content_type="application/json"):
            return Request("POST", "/api/orders", headers={"content-type": content_type}, body=body)
        self.assertEqual(request(b'{"n":0.1000001}').json()["n"], Decimal("0.1000001"))
        for raw in (b'{"a":1,"a":2}', b'{"n":NaN}', b'{"n":1e99999999}', b'{"n":1e-99999999}', b'{"n":"\\ud800"}', b'[]', b'null', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ApiError) as raised:
                request(raw).json()
            self.assertEqual(raised.exception.status, 400)
        with self.assertRaises(ApiError) as raised:
            request(b"{}", "text/plain").json()
        self.assertEqual(raised.exception.status, 415)
        with self.assertRaises(ApiError) as raised:
            request(b" " * (1024 * 1024 + 1)).json()
        self.assertEqual(raised.exception.status, 413)

    def test_query_integer_boolean_are_typed_and_bounded(self):
        self.assertEqual(Request("GET", "/", {"x": "-12"}).integer("x"), -12)
        for raw in ("1.1", "true", "１２", "1e2", "2147483648", "9" * 20, " 1"):
            with self.subTest(raw=raw), self.assertRaises(ApiError):
                Request("GET", "/", {"x": raw}).integer("x")
        self.assertTrue(Request("GET", "/", {"x": "True"}).boolean("x"))
        with self.assertRaises(ApiError):
            Request("GET", "/", {"x": "yes"}).boolean("x")

    def test_cookies_and_error_messages_do_not_expose_internal_exception(self):
        token = "A" * 64
        self.assertEqual(Request("GET", "/", headers={"cookie": f"{COOKIE}={token}"}).token, token)
        self.assertEqual(Request("GET", "/").token, "")
        error = safe_error(RuntimeError("password=secret;SQL SELECT company_data"))
        self.assertEqual(error.status, 500)
        self.assertNotIn("secret", str(error.payload))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tech = User("tech", "1", "تکنسین", "technician", "برق")
        self.auth = SimpleNamespace(authenticate=Mock(return_value=self.tech))
        self.services = SimpleNamespace(auth=self.auth, **{name: Mock() for name in ("master", "work", "pm", "registry", "setup", "performance", "reporting", "consumption", "backup", "db")})
        self.config = SimpleNamespace(database=SimpleNamespace(provider="postgresql"), seed_directory="unused", company_name="پاک", local_test_mode=False)
        self.api = Api(self.services, self.config)

    def test_every_unmarked_route_denies_technician_before_service(self):
        checked = 0
        for route in self.api.routes:
            if route.public or route.registration:
                continue
            pattern = route.pattern.pattern.removesuffix(r"\Z")
            path = pattern.replace(r"(?P<id>[^/]{1,100})", "item").replace("\\", "")
            with self.subTest(method=route.method, path=path), self.assertRaises(ApiError) as raised:
                self.api.dispatch(Request(route.method, path))
            self.assertEqual(raised.exception.status, 403)
            checked += 1
        self.assertGreater(checked, 40)

    def test_unknown_api_is_denied_for_technician_and_404_for_head(self):
        with self.assertRaises(ApiError) as raised:
            self.api.dispatch(Request("GET", "/api/future-management-page"))
        self.assertEqual(raised.exception.status, 403)
        self.auth.authenticate.return_value = User("head", "2", "رئیس", "head")
        with self.assertRaises(ApiError) as raised:
            self.api.dispatch(Request("GET", "/api/future-management-page"))
        self.assertEqual(raised.exception.status, 404)

    def test_health_is_public_and_honest_about_development_status(self):
        self.auth.authenticate.return_value = None
        result = self.api.dispatch(Request("GET", "/api/health"))
        self.assertFalse(result.payload["productionAccepted"])
        self.assertEqual(result.payload["apiVersion"], 7)
        self.assertEqual(result.payload["schemaVersion"], 10)
        self.auth.authenticate.assert_not_called()

    def test_catalog_closures_bind_separate_kinds_and_tech_can_get_own_tasks(self):
        for kind in ("assets", "parts", "people"):
            self.api.dispatch(Request("GET", "/api/catalog/" + kind, {"q": "پمپ"}))
            self.services.master.catalog.assert_called_with(self.tech, kind, {"q": "پمپ"})
        self.api.dispatch(Request("GET", "/api/pm/tasks", {"page": "2", "pageSize": "25"}))
        self.services.pm.tasks.assert_called_with(self.tech, 2, 25, False, None)

    def test_cookie_is_http_only_same_site_and_secure_when_tls(self):
        value = Api.cookie("A" * 64, True)[0][1]
        for part in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/", "Max-Age=28800"):
            self.assertIn(part, value)
        deleted = Api.cookie("", True, True)[0][1]
        self.assertIn("Max-Age=0", deleted)
        self.assertIn("Expires=Thu, 01 Jan 1970", deleted)


class ConfigurationStaticTests(Fixture):
    def test_config_refuses_public_listener_different_modes_and_production_sqlite(self):
        arguments = dict(database=self.config.database, listen_url="http://8.8.8.8:5081", frontend_directory=self.config.frontend_directory,
                         seed_directory=self.config.seed_directory, backup_directory=self.config.backup_directory, development=True)
        with self.assertRaises(ValueError):
            AppConfig(**arguments)
        arguments["listen_url"] = "https://127.0.0.1:5914"
        arguments["development"] = False
        with self.assertRaises(ValueError):
            AppConfig(**arguments)
        self.assertEqual(self.config.request_policy.authorities, frozenset(("127.0.0.1:5914",)))

    def test_static_paths_only_read_local_ui_and_deny_source_or_traversal(self):
        root = Path(self.config.frontend_directory)
        root.mkdir()
        (root / "index.html").write_text("<html>پاک</html>", encoding="utf-8")
        (root / "app.js").write_text("fetch('/api/auth/me')", encoding="utf-8")
        (root / "secrets.toml").write_text("private=1", encoding="utf-8")
        self.assertEqual(static_path(root, "/"), root / "index.html")
        self.assertEqual(static_path(root, "/app.js"), root / "app.js")
        for path in ("/../synthetic.db", "/..\\synthetic.db", "/C:/secret", "/secrets.toml", "/app.js.map"):
            with self.subTest(path=path), self.assertRaises((ApiError, NetworkPolicyError)):
                static_path(root, path)

    def test_factory_no_dependency_install_or_database_fallback(self):
        with patch.dict("sys.modules", {"fastapi": None}):
            with self.assertRaises(DatabaseDependencyError):
                create_app(self.config)
        self.db.verify_schema()

    def test_disabled_scheduler_does_not_issue_anything(self):
        services = SimpleNamespace(pm=Mock(), work=Mock())
        self.assertEqual(Scheduler(services, False).tick(), 0)
        services.pm.generate.assert_not_called()
        services.work.generate_pm.assert_not_called()


class BackupTests(Fixture):
    def test_consistent_snapshot_integrity_hash_and_source_unchanged(self):
        with self.db.transaction() as c:
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('backup-a','SYN-B','آزمایش','','','{}')")
        backup = BackupService(self.db, self.config.backup_directory,
            dump_executable=self.config.postgresql_dump_executable, restore_executable=self.config.postgresql_restore_executable)
        result = backup.create_snapshot()
        self.assertEqual(result.sha256, hashlib.sha256(result.path.read_bytes()).hexdigest())
        self.assertEqual(result.size, result.path.stat().st_size)
        self.assertTrue(result.path.read_bytes().startswith(b"PGDMP"))
        assert_database_constraints(self, self.db)
        with self.db.transaction(write=False) as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM assets WHERE id='backup-a'"), 1)
        self.assertFalse(list(Path(self.config.backup_directory).glob("*.partial")))

    def test_concurrent_snapshot_refused_and_postgres_is_never_file_copied(self):
        backup = BackupService(self.db, self.config.backup_directory,
            dump_executable=self.config.postgresql_dump_executable, restore_executable=self.config.postgresql_restore_executable)
        backup._lock.acquire()
        try:
            with self.assertRaises(ApiError) as raised:
                backup.create_snapshot()
            self.assertEqual(raised.exception.status, 409)
        finally:
            backup._lock.release()
        fake = SimpleNamespace(config=self.db.config)
        with self.assertRaises(ApiError) as missing_tools:
            BackupService(fake, self.config.backup_directory).create_snapshot()
        self.assertEqual(missing_tools.exception.status, 503)
        self.assertFalse(list(Path(self.config.backup_directory).glob("*.dump")))


class ExportTests(unittest.TestCase):
    def test_csv_escapes_formulas_quotes_newlines_and_preserves_numbers(self):
        result = csv_line(["=HYPERLINK(\"https://external\")", "  +SUM(1)", "@cmd", "normal,quoted\ntext", -3, Decimal("0.10")]).decode()
        self.assertIn("'=HYPERLINK", result)
        self.assertIn("'  +SUM", result)
        self.assertIn("'@cmd", result)
        self.assertIn('-3,0.10\r\n', result)

    def test_stream_limit_releases_on_disconnect_or_deadline(self):
        streams = [ExportStream(["h"], iter([["x"]])) for _ in range(4)]
        try:
            with self.assertRaises(ApiError) as raised:
                ExportStream(["h"], iter([]))
            self.assertEqual(raised.exception.status, 429)
        finally:
            for stream in streams:
                stream.close()
        clock = Mock(side_effect=[0, 601])
        timed = ExportStream(["h"], iter([]), clock=clock)
        with self.assertRaises(TimeoutError):
            next(timed)
        fresh = ExportStream(["h"], iter([["x"]]))
        self.assertEqual(list(fresh), [b"\xef\xbb\xbf", b"h\r\n", b"x\r\n"])
        fresh.close()


class SessionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.password = "synthetic-password-123"
        self.hash = hash_password(self.password)
        with self.db.transaction() as c:
            for id, role in (("admin", "admin"), ("tech", "technician")):
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:id,:role,'برق',1,:hash,0)", {"id": id, "role": role, "hash": self.hash})
        self.auth = Authentication(self.db)

    def test_login_digest_epoch_and_change_password_invalidate_all_sessions(self):
        user, token = self.auth.login("127.0.0.1", "tech", self.password)
        _, second = self.auth.login("127.0.0.1", "tech", self.password)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM sessions WHERE id=:id", {"id": token_digest(token)}), 1)
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM sessions WHERE id=:id", {"id": token}), 0)
        self.assertEqual(self.auth.authenticate(token).id, "tech")
        self.auth.change_password(user, self.password, "replacement-password-456")
        self.assertIsNone(self.auth.authenticate(token))
        self.assertIsNone(self.auth.authenticate(second))
        with self.db.connect() as c:
            row = c.one("SELECT * FROM users WHERE id='tech'")
        self.assertEqual(row["session_epoch"], 1)
        self.assertTrue(verify_password("replacement-password-456", row["password_hash"]))

    def test_revoked_admin_cannot_reset_after_pretransaction_authentication(self):
        admin, token = self.auth.login("127.0.0.1", "admin", self.password)
        with self.db.transaction() as c:
            c.execute("UPDATE users SET session_epoch=1 WHERE id='admin'")
        with self.assertRaises(ApiError) as raised:
            self.auth.reset_password(admin, "tech", "replacement-password-456")
        self.assertEqual(raised.exception.status, 401)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT password_hash FROM users WHERE id='tech'"), self.hash)


if __name__ == "__main__":
    unittest.main()
