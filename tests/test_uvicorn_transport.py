"""Real loopback Uvicorn TCP/TLS tests using explicitly supplied QA certificates.

Set PAK_QA_TLS_DIRECTORY to the local synthetic TLS directory containing
root.pem, server.pem and server.key. No trust store, firewall, service, company
server, live database or Internet destination is changed. The socket is bound
by this fixture to a random loopback port before Uvicorn receives it.
"""
from dataclasses import replace
from http.client import HTTPConnection, HTTPSConnection
from http.cookies import SimpleCookie
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import threading
import time
import unittest
from uuid import uuid4

from pak_maintenance.api import Result
from pak_maintenance.application import compose, create_app
from pak_maintenance.configuration import AppConfig
from pak_maintenance.database import DatabaseConfig
from pak_maintenance.exports import ExportStream
from pak_maintenance.http_security import InternalOnlyMiddleware
from pak_maintenance.network_policy import validate_file_path
from pak_maintenance.security import COOKIE, hash_password
from pg_test_support import fresh_database, native_tool_paths

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "starlette", "uvicorn"))
if AVAILABLE:
    import uvicorn

PROJECT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT / "tests" / ".transport-fixtures"


@unittest.skipUnless(AVAILABLE, "Actual FastAPI/Starlette/Uvicorn dependencies are unavailable; TCP/TLS acceptance has not run")
class UvicornTransportTests(unittest.TestCase):
    password = "Synthetic-Transport-123!"

    @classmethod
    def setUpClass(cls):
        value = os.environ.get("PAK_QA_TLS_DIRECTORY")
        if not value:
            raise unittest.SkipTest("Explicit local synthetic TLS certificate directory is required; no certificate is downloaded or trusted globally")
        root = Path(validate_file_path(value))
        if str(root).startswith("\\\\"):
            raise ValueError("QA certificate directory must be local")
        cls.ca, cls.certificate, cls.key = (str(root / name) for name in ("root.pem", "server.pem", "server.key"))
        for path in (cls.ca, cls.certificate, cls.key):
            validate_file_path(path)
            if not Path(path).is_file():
                raise ValueError("Synthetic local QA TLS file is missing")
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.directory = FIXTURE_ROOT / uuid4().hex
        self.directory.mkdir(parents=True, mode=0o755)
        self.addCleanup(self.cleanup_directory)
        for name in ("frontend", "seed", "backups"):
            (self.directory / name).mkdir(mode=0o755)
        (self.directory / "frontend/index.html").write_text("<!doctype html><html lang='fa' dir='rtl'><body>پاک</body></html>", encoding="utf-8")
        database_config, self.owner = fresh_database(self, prefix="transport")
        listener = self.owned_socket()
        self.port = listener.getsockname()[1]
        self.base_url = f"https://127.0.0.1:{self.port}"
        self.config = AppConfig(database_config,
            self.base_url, str(self.directory / "frontend"), str(self.directory / "seed"),
            str(self.directory / "backups"), development=True, scheduling_enabled=False, **native_tool_paths())
        self.services = compose(self.config)
        with self.services.db.transaction() as c:
            c.execute("INSERT INTO users(id,code,name,role,specialty,active,password_hash,session_epoch) VALUES('admin','admin','Synthetic','admin','',1,:hash,0)", {"hash": self.password_hash})
            c.execute("INSERT INTO parts(id,code,name,specification,unit) VALUES('part','SYN-P','قطعه آزمایشی','','عدد')")
        self.inner_app = create_app(self.config, services=self.services)
        # The strict request policy wraps a real PostgreSQL-backed application.
        self.app = InternalOnlyMiddleware(self.inner_app, replace(self.config.request_policy, development=False))
        self.server, self.thread = self.start(self.app, listener, tls=True)
        self.context = ssl.create_default_context(cafile=self.ca)
        self.cookie = ""

    def owned_socket(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        self.addCleanup(listener.close)
        return listener

    def start(self, app, listener, *, tls):
        options = dict(app=app, host="127.0.0.1", port=listener.getsockname()[1],
            proxy_headers=False, forwarded_allow_ips="", loop="asyncio", http="h11", ws="none", lifespan="on",
            limit_concurrency=128, backlog=128, timeout_keep_alive=2, timeout_graceful_shutdown=5,
            access_log=False, server_header=False, log_config=None, log_level="error")
        if tls:
            options.update(ssl_certfile=self.certificate, ssl_keyfile=self.key)
        server = uvicorn.Server(uvicorn.Config(**options))
        failures = []
        def serve():
            try:
                server.run(sockets=[listener])
            except BaseException as error:
                failures.append(type(error).__name__)
        thread = threading.Thread(target=serve, name="pak-synthetic-uvicorn", daemon=True)
        thread.start()
        def stop():
            server.should_exit = True
            thread.join(8)
            if thread.is_alive():
                server.force_exit = True
                thread.join(2)
            if thread.is_alive():
                raise RuntimeError("Owned synthetic Uvicorn test thread did not stop")
            if failures:
                raise RuntimeError("Synthetic Uvicorn thread failed: " + ", ".join(failures))
        self.addCleanup(stop)
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            self.fail("Synthetic Uvicorn listener did not start")
        return server, thread

    def request(self, method, path, body=None, headers=None):
        values = {"host": f"127.0.0.1:{self.port}"}
        if self.cookie:
            values["cookie"] = self.cookie
        if body is not None:
            values.update({"x-app-request": "1", "content-type": "application/json"})
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        values.update(headers or {})
        connection = HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=5)
        try:
            connection.request(method, path, body=body, headers=values)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def login(self):
        status, headers, data = self.request("POST", "/api/auth/login", {"code": "admin", "password": self.password}, {"origin": self.base_url})
        self.assertEqual(status, 200, data)
        cookie = SimpleCookie()
        cookie.load(headers["set-cookie"])
        self.cookie = f"{COOKIE}={cookie[COOKIE].value}"
        return headers

    def cleanup_directory(self):
        resolved = self.directory.resolve()
        if not resolved.is_relative_to(FIXTURE_ROOT.resolve()) or resolved == FIXTURE_ROOT.resolve():
            raise RuntimeError("Refusing to remove a path outside synthetic transport fixtures")
        shutil.rmtree(resolved)
        if FIXTURE_ROOT.exists() and not any(FIXTURE_ROOT.iterdir()):
            FIXTURE_ROOT.rmdir()

    def test_verified_tls_health_headers_and_head_representation_length(self):
        status, headers, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertIs(json.loads(data)["productionAccepted"], False)
        self.assertEqual(headers["strict-transport-security"], "max-age=31536000")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertIn("connect-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("server", headers)
        status, head, body = self.request("HEAD", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(head["content-length"], str(len(data)))

    def test_tls_certificate_is_actually_verified_without_global_trust_changes(self):
        untrusted = ssl.create_default_context()
        connection = HTTPSConnection("127.0.0.1", self.port, context=untrusted, timeout=5)
        try:
            with self.assertRaises(ssl.SSLCertVerificationError):
                connection.request("GET", "/api/health")
        finally:
            connection.close()

    def test_tls_session_cookie_csrf_and_external_origins(self):
        headers = self.login()
        self.assertIn("Secure", headers["set-cookie"])
        self.assertIn("HttpOnly", headers["set-cookie"])
        self.assertIn("SameSite=Strict", headers["set-cookie"])
        self.assertEqual(self.request("GET", "/api/auth/me")[0], 200)
        denied = self.request("POST", "/api/auth/logout", headers={"x-app-request": "1", "origin": "https://external.invalid"})
        self.assertEqual(denied[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/logout")[0], 403)
        self.assertEqual(self.request("POST", "/api/auth/logout", headers={"x-app-request": "1"})[0], 200)
        self.assertEqual(self.request("GET", "/api/auth/me")[0], 401)

    def test_forged_proxy_headers_do_not_replace_actual_peer_or_tls(self):
        status, headers, data = self.request("GET", "/api/health", headers={"x-forwarded-for": "8.8.8.8", "x-forwarded-proto": "http", "forwarded": "for=8.8.8.8;proto=http"})
        self.assertEqual(status, 200, data)
        self.assertIn("strict-transport-security", headers)
        self.assertEqual(self.request("GET", "/api/health", headers={"host": "external.invalid"})[0], 400)

    def test_plain_http_login_screen_is_rejected_by_strict_production_policy(self):
        listener = self.owned_socket()
        self.start(self.app, listener, tls=False)
        connection = HTTPConnection("127.0.0.1", listener.getsockname()[1], timeout=5)
        try:
            # Logical Host is held fixed; actual scheme is plaintext even if a
            # caller supplies a forged TLS forwarding header.
            connection.request("GET", "/", headers={"host": f"127.0.0.1:{self.port}", "x-forwarded-proto": "https"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertIn("HTTPS", response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_tcp_csv_download_and_file_response_head(self):
        self.login()
        status, headers, data = self.request("GET", "/api/reports/consumption.csv")
        self.assertEqual(status, 200, data)
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"SYN-P", data)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(self.request("HEAD", "/api/backup")[0], 204)
        self.assertEqual(list((self.directory / "backups").iterdir()), [])
        status, headers, data = self.request("GET", "/api/backup")
        self.assertEqual(status, 200, data[:100])
        self.assertTrue(data.startswith(b"PGDMP"))
        self.assertIn("attachment", headers["content-disposition"])

    def test_real_tcp_disconnect_releases_stream_and_transaction(self):
        self.login()
        closed = threading.Event()
        owners = []
        def rows():
            try:
                with self.services.db.transaction(write=False) as connection:
                    owners.append(threading.get_ident())
                    self.assertEqual(connection.scalar("SELECT 1"), 1)
                    # Outgrow the socket send buffer with an active PG read.
                    for i in range(10000):
                        yield (i, "x" * 65536)
            finally:
                owners.append(threading.get_ident())
                closed.set()
        self.inner_app.state.api.add("GET", "/api/review-tcp-stream", lambda r, u: Result(stream=ExportStream(["n", "text"], rows()), media_type="text/csv"), roles=("admin",))
        connection = HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=5)
        response = None
        try:
            connection.request("GET", "/api/review-tcp-stream", headers={"cookie": self.cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read(128).startswith(b"\xef\xbb\xbf"))
        finally:
            if response is not None:
                response.close()
            connection.close()
        self.assertTrue(closed.wait(5), "Stream did not release its transaction after actual TCP disconnect")
        self.assertEqual(len(set(owners)), 1)
        slots = [ExportStream(["h"], iter([])) for _ in range(4)]
        for stream in slots:
            stream.close()


if __name__ == "__main__":
    unittest.main()
