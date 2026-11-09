"""One-command private acceptance copy with browser setup, never production.

Original snapshots/passwords are never opened for writing. The bundled payload
has no working passwords; a local operator explicitly chooses test credentials
in the browser. Setup endpoints do not exist in the normal server launcher.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import asyncio
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

from .application import compose, create_app
from .api import Request
from .common import ApiError, dumps, loads
from .configuration import AppConfig
from .database import DatabaseConfig
from .http_security import InternalOnlyMiddleware
from .local_postgres import LocalPostgres, LocalPostgresPathError
from .network_policy import validate_file_path, validate_internal_address, validate_port
from .security import hash_password

PASSWORD_MARKER = "!LOCAL_ACCEPTANCE_PASSWORD_REQUIRED!"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read_json(path, maximum=1024 * 1024):
    path = validate_file_path(str(path))
    if path.startswith("\\\\"):
        raise ValueError("Local state files cannot use a network share")
    with Path(path).open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Local state file exceeds its size limit")
    return json.loads(data.decode("utf-8"))


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".partial")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def checked_child(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Invalid payload path")
    components = relative.split("/")
    if any(part in ("", ".", "..") for part in components):
        raise ValueError("Invalid payload component")
    path = Path(root).joinpath(*components)
    validate_file_path(str(path))
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Payload path escapes its root")
    return path


def prepare_copy(root, data_directory):
    root = Path(validate_file_path(str(root)))
    directory = Path(validate_file_path(str(data_directory)))
    if str(root).startswith("\\\\") or str(directory).startswith("\\\\"):
        raise ValueError("Local acceptance files require local disk")
    if not (root / "frontend/index.html").is_file():
        raise ValueError("Extract the complete application before starting")
    payload = root / "test-payload"
    if directory.resolve().is_relative_to(payload.resolve()):
        raise ValueError("Mutable test files cannot be placed inside the bundled payload")
    manifest_path = payload / "acceptance-data.json"
    manifest = read_json(manifest_path)
    if (manifest.get("databaseProvider") != "postgresql" or manifest.get("databaseFormat") != "postgresql-custom"
            or manifest.get("schemaVersion") != 8):
        raise ValueError("The local application requires an explicit PostgreSQL schema-8 custom dump payload")
    source = checked_child(payload, manifest["databaseFile"])
    if digest(source) != manifest["databaseSha256"]:
        raise ValueError("Bundled test database hash does not match")
    with source.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("The bundled payload is not a PostgreSQL custom dump")
    if manifest.get("authentication", {}).get("passwordMarker") != PASSWORD_MARKER:
        raise ValueError("Payload is not a password-redacted local acceptance copy")
    for entry in manifest["seedFiles"]:
        path = checked_child(payload / "seed", entry["path"])
        if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError("Original PM file is incomplete or changed")
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    identity = digest(manifest_path)
    copy_marker = directory / "copy.json"
    if (directory / "acceptance.db").exists():
        raise ValueError("Legacy local data requires an explicit verified PostgreSQL migration; no SQLite fallback was opened")
    if copy_marker.exists():
        previous = read_json(copy_marker, 4096)
        if previous.get("payloadSha256") != identity or previous.get("databaseProvider") != "postgresql":
            raise ValueError("Existing test data belongs to a different package or is incomplete")
    else:
        if (directory / "postgresql").exists():
            raise ValueError("Unrecognized PostgreSQL data needs an explicit ownership check; it was not replaced")
        atomic_json(copy_marker, {"payloadSha256": identity, "databaseProvider": "postgresql", "independentTestCopy": True})
    return manifest


def test_config(root, directory, port, database):
    if not isinstance(database, DatabaseConfig) or database.provider != "postgresql" or database.host != "127.0.0.1":
        raise ValueError("The local application requires its explicit loopback PostgreSQL configuration")
    suffix = ".exe" if os.name == "nt" else ""
    return AppConfig(database=database,
        listen_url=f"http://127.0.0.1:{validate_port(port)}", frontend_directory=str(Path(root) / "frontend"),
        seed_directory=str(Path(root) / "test-payload/seed"), backup_directory=str(Path(directory) / "postgresql/backups"),
        postgresql_dump_executable=str(Path(root) / "postgres/bin" / ("pg_dump" + suffix)),
        postgresql_restore_executable=str(Path(root) / "postgres/bin" / ("pg_restore" + suffix)),
        development=True, scheduling_enabled=True, local_test_mode=True, automatic_backup_enabled=True,
        backup_interval_hours=24, backup_retention_count=14)


class TestSetupApp:
    def __init__(self, config, directory, *, instance_id=None, services=None):
        if not config.local_test_mode:
            raise ValueError("Browser test setup is forbidden in normal server mode")
        self.config = config
        self.directory = Path(directory)
        self.services = services or compose(config)
        self.application = create_app(config, services=self.services)
        self.instance_id = instance_id or secrets.token_hex(24)
        self._lock = threading.Lock()
        self._ready_path = self.directory / "credentials-ready.json"
        self.initialized = False
        with self.services.db.connect() as connection:
            self.initialized = bool(connection.scalar("SELECT COUNT(*) FROM audit WHERE id='local-test-credentials-v1' AND action='initialize-local-test' AND entity='local-test'"))
        self.setup = InternalOnlyMiddleware(self.dispatch_setup, policy=config.request_policy)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.application(scope, receive, send)
        path = scope.get("path", "")
        if not self.initialized or path.startswith("/api/test-setup/") or (path == "/api/health" and scope["method"] in ("GET", "HEAD")):
            return await self.setup(scope, receive, send)
        return await self.application(scope, receive, send)

    def accounts(self):
        with self.services.db.connect() as connection:
            return connection.query("SELECT code,name,role,specialty FROM users WHERE active=1 ORDER BY CASE role WHEN 'head' THEN 0 WHEN 'manager' THEN 1 WHEN 'admin' THEN 2 WHEN 'supervisor' THEN 3 WHEN 'technician' THEN 4 ELSE 5 END,name,code")

    def initialize(self, body):
        if not isinstance(body, dict) or set(body) != {"password", "passwordRepeat"}:
            raise ApiError(400, "اطلاعات رمز آزمایشی معتبر نیست.")
        password = body["password"]
        if not isinstance(password, str) or password != body["passwordRepeat"]:
            raise ApiError(400, "دو رمز واردشده یکسان نیستند.")
        # Validate before claiming the setup lock or opening a write transaction.
        hash_password(password)
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "آماده‌سازی در حال انجام است؛ کمی صبر کنید.")
        try:
            if self.initialized:
                raise ApiError(409, "رمز این نسخهٔ تست قبلاً تنظیم شده است؛ از صفحه ورود استفاده کنید.")
            with self.services.db.transaction() as connection:
                users = connection.query("SELECT id,session_epoch,password_hash FROM users")
                if not users:
                    raise ApiError(409, "حساب‌های نسخهٔ تست پیدا نشدند.")
                for user in users:
                    if user["password_hash"] != PASSWORD_MARKER or not 0 <= user["session_epoch"] < 2**63-1:
                        raise ApiError(409, "وضعیت حساب‌های کپی آزمایشی معتبر نیست.")
                    connection.execute("UPDATE users SET password_hash=:hash,session_epoch=session_epoch+1 WHERE id=:id",
                        {"hash": hash_password(password), "id": user["id"]})
                connection.execute("DELETE FROM sessions")
                connection.execute("INSERT INTO audit(id,actor_id,actor_name,action,entity,entity_id,details,created_at) VALUES('local-test-credentials-v1',NULL,'آماده‌سازی نسخه تست','initialize-local-test','local-test','credentials-v1',:details,:created)",
                    {"details": dumps({"independentTestCopy": True, "accountCount": len(users)}), "created": datetime.now(timezone.utc).isoformat()})
            self.initialized = True
            # The database audit checkpoint commits with the password update.
            # Losing this informational file cannot strand setup after a crash.
            try:
                atomic_json(self._ready_path, {"localAcceptanceCredentials": True, "accountCount": len(users),
                    "initializedAt": datetime.now(timezone.utc).isoformat()})
            except OSError:
                pass
            return {"initialized": True}
        finally:
            self._lock.release()

    async def respond(self, scope, send, status, payload, media_type="application/json; charset=utf-8"):
        data = dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", media_type.encode()), (b"content-length", str(len(data)).encode()),
            (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else data})

    async def dispatch_setup(self, scope, receive, send):
        try:
            if not validate_internal_address(scope["client"][0]).is_loopback:
                raise ApiError(403, "این نسخهٔ تست فقط روی همین کامپیوتر باز می‌شود.")
            path, method = scope.get("path", "/"), scope["method"]
            if path == "/api/health" and method in ("GET", "HEAD"):
                # The same React login renders first setup and ordinary sign-in.
                # Use the actual API contract; only this private wrapper adds state.
                result = self.application.state.api.dispatch(Request("GET", "/api/health"))
                return await self.respond(scope, send, result.status,
                    {**result.payload, "localTestInitialized": self.initialized})
            if not self.initialized and path == "/api/auth/me" and method in ("GET", "HEAD"):
                raise ApiError(401, "ابتدا رمز نسخهٔ تست را تعیین کنید.")
            if path == "/api/test-setup/state" and method in ("GET", "HEAD"):
                return await self.respond(scope, send, 200, {"initialized": self.initialized,
                    "busy": self._lock.locked(), "instanceId": self.instance_id})
            if path == "/api/test-setup/accounts" and method == "GET":
                if not self.initialized:
                    raise ApiError(409, "ابتدا رمز آزمایشی را تنظیم کنید.")
                return await self.respond(scope, send, 200, {"accounts": await asyncio.to_thread(self.accounts)})
            if path == "/api/test-setup/initialize" and method == "POST":
                headers = dict(scope["headers"])
                if headers.get(b"content-type", b"").split(b";",1)[0].strip().lower() != b"application/json":
                    raise ApiError(415, "نوع درخواست معتبر نیست.")
                message = await receive()
                try:
                    body = loads(message.get("body", b"").decode("utf-8"))
                except (UnicodeError, ValueError):
                    raise ApiError(400, "اطلاعات درخواست معتبر نیست.") from None
                result = await asyncio.to_thread(self.initialize, body)
                return await self.respond(scope, send, 200, result)
            if method in ("GET", "HEAD") and not path.startswith("/api/"):
                return await self.application(scope, receive, send)
            raise ApiError(409, "ابتدا رمز نسخهٔ تست را در صفحهٔ آماده‌سازی تنظیم کنید.")
        except ApiError as error:
            return await self.respond(scope, send, error.status, {"error": error.message})
        except Exception:
            return await self.respond(scope, send, 500, {"error": "آماده‌سازی انجام نشد؛ فایل گزارش شروع برنامه را بررسی کنید."})


@contextmanager
def instance_lock(directory):
    directory = Path(validate_file_path(str(directory)))
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    handle = (directory / "instance.lock").open("a+b")
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
            return
        yield True
    finally:
        handle.close()


def bind_listener(preferred=5187):
    validate_port(preferred)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", preferred))
        except OSError as error:
            if error.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.set_inheritable(False)
        return listener
    except BaseException:
        listener.close()
        raise


def open_browser(url):
    from .network_policy import validate_listener_url
    endpoint = validate_listener_url(url)
    if not endpoint.address.is_loopback:
        raise ValueError("A loopback URL is required")
    if os.name == "nt":
        os.startfile(endpoint.url)
    # No BROWSER environment, proxy, shell command or remote browser URL.


def announce(message):
    """Console feedback must never prevent the ready browser from opening."""
    try:
        print(message, flush=True)
    except (UnicodeError, OSError, ValueError):
        try:
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            sys.stdout.buffer.write((message + "\n").encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()
        except (AttributeError, OSError, ValueError):
            pass


def reopen_existing(directory, *, browser=True):
    record = read_json(Path(directory) / "instance.json", 4096)
    port = validate_port(record["port"])
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/api/test-setup/state")
        response = connection.getresponse()
        body = response.read(4097)
        if response.status != 200 or len(body) > 4096 or json.loads(body).get("instanceId") != record["instanceId"]:
            raise ValueError("The existing instance identity could not be verified")
    finally:
        connection.close()
    if browser:
        open_browser(f"http://127.0.0.1:{port}")
    return port


def launch_test(root, directory, *, browser=True, port=5187):
    root = Path(validate_file_path(str(root)))
    directory = Path(validate_file_path(str(directory)))
    if not directory.resolve().is_relative_to(root.resolve()) or directory.resolve().is_relative_to((root / "test-payload").resolve()):
        raise ValueError("Mutable acceptance files must stay inside this extracted application")
    if not (root / "test-payload/acceptance-data.json").is_file() or not (root / "frontend/index.html").is_file():
        raise ValueError("The complete acceptance package must be extracted first")
    with instance_lock(directory) as owned:
        if not owned:
            return reopen_existing(directory, browser=browser)
        manifest = prepare_copy(root, directory)
        postgres = LocalPostgres(root / "postgres", directory / "postgresql",
            checked_child(root / "test-payload", manifest["databaseFile"]),
            payload_sha256=digest(root / "test-payload/acceptance-data.json"))
        with postgres.running() as database:
            return serve_test(root, directory, database, browser=browser, port=port)


def serve_test(root, directory, database, *, browser=True, port=5187):
    listener = bind_listener(port)
    actual_port = listener.getsockname()[1]
    config = test_config(root, directory, actual_port, database)
    app = TestSetupApp(config, directory)
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=actual_port,
        proxy_headers=False, forwarded_allow_ips="", reload=False, workers=1,
        loop="asyncio", http="h11", ws="none", lifespan="on", limit_concurrency=128,
        backlog=128, timeout_keep_alive=5, timeout_graceful_shutdown=30,
        server_header=False, access_log=False, log_level="warning"))
    stopped = threading.Event()
    def ready():
        for _ in range(300):
            if stopped.wait(0.1):
                return
            if server.started:
                url = config.listen_url
                announce("\nبرنامه آماده است: " + url)
                announce("این پنجره را هنگام استفاده باز نگه دارید؛ بستن آن برنامه را متوقف می‌کند.")
                try:
                    atomic_json(directory / "instance.json", {"port": actual_port, "instanceId": app.instance_id})
                except OSError:
                    announce("ذخیرهٔ نشانی شروع انجام نشد؛ نشانی بالا را در مرورگر نگه دارید.")
                try:
                    if browser:
                        open_browser(url)
                except OSError:
                    announce("مرورگر خودکار باز نشد؛ نشانی بالا را در مرورگر باز کنید.")
                return
    worker = threading.Thread(target=ready, daemon=True, name="pak-test-browser")
    worker.start()
    try:
        server.run(sockets=[listener])
        if not server.started:
            raise RuntimeError("The local listener did not finish startup")
    finally:
        stopped.set()
        worker.join(timeout=2)
        listener.close()
    return actual_port


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Run the independent PAK local acceptance test")
    parser.add_argument("--root", required=True)
    parser.add_argument("--data-directory")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=5187)
    args = parser.parse_args(argv)
    directory = None
    phase = "configuration"
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        root = Path(validate_file_path(args.root))
        directory = Path(validate_file_path(args.data_directory or str(root / "test-data")))
        phase = "local-acceptance-startup"
        print("در حال بررسی و آماده‌سازی نسخهٔ مستقل تست…", flush=True)
        launch_test(root, directory, browser=not args.no_browser, port=args.port)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        text = str(error) if isinstance(error, LocalPostgresPathError) else "نسخهٔ تست اجرا نشد. فایل ZIP را کامل در یک پوشهٔ محلی استخراج کنید و «شروع تست.cmd» را اجرا کنید."
        if directory is not None and directory.exists():
            try:
                atomic_json(directory / "startup-error.json", {"phase": phase, "errorType": type(error).__name__,
                    "utc": datetime.now(timezone.utc).isoformat(), "message": text})
                text += "\nگزارش خطا در پوشهٔ test-data و فایل startup-error.json ذخیره شد."
            except OSError:
                pass
        print(text, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
