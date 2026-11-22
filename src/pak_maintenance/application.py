"""FastAPI adapter. Importing this module opens no sockets and changes no data.

Framework/driver dependencies must be provided offline before integration QA.
This factory never installs dependencies, migrates a database, seeds accounts,
configures the server, or falls back from PostgreSQL to SQLite.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from types import SimpleNamespace

from .api import Api, Request, safe_error
from .backup import BackupService
from .common import ApiError, dumps
from .configuration import AppConfig
from .database import Database, DatabaseDependencyError
from .http_security import InternalOnlyMiddleware
from .network_policy import validate_file_path

_log = logging.getLogger("pak_maintenance")


class ThreadAffineStream:
    """Run every iterator operation and close on the same bounded worker.

    A report holds a read transaction across its yields. A generic thread pool
    can switch workers between next() calls, violating SQLite's connection
    ownership, and can close a generator on a different thread at disconnect.
    ExportStream already limits the number of concurrent report iterators.
    """
    def __init__(self, stream):
        self.stream = stream
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pak-export")
        self._close_task = None

    def __aiter__(self):
        return self

    def _next(self):
        # StopIteration cannot cross an asyncio Future boundary.
        try:
            return True, next(self.stream)
        except StopIteration:
            return False, None

    async def __anext__(self):
        if self._close_task is not None:
            raise StopAsyncIteration
        try:
            available, value = await asyncio.get_running_loop().run_in_executor(self._worker, self._next)
        except BaseException:
            await self.aclose()
            raise
        if not available:
            await self.aclose()
            raise StopAsyncIteration
        return value

    async def _finish(self):
        try:
            # The worker queue ensures a pending next() finishes before close().
            # A cancelled Future cannot cancel SQL already running in a thread.
            await asyncio.get_running_loop().run_in_executor(self._worker, self.stream.close)
        finally:
            self._worker.shutdown(wait=False, cancel_futures=False)

    async def aclose(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish())
        # Keep cleanup alive even if the HTTP request's task is cancelled again.
        await asyncio.shield(self._close_task)


def compose(config):
    if not isinstance(config, AppConfig) or config.database.provider != "postgresql":
        raise ValueError("The running application requires an explicit PostgreSQL 17 or newer configuration")
    from .security import Authentication
    from .work_service import WorkService
    from .pm_service import PmService
    from .pm_registry import PmRegistry
    from .pm_setup import PmSetupService
    from .pm_archive import PmArchiveService
    from .master_data import MasterDataService
    from .parts_import import PartsImportService
    from .consumption_service import ConsumptionService
    from .performance_service import PerformanceService
    from .reporting_service import ReportingService
    from .oee_service import OeeService
    db = Database(config.database)
    work = WorkService(db)
    pm = PmService(db, work)
    work.pm_service = pm
    return SimpleNamespace(db=db, auth=Authentication(db), work=work, pm=pm,
        registry=PmRegistry(db), archive=PmArchiveService(db), setup=PmSetupService(db, pm, {"scheduling_enabled": config.scheduling_enabled}),
        master=MasterDataService(db, config.seed_directory), parts_import=PartsImportService(db), consumption=ConsumptionService(db),
        performance=PerformanceService(db), reporting=ReportingService(db, work), oee=OeeService(db),
        backup=BackupService(db, config.backup_directory, automatic_enabled=config.automatic_backup_enabled,
            interval_hours=config.backup_interval_hours, retention_count=config.backup_retention_count,
            dump_executable=config.postgresql_dump_executable, restore_executable=config.postgresql_restore_executable))


class Scheduler:
    """Serial software scheduler; occurrence keys also prevent cross-process duplication."""
    def __init__(self, services, enabled=True):
        self.services, self.enabled = services, enabled
        self._stop = asyncio.Event()

    def tick(self):
        if not self.enabled:
            return 0
        return self.services.pm.generate() + self.services.work.generate_pm()

    async def run(self):
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception as error:
                # Avoid credentials, personal fields and SQL parameters in logs.
                _log.error("PM scheduler failed (%s)", type(error).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except TimeoutError:
                pass

    def stop(self):
        self._stop.set()


def static_path(directory, request_path):
    root = Path(validate_file_path(str(directory)))
    if not root.is_dir():
        raise ApiError(503, "فایل‌های رابط برنامه در دسترس نیستند.")
    if any(c in request_path for c in ("\\", "\x00", ":")) or any(p in (".", "..") for p in request_path.split("/")):
        raise ApiError(404, "فایل پیدا نشد.")
    candidate = root / request_path.lstrip("/")
    validate_file_path(str(candidate))
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ApiError(404, "فایل پیدا نشد.")
    if request_path in ("", "/") or not candidate.suffix:
        candidate = root / "index.html"
    if candidate.suffix.lower() not in {".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".woff", ".woff2", ".ico", ".webmanifest"} or not candidate.is_file():
        raise ApiError(404, "فایل پیدا نشد.")
    validate_file_path(str(candidate))
    return candidate


def accepts_gzip(value):
    """Negotiate a prepared public asset without compressing user responses."""
    from decimal import Decimal, InvalidOperation
    qualities = {}
    for entry in value.lower().split(","):
        parts = [part.strip() for part in entry.split(";")]
        if not parts or parts[0] not in ("gzip", "*"):
            continue
        quality = Decimal(1)
        try:
            for parameter in parts[1:]:
                if not parameter.startswith("q="):
                    raise ValueError()
                quality = Decimal(parameter[2:])
            if not quality.is_finite() or not 0 <= quality <= 1:
                raise ValueError()
        except (InvalidOperation, ValueError):
            quality = Decimal(0)
        qualities[parts[0]] = quality
    return qualities.get("gzip", qualities.get("*", Decimal(0))) > 0


def create_app(config, *, services=None):
    if not isinstance(config, AppConfig):
        raise ValueError("An explicit validated AppConfig is required")
    try:
        from fastapi import FastAPI, Request as FrameworkRequest
        from starlette.responses import Response, FileResponse, StreamingResponse
        from starlette.concurrency import run_in_threadpool
    except ImportError:
        raise DatabaseDependencyError("FastAPI/Starlette dependencies are unavailable. Supply the reviewed offline dependency bundle before running integration tests.") from None

    services = services or compose(config)
    if services.db.config.provider != "postgresql":
        raise ValueError("HTTP application services must use PostgreSQL 17 or newer; SQLite services cannot be hosted")
    api = Api(services, config)

    class ClosingStreamingResponse(StreamingResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            except Exception as error:
                # Mid-stream failures cannot change the response status. Keep
                # database values out of the HTTP server's exception log.
                _log.error("Report stream failed (%s)", type(error).__name__)
                raise RuntimeError("Report stream aborted") from None
            finally:
                await self.body_iterator.aclose()

    @asynccontextmanager
    async def lifespan(app):
        # Verify existing data/schema. Never initialize/migrate/activate on startup.
        await asyncio.to_thread(services.db.verify_schema)
        # A fresh event per lifecycle also permits a clean repeated local test
        # lifecycle without keeping an already-stopped scheduler instance.
        scheduler = Scheduler(services, config.scheduling_enabled)
        task = asyncio.create_task(scheduler.run()) if scheduler.enabled else None
        services.backup.start_automatic()
        try:
            yield
        finally:
            await asyncio.to_thread(services.backup.stop_automatic)
            scheduler.stop()
            if task:
                await task

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan, redirect_slashes=False,
        telemetry={"tracing": False, "metrics": False, "logs": False,
                   "operation_spans": False, "auto_configure": False})
    app.add_middleware(InternalOnlyMiddleware, policy=config.request_policy)
    if config.reverse_proxy:
        from .http_security import TrustedReverseProxyMiddleware
        # Last-added Starlette middleware is outermost: validate the actual
        # socket peer before supplying trusted scheme/client to the boundary.
        app.add_middleware(TrustedReverseProxyMiddleware, public_url=config.listen_url)

    async def endpoint(request: FrameworkRequest):
        result = None
        try:
            query = {}
            for key, value in request.query_params.multi_items():
                if key in query:
                    raise ApiError(400, "فیلتر تکراری معتبر نیست.")
                query[key] = value
            incoming = Request(method=request.method, path=request.url.path, query=query,
                headers=dict(request.headers), body=await request.body(),
                address=request.client.host if request.client else "", secure=request.url.scheme == "https")
            result = await run_in_threadpool(api.dispatch, incoming)
            if result.file is not None:
                filename = result.filename or result.file.name
                if any(c in filename for c in "\r\n\x00"):
                    raise ApiError(500, "نام فایل خروجی معتبر نیست.")
                path = Path(validate_file_path(str(result.file)))
                if str(path).startswith("\\\\"):
                    raise ApiError(500, "مسیر فایل خروجی باید محلی باشد.")
                return FileResponse(path, status_code=result.status, filename=filename,
                                    media_type=result.media_type, headers=dict(result.headers))
            if result.stream is not None:
                return ClosingStreamingResponse(ThreadAffineStream(result.stream), status_code=result.status,
                    media_type=result.media_type, headers=dict(result.headers))
            data = b"" if result.status == 204 else result.binary if result.binary is not None else dumps(result.payload).encode("utf-8")
        except Exception as error:
            if result is not None and result.stream is not None:
                await run_in_threadpool(result.stream.close)
            result = safe_error(error)
            if result.status >= 500:
                _log.error("API request failed (%s)", type(error).__name__)
            data = dumps(result.payload).encode("utf-8")
        headers = dict(result.headers)
        if request.method == "HEAD" and result.status not in (204, 304):
            # HEAD has no body, but an advertised Content-Length describes
            # the same representation GET would have returned.
            headers["content-length"] = str(len(data))
        return Response(b"" if request.method == "HEAD" else data, status_code=result.status,
                        media_type=result.media_type, headers=headers)

    app.add_api_route("/api", endpoint, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    app.add_api_route("/api/{path:path}", endpoint, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)

    async def frontend(request: FrameworkRequest):
        try:
            path = await run_in_threadpool(static_path, config.frontend_directory, request.url.path)
            immutable = bool(__import__("re").fullmatch(r".+-[A-Za-z0-9_-]{8,}\.(js|css)", path.name))
            headers = {"cache-control": "public,max-age=31536000,immutable" if immutable else "no-cache"}
            if immutable and path.suffix in (".js", ".css"):
                headers["vary"] = "Accept-Encoding"
                compressed = path.with_name(path.name + ".gz")
                if accepts_gzip(request.headers.get("accept-encoding", "")) and compressed.is_file():
                    validate_file_path(str(compressed))
                    if compressed.is_symlink() or not compressed.resolve().is_relative_to(Path(config.frontend_directory).resolve()):
                        raise ApiError(404, "فایل پیدا نشد.")
                    headers["content-encoding"] = "gzip"
                    return FileResponse(compressed, media_type="text/javascript" if path.suffix == ".js" else "text/css", headers=headers)
            return FileResponse(path, headers=headers)
        except Exception as error:
            result = safe_error(error)
            return Response(dumps(result.payload).encode("utf-8"), status_code=result.status, media_type=result.media_type)
    if config.serve_frontend:
        app.add_api_route("/{path:path}", frontend, methods=["GET", "HEAD"], include_in_schema=False)
    app.state.services, app.state.api = services, api
    return app
