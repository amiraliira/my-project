"""Actual FastAPI/Starlette/httpx integration on disposable synthetic data.

ASGITransport exercises the real application and framework without opening a
listener, backed by a real isolated PostgreSQL database. These tests do not
claim Uvicorn TCP/TLS or company deployment acceptance.
Framework absence is an explicit skip, never a simulated HTTP success.
"""
import asyncio
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
import shutil
import threading
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from pak_maintenance.api import Result
from pak_maintenance.application import compose, create_app
from pak_maintenance.common import dumps
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig
from pak_maintenance.exports import ExportStream
from pak_maintenance.security import COOKIE, hash_password
from pg_test_support import fresh_database, native_tool_paths

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "starlette", "httpx"))
if AVAILABLE:
    import httpx

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT / "tests" / ".framework-fixtures"


@unittest.skipUnless(AVAILABLE, "Actual FastAPI/Starlette/httpx dependencies are unavailable; framework acceptance has not run")
class HttpFrameworkTests(unittest.IsolatedAsyncioTestCase):
    password = "Synthetic-Framework-123!"

    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password(cls.password)

    async def asyncSetUp(self):
        self.directory = FIXTURE_ROOT / uuid4().hex
        self.directory.mkdir(parents=True, mode=0o755)
        for name in ("frontend", "seed", "backups"):
            (self.directory / name).mkdir(mode=0o755)
        (self.directory / "frontend/index.html").write_text("<!doctype html><html lang='fa' dir='rtl'><body>پاک</body></html>", encoding="utf-8")
        (self.directory / "frontend/app-AbC12345.js").write_text("console.log('synthetic local ui')", encoding="utf-8")
        (self.directory / "frontend/private.toml").write_text("private='synthetic'", encoding="ascii")
        database_config, self.owner = fresh_database(self, prefix="framework")
        self.config = AppConfig(database_config,
            "http://127.0.0.1:5916", str(self.directory / "frontend"), str(self.directory / "seed"),
            str(self.directory / "backups"), development=True, scheduling_enabled=False, **native_tool_paths())
        self.services = compose(self.config)
        with self.services.db.transaction() as c:
            for identifier, role, specialty in (("tech", "technician", "برق"), ("supervisor", "supervisor", "برق"), ("head", "head", ""), ("admin", "admin", "")):
                c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES(:id,:id,:id,:role,:specialty,1,:hash,0)",
                          {"id": identifier, "role": role, "specialty": specialty, "hash": self.password_hash})
            c.execute("INSERT INTO assets(id,code,name,hall,line,body) VALUES('asset','SYN-A','دستگاه آزمایشی','سالن آزمایشی','','{}')")
            c.execute("INSERT INTO parts(id,code,name,specification,unit) VALUES('part','SYN-P','قطعه آزمایشی','','عدد')")
        self.app = create_app(self.config, services=self.services)
        self.lifecycle = self.app.router.lifespan_context(self.app)
        await self.lifecycle.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 1234)),
                                       base_url=self.config.listen_url)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifecycle.__aexit__(None, None, None)
        resolved = self.directory.resolve()
        if not resolved.is_relative_to(FIXTURE_ROOT.resolve()) or resolved == FIXTURE_ROOT.resolve():
            raise RuntimeError("Refusing to remove a path outside synthetic framework fixtures")
        shutil.rmtree(resolved)
        if FIXTURE_ROOT.exists() and not any(FIXTURE_ROOT.iterdir()):
            FIXTURE_ROOT.rmdir()

    async def login(self, identifier="tech"):
        response = await self.client.post("/api/auth/login", json={"code": identifier, "password": self.password},
            headers={"x-app-request": "1", "origin": self.config.listen_url})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["id"], identifier)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=Strict", response.headers["set-cookie"])
        self.assertIn(COOKIE, self.client.cookies)
        return response

    async def test_public_precompressed_asset_negotiation_and_head_keep_api_uncompressed(self):
        import gzip
        asset = self.directory / "frontend/app-AbC12345.js"
        raw = asset.read_bytes()
        compressed = gzip.compress(raw, mtime=0)
        asset.with_name(asset.name+".gz").write_bytes(compressed)
        response = await self.client.get("/app-AbC12345.js", headers={"accept-encoding":"br, gzip;q=0.8"})
        self.assertEqual(response.content, raw)
        self.assertEqual(response.headers["content-encoding"], "gzip")
        self.assertEqual(response.headers["content-length"], str(len(compressed)))
        self.assertEqual(response.headers["vary"], "Accept-Encoding")
        self.assertIn("immutable",response.headers["cache-control"])
        head = await self.client.head("/app-AbC12345.js",headers={"accept-encoding":"gzip"})
        self.assertEqual(head.content,b"")
        self.assertEqual(head.headers["content-length"],str(len(compressed)))
        for value in ("gzip;q=0, *;q=1", "identity", "gzip;q=NaN", "gzip;q=1.2", "gzip;q=broken"):
            response = await self.client.get("/app-AbC12345.js", headers={"accept-encoding":value})
            self.assertEqual(response.content,raw)
            self.assertNotIn("content-encoding",response.headers)
        response = await self.client.get("/api/health",headers={"accept-encoding":"gzip"})
        self.assertNotIn("content-encoding",response.headers)

    async def test_real_framework_health_head_headers_and_static_resources(self):
        health = await self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["apiVersion"], 7)
        self.assertEqual(health.json()["schemaVersion"], 10)
        self.assertEqual(health.json()["databaseProvider"], "postgresql")
        self.assertGreaterEqual(health.json()["databaseVersionNum"], 170000)
        self.assertEqual(health.json()["databaseVersion"], self.services.db.server_version)
        self.assertIn("postgresql17-native-backups", health.json()["capabilities"])
        self.assertFalse(health.json()["productionAccepted"])
        self.assertIn("workflow-inbox", health.json()["capabilities"])
        self.assertFalse(health.json()["productionAccepted"])
        self.assertEqual(health.headers["cache-control"], "no-store")
        self.assertEqual(health.headers["x-frame-options"], "DENY")
        self.assertIn("connect-src 'self'", health.headers["content-security-policy"])
        head = await self.client.head("/api/health")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers.get("content-length"), health.headers.get("content-length"))
        html = await self.client.get("/")
        self.assertEqual(html.status_code, 200)
        self.assertIn("پاک", html.text)
        self.assertEqual(html.headers["cache-control"], "no-cache")
        script = await self.client.get("/app-AbC12345.js")
        self.assertEqual(script.status_code, 200)
        self.assertEqual(script.headers["cache-control"], "public,max-age=31536000,immutable")
        self.assertEqual((await self.client.get("/private.toml")).status_code, 404)
        self.assertEqual((await self.client.get("/openapi.json")).status_code, 404)

    async def test_cookie_login_csrf_origin_query_duplicates_and_logout(self):
        denied = await self.client.post("/api/auth/login", json={"code": "tech", "password": self.password})
        self.assertEqual(denied.status_code, 403)
        await self.login()
        self.assertEqual((await self.client.get("/api/auth/me")).json()["user"]["id"], "tech")
        denied = await self.client.post("/api/auth/logout", headers={"x-app-request": "1", "origin": "https://external.invalid"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual((await self.client.get("/api/catalog/parts?page=1&page=2")).status_code, 400)
        for path in ("/api/users", "/api/dashboard", "/api/consumption", "/api/not-yet-defined"):
            self.assertEqual((await self.client.get(path)).status_code, 403, path)
        logout = await self.client.post("/api/auth/logout", headers={"x-app-request": "1"})
        self.assertEqual(logout.status_code, 200)
        self.assertIn("Max-Age=0", logout.headers["set-cookie"])
        self.assertEqual((await self.client.get("/api/auth/me")).status_code, 401)

    async def test_actual_transport_rejects_public_peers_and_duplicate_security_headers(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("8.8.8.8", 1234)), base_url=self.config.listen_url) as public:
            response = await public.get("/api/health", headers={"x-forwarded-for": "127.0.0.1"})
            self.assertEqual(response.status_code, 403)
        for headers in (("host", "127.0.0.1:5916", "host", "external.invalid"),
                        ("origin", self.config.listen_url, "origin", self.config.listen_url)):
            pairs = [(headers[0], headers[1]), (headers[2], headers[3])]
            response = await self.client.get("/api/health", headers=pairs)
            self.assertEqual(response.status_code, 400)

    async def test_actual_body_limits_and_strict_json_before_any_write(self):
        await self.login()
        for content, expected in ((b'{"title":"a","title":"b"}', 400),
                                  (b'{"quantity":NaN}', 400),
                                  (b" " * (1024 * 1024 + 1), 413)):
            response = await self.client.post("/api/orders", content=content,
                headers={"x-app-request": "1", "content-type": "application/json"})
            self.assertEqual(response.status_code, expected, response.text)
        with self.services.db.connect() as c:
            self.assertEqual(c.scalar("SELECT COUNT(*) FROM orders"), 0)

    async def test_json_domain_workflow_and_exact_csv_use_real_framework(self):
        await self.login()
        body = {"type": "EM", "assetId": "asset", "title": "تعمیر آزمایشی", "description": "بررسی اتصال HTTP واقعی", "workDate": "2026-10-11",
                "startTime": "08:00", "endTime": "09:00", "participants": [{"personId": "tech", "minutes": 60}],
                "parts": [{"partId": "part", "quantity": Decimal("1.000001")}], "checklist": []}
        response = await self.client.post("/api/orders", content=dumps(body),
            headers={"x-app-request": "1", "content-type": "application/json", "x-idempotency-key": "framework-work"})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        response = await self.client.post(f"/api/orders/{row['id']}/submit", json={"version": row["version"]}, headers={"x-app-request": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        await self.login("supervisor")
        response = await self.client.post(f"/api/orders/{row['id']}/decision", json={"version": row["version"], "decision": "approve", "grades": [{"personId": "tech", "coefficient": 0.75}]}, headers={"x-app-request": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        await self.login("head")
        response = await self.client.post(f"/api/orders/{row['id']}/decision", json={"version": row["version"], "decision": "approve", "participantScores": [{"personId": "tech", "coefficient": "3"}]}, headers={"x-app-request": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["score"], 2.25)
        csv = await self.client.get("/api/reports/consumption-details.csv")
        self.assertEqual(csv.status_code, 200, csv.text)
        self.assertTrue(csv.content.startswith(b"\xef\xbb\xbf"))
        self.assertIn("1.000001", csv.text)
        self.assertEqual(csv.headers["cache-control"], "no-store")
        self.assertEqual((await self.client.head("/api/reports/works.csv")).status_code, 204)

    async def test_actual_file_response_backup_head_and_download(self):
        await self.login("admin")
        head = await self.client.head("/api/backup")
        self.assertEqual(head.status_code, 204)
        self.assertEqual(head.content, b"")
        self.assertEqual(list((self.directory / "backups").iterdir()), [])
        response = await self.client.get("/api/backup")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b"PGDMP"))
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertEqual(response.headers["content-type"], "application/octet-stream")

    async def test_telemetry_is_explicitly_disabled_even_with_hostile_inherited_environment(self):
        from opentelemetry import trace, metrics, _logs
        inherited = {"FASTAPI_OTEL_AUTO_CONFIGURE": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "https://external.invalid",
                     "OTEL_PYTHON_TRACER_PROVIDER": "external-provider", "OTEL_PYTHON_METER_PROVIDER": "external-provider"}
        with patch.dict(os.environ, inherited), \
                patch.object(trace, "get_tracer_provider", side_effect=AssertionError("Tracer provider must not be consulted")), \
                patch.object(metrics, "get_meter_provider", side_effect=AssertionError("Meter provider must not be consulted")), \
                patch.object(_logs, "get_logger_provider", side_effect=AssertionError("Log provider must not be consulted")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS is prohibited")), \
                patch("socket.create_connection", side_effect=AssertionError("Outbound connections are prohibited")), \
                patch("socket.socket.connect", side_effect=AssertionError("Outbound connections are prohibited")):
            app = create_app(self.config, services=self.services)
            self.assertFalse(app._native_telemetry.enabled())
            for option in ("tracing", "metrics", "logs", "operation_spans", "auto_configure"):
                self.assertIs(app._telemetry[option], False)
            # Exercise FastAPI's ASGI lifespan wrapper too; the router context
            # alone bypasses its inherited-environment telemetry setup hook.
            inbound, outbound = asyncio.Queue(), asyncio.Queue()
            await inbound.put({"type": "lifespan.startup"})
            async def receive():
                return await inbound.get()
            async def send(message):
                await outbound.put(message)
            lifespan = asyncio.create_task(app({"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}, receive, send))
            self.assertEqual((await asyncio.wait_for(outbound.get(), 5))["type"], "lifespan.startup.complete")
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)), base_url=self.config.listen_url) as client:
                    response = await client.get("/api/health")
                    self.assertEqual(response.status_code, 200)
            finally:
                await inbound.put({"type": "lifespan.shutdown"})
                self.assertEqual((await asyncio.wait_for(outbound.get(), 5))["type"], "lifespan.shutdown.complete")
                await asyncio.wait_for(lifespan, 5)
        self.assertNotIn("opentelemetry.sdk", sys.modules)
        self.assertFalse(any(name.startswith("opentelemetry.exporter") for name in sys.modules))

    async def test_real_starlette_disconnect_closes_affine_transaction(self):
        await self.login("admin")
        disconnected = asyncio.Event()
        closed = threading.Event()
        owner = []
        def rows():
            try:
                with self.services.db.transaction(write=False) as connection:
                    owner.append(threading.get_ident())
                    self.assertEqual(connection.scalar("SELECT 1"), 1)
                    for i in range(1000):
                        yield (i,)
            finally:
                owner.append(threading.get_ident())
                closed.set()
        self.app.state.api.add("GET", "/api/review-stream", lambda r, u: Result(stream=ExportStream(["head"], rows()), media_type="text/csv"), roles=("admin",))
        scope = self.scope("/api/review-stream")
        initial = True
        async def receive():
            nonlocal initial
            if initial:
                initial = False
                return {"type": "http.request", "body": b""}
            await disconnected.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == "http.response.body" and b"0\r\n" in message.get("body", b""):
                disconnected.set()
                # Model a transport checkpoint so the simulated peer's receive
                # task can deliver disconnect before the entire CSV is emitted.
                await asyncio.sleep(0)
        # Connection/authentication and disconnect cleanup are separate bounds.
        # A busy QA host can spend several seconds before the first body; the
        # cleanup contract starts when the simulated peer actually disconnects.
        response = asyncio.create_task(self.app(scope, receive, send))
        try:
            await asyncio.wait_for(disconnected.wait(), timeout=15)
            await asyncio.wait_for(response, timeout=2)
        finally:
            if not response.done():
                response.cancel()
            await asyncio.gather(response, return_exceptions=True)
        self.assertTrue(await asyncio.to_thread(closed.wait, 2))
        self.assertEqual(len(set(owner)), 1)
        slots = [ExportStream(["h"], iter([])) for _ in range(4)]
        for stream in slots:
            stream.close()

    async def test_real_stream_send_failure_cleans_resources_and_masks_private_error(self):
        await self.login("admin")
        closed = threading.Event()
        def rows():
            try:
                yield ("one",)
            finally:
                closed.set()
        self.app.state.api.add("GET", "/api/review-stream-failure", lambda r, u: Result(stream=ExportStream(["h"], rows()), media_type="text/csv"), roles=("admin",))
        scope = self.scope("/api/review-stream-failure", spec_version="2.4")
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            if message["type"] == "http.response.body" and b"one" in message.get("body", b""):
                raise OSError("synthetic-sensitive-message")
        with self.assertRaises(RuntimeError) as error:
            await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        self.assertNotIn("synthetic-sensitive-message", str(error.exception))
        self.assertTrue(await asyncio.to_thread(closed.wait, 2))
        slots = [ExportStream(["h"], iter([])) for _ in range(4)]
        for stream in slots:
            stream.close()

    def scope(self, path, spec_version="2.3"):
        token = self.client.cookies[COOKIE]
        return {"type": "http", "asgi": {"version": "3.0", "spec_version": spec_version}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "server": ("127.0.0.1", 5916), "client": ("127.0.0.1", 1234),
            "headers": [(b"host", b"127.0.0.1:5916"), (b"cookie", f"{COOKIE}={token}".encode())]}


if __name__ == "__main__":
    unittest.main()
