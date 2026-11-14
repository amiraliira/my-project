"""Independent transport/configuration/session review using no HTTP framework.

These tests exercise real asyncio cancellation, iterator ownership, the pure
ASGI boundary, SQLite transactions and launcher arguments. They intentionally
do not stand in for missing FastAPI/Uvicorn/PostgreSQL integration acceptance.
"""
import asyncio
from dataclasses import replace
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from pak_maintenance.api import Api, Request
from pak_maintenance.application import ThreadAffineStream
from pak_maintenance.cli import launch
from pak_maintenance.common import ApiError, User
from pak_maintenance.http_security import InternalOnlyMiddleware, RequestPolicy
from pak_maintenance.security import Authentication, COOKIE, assert_live_actor
from test_api import Fixture


class StreamReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_transaction_and_close_stay_on_one_worker(self):
        operations = []
        def rows():
            connection = sqlite3.connect(":memory:")
            try:
                connection.execute("CREATE TABLE synthetic(value INTEGER)")
                connection.executemany("INSERT INTO synthetic VALUES(?)", ((1,), (2,), (3,)))
                for value, in connection.execute("SELECT value FROM synthetic ORDER BY value"):
                    operations.append(("next", threading.get_ident()))
                    yield str(value).encode()
            finally:
                operations.append(("close", threading.get_ident()))
                connection.close()
        stream = ThreadAffineStream(rows())
        self.assertEqual([chunk async for chunk in stream], [b"1", b"2", b"3"])
        await stream.aclose()
        self.assertEqual([name for name, _ in operations], ["next", "next", "next", "close"])
        self.assertEqual(len({worker for _, worker in operations}), 1)
        self.assertNotEqual(operations[0][1], threading.get_ident())

    async def test_disconnect_closes_generator_before_exhaustion(self):
        closed = []
        def rows():
            connection = sqlite3.connect(":memory:")
            try:
                yield b"one"
                yield b"two"
            finally:
                connection.close()
                closed.append(True)
        stream = ThreadAffineStream(rows())
        self.assertEqual(await anext(stream), b"one")
        await stream.aclose()
        await stream.aclose()
        self.assertEqual(closed, [True])
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_cancelled_pending_read_finishes_then_releases_resource(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        operations = []
        def rows():
            try:
                operations.append(threading.get_ident())
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("Test read did not release")
                yield b"one"
            finally:
                operations.append(threading.get_ident())
                closed.set()
        stream = ThreadAffineStream(rows())
        read = asyncio.create_task(anext(stream))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            read.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await read
            await stream.aclose()
            self.assertTrue(closed.is_set())
            self.assertEqual(len(set(operations)), 1)
        finally:
            release.set()
            await stream.aclose()

    async def test_failed_iterator_is_closed_without_leaking_export_slot(self):
        from pak_maintenance.exports import ExportStream
        closed = []
        def rows():
            try:
                raise RuntimeError("synthetic iterator failure")
                yield b"unreachable"
            finally:
                closed.append(True)
        stream = ThreadAffineStream(ExportStream(["head"], rows()))
        self.assertEqual(await anext(stream), b"\xef\xbb\xbf")
        self.assertEqual(await anext(stream), b"head\r\n")
        with self.assertRaises(RuntimeError):
            await anext(stream)
        self.assertEqual(closed, [True])
        slots = [ExportStream(["h"], iter([])) for _ in range(4)]
        for entry in slots:
            entry.close()


class AsgiReviewTests(unittest.IsolatedAsyncioTestCase):
    async def perform(self, *, authority="127.0.0.1", origin=None, path="/api/orders", scheme="https", headers=(), method="POST", messages=None):
        called, sent = [], []
        async def app(scope, receive, send):
            called.append(await receive())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        pending = iter(messages or [{"type": "http.request", "body": b""}])
        async def receive():
            return next(pending, {"type": "http.disconnect"})
        async def send(message):
            sent.append(message)
        pairs = [(b"host", authority.encode()), (b"x-app-request", b"1")] + list(headers)
        if origin is not None:
            pairs.append((b"origin", origin.encode()))
        scope = {"type": "http", "client": ("192.168.1.2", 1234), "scheme": scheme,
                 "method": method, "path": path, "headers": pairs}
        middleware = InternalOnlyMiddleware(app, RequestPolicy(frozenset(("127.0.0.1", "127.0.0.1:443"))))
        await middleware(scope, receive, send)
        return sent, called

    async def test_default_port_origins_are_equivalent_and_different_ports_are_denied(self):
        for authority, origin in (("127.0.0.1", "https://127.0.0.1:443"), ("127.0.0.1:443", "https://127.0.0.1")):
            sent, called = await self.perform(authority=authority, origin=origin)
            self.assertEqual(sent[0]["status"], 200)
            self.assertTrue(called)
        for origin in ("https://127.0.0.1:444", "https://127.0.0.1/", "https://localhost", "https://8.8.8.8"):
            sent, called = await self.perform(origin=origin)
            self.assertEqual(sent[0]["status"], 403)
            self.assertFalse(called)

    async def test_production_login_screen_and_forged_tls_proxy_header_refuse_http(self):
        for path in ("/", "/app.js", "/api/auth/login"):
            sent, called = await self.perform(path=path, scheme="http", headers=[(b"x-forwarded-proto", b"https")])
            self.assertEqual(sent[0]["status"], 403)
            self.assertFalse(called)

    async def test_duplicate_idempotency_or_content_type_is_rejected(self):
        for header in (b"x-idempotency-key", b"content-type", b"transfer-encoding"):
            sent, called = await self.perform(headers=[(header, b"one"), (header, b"two")])
            self.assertEqual(sent[0]["status"], 400)
            self.assertFalse(called)

    async def test_disconnect_before_complete_body_never_calls_domain(self):
        sent, called = await self.perform(messages=[{"type": "http.request", "body": b"{", "more_body": True}, {"type": "http.disconnect"}])
        self.assertEqual(sent, [])
        self.assertFalse(called)

    async def test_replayed_body_keeps_later_disconnect(self):
        scope = {"type": "http", "client": ("127.0.0.1", 1), "scheme": "http", "method": "GET", "path": "/api/health", "headers": [(b"host", b"127.0.0.1")]}
        pending = iter([{"type": "http.request", "body": b"one", "more_body": True}, {"type": "http.request", "body": b"two"}, {"type": "http.disconnect"}])
        received = []
        async def app(scope, receive, send):
            received.append(await receive())
            received.append(await receive())
        async def receive():
            return next(pending)
        async def send(message):
            pass
        await InternalOnlyMiddleware(app, RequestPolicy(frozenset(("127.0.0.1",)), development=True))(scope, receive, send)
        self.assertEqual(received, [{"type": "http.request", "body": b"onetwo", "more_body": False}, {"type": "http.disconnect"}])


class ConfigurationLauncherReviewTests(Fixture):
    def test_default_port_authorities_include_browser_omission(self):
        self.assertEqual(replace(self.config, listen_url="http://127.0.0.1:80").request_policy.authorities,
                         frozenset(("127.0.0.1", "127.0.0.1:80")))
        with self.assertRaises(ValueError):
            RequestPolicy(frozenset(("public.example",)), development=True)

    def test_launcher_disables_proxy_headers_reload_external_bind_and_access_log(self):
        # Argument construction only; no framework mock pretends to handle HTTP.
        app, uvicorn = object(), SimpleNamespace(run=Mock())
        with patch.dict("sys.modules", {"uvicorn": uvicorn}), patch("pak_maintenance.cli.create_app", return_value=app):
            launch(self.config)
        args, options = uvicorn.run.call_args
        self.assertEqual(args, (app,))
        for key, expected in {"host": "127.0.0.1", "port": 5914, "proxy_headers": False, "forwarded_allow_ips": "",
                "reload": False, "workers": 1, "limit_concurrency": 128, "ws": "none", "http": "h11", "access_log": False}.items():
            self.assertEqual(options[key], expected, key)

    def test_https_requires_local_explicit_certificate_and_key(self):
        configuration = replace(self.config, listen_url="https://127.0.0.1:5914")
        with self.assertRaises(ValueError):
            launch(configuration)
        certificate, private_key = self.directory / "test-cert.pem", self.directory / "test-key.pem"
        certificate.write_text("synthetic only", encoding="ascii")
        private_key.write_text("synthetic only", encoding="ascii")
        uvicorn = SimpleNamespace(run=Mock())
        with patch.dict("sys.modules", {"uvicorn": uvicorn}), patch("pak_maintenance.cli.create_app", return_value=object()):
            launch(configuration, certificate=str(certificate), private_key=str(private_key))
        self.assertEqual(uvicorn.run.call_args.kwargs["ssl_certfile"], str(certificate))
        self.assertEqual(uvicorn.run.call_args.kwargs["ssl_keyfile"], str(private_key))
        with self.assertRaises(ValueError):
            launch(configuration, certificate=r"\\10.0.0.1\private\cert.pem", private_key=str(private_key))
        with self.assertRaises(ValueError):
            launch(self.config, certificate=str(certificate), private_key=str(private_key))


class AuthorizationReviewTests(Fixture):
    def setUp(self):
        super().setUp()
        self.user = User("actor", "actor", "Synthetic", "supervisor", "برق")
        with self.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES('actor','actor','Synthetic','supervisor','برق',1,'unusable',0)")

    def test_in_transaction_authority_rejects_epoch_specialty_role_and_active_changes(self):
        for field, value in (("session_epoch", 1), ("specialty", "مکانیک"), ("role", "technician"), ("active", 0)):
            with self.subTest(field=field):
                with self.db.transaction() as c:
                    c.execute("UPDATE users SET active=1,session_epoch=0,specialty='برق',role='supervisor' WHERE id='actor'")
                    c.execute("UPDATE users SET " + field + "=:value WHERE id='actor'", {"value": value})
                with self.assertRaises(ApiError) as error:
                    self.db.run_transaction(lambda c: assert_live_actor(c, self.user))
                self.assertEqual(error.exception.status, 401)

    def test_postgres_actor_lock_is_requested_before_returning_authority(self):
        connection = SimpleNamespace(is_sqlite=False, one=Mock(return_value={"role": "supervisor", "specialty": "برق", "active": 1, "session_epoch": 0}))
        self.assertIs(assert_live_actor(connection, self.user), self.user)
        self.assertTrue(connection.one.call_args.args[0].endswith(" FOR UPDATE"))

    def test_login_rechecks_authority_after_password_work_before_creating_session(self):
        auth = Authentication(self.db)
        def revoke_during_password_work(password, encoded):
            with self.db.transaction() as c:
                c.execute("UPDATE users SET specialty='مکانیک' WHERE id='actor'")
            return True
        with patch("pak_maintenance.security.verify_login", side_effect=revoke_during_password_work):
            with self.assertRaises(ApiError) as error:
                auth.login("127.0.0.1", "actor", "synthetic")
        self.assertEqual(error.exception.status, 401)
        with self.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM sessions"), 0)

    def test_duplicate_authentication_cookie_fails_closed(self):
        request = Request("GET", "/api/auth/me", headers={"cookie": f"{COOKIE}={'A'*64}; other=1; {COOKIE}={'B'*64}"})
        self.assertEqual(request.token, "")

    def test_health_does_not_advertise_client_acceptance_before_integration(self):
        services = SimpleNamespace(auth=Mock(), **{name: Mock() for name in ("master", "work", "pm", "registry", "setup", "performance", "reporting", "consumption", "backup", "db")})
        health = Api(services, self.config).dispatch(Request("GET", "/api/health")).payload
        self.assertIn("workflow-inbox", health["capabilities"])
        self.assertIn("production-data-reconciliation", health["acceptancePending"])
        self.assertIn("participant-grading", health["implementedCapabilities"])

    def test_asset_history_keeps_legacy_array_and_paged_contracts(self):
        user = replace(self.user, role="head")
        services = SimpleNamespace(auth=SimpleNamespace(authenticate=Mock(return_value=user)),
            **{name: Mock() for name in ("master", "work", "pm", "registry", "setup", "performance", "reporting", "consumption", "backup", "db")})
        api = Api(services, self.config)
        api.dispatch(Request("GET", "/api/assets/asset/history"))
        services.work.list.assert_called_once_with(user, asset_id="asset")
        api.dispatch(Request("GET", "/api/assets/asset/history", {"page": "2"}))
        services.work.list_page.assert_called_once_with(user, page=2, page_size=None, asset_id="asset")
        api.dispatch(Request("GET", "/api/orders/order"))
        services.work.get.assert_called_once_with(user, "order", 1, 200)


if __name__ == "__main__":
    unittest.main()
