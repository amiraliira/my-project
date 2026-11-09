"""Consistent PostgreSQL custom archives and offline legacy SQLite snapshots.

Application backups use explicit native PostgreSQL tools, never database file
copies. The SQLite branch remains for offline import/maintenance fixtures only.
Automatic retention affects only unchanged files registered by this worker for
this database. Manual, unknown and modified files are never pruned.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4

from .common import ApiError
from .network_policy import validate_file_path


@dataclass(frozen=True, slots=True)
class Snapshot:
    path: Path
    size: int
    sha256: str
    created_at: datetime | None = None


_BACKUP_ERROR = "پشتیبان انجام نشد؛ مسیر، فضای دیسک و دسترسی پوشهٔ پشتیبان را بررسی کنید."
_REGISTRY_ERROR = "فهرست پشتیبان‌های خودکار معتبر نیست؛ هیچ نسخه‌ای حذف نشد. پوشهٔ پشتیبان را بررسی کنید."
_CLEANUP_ERROR = "پشتیبان جدید تهیه شد؛ حذف نسخه‌های قدیمی کامل نشد. فایل‌های تغییرکرده یا دارای مشکل دسترسی حذف نشده‌اند."


class BackupService:
    def __init__(self, database, directory, *, automatic_enabled=False, interval_hours=24, retention_count=14,
                 dump_executable=None, restore_executable=None):
        if type(automatic_enabled) is not bool:
            raise ValueError("Automatic backup flag must be boolean")
        if type(interval_hours) not in (int, float) or not 1 <= interval_hours <= 720:
            raise ValueError("Backup interval must be between 1 and 720 hours")
        if type(retention_count) is not int or not 1 <= retention_count <= 365:
            raise ValueError("Backup retention must be between 1 and 365 snapshots")
        self._postgresql = database.config.provider == "postgresql"
        self._dump_executable, self._restore_executable = dump_executable, restore_executable
        if automatic_enabled:
            if self._postgresql:
                from .postgres_backup import executable_path
                executable_path(dump_executable)
                executable_path(restore_executable)
            elif database.config.provider != "sqlite" or getattr(database.config, "development", False) is not True:
                raise ValueError("Offline SQLite snapshots require an explicit development fixture")
        self.database = database
        path = validate_file_path(str(directory))
        if path.startswith("\\\\"):
            raise ValueError("Application backups must use local disk")
        self.directory = Path(path)
        self._lock = threading.Lock()
        self._automatic_tick_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._automatic_enabled = automatic_enabled
        self._interval_hours = interval_hours
        self._retention_count = retention_count
        self._last_completed_at = None
        self._last_manual_completed_at = None
        self._last_error = None
        self._cleanup_error = None
        self._registered_count = 0
        self._running = False
        self._stop = threading.Event()
        self._worker = None
        source = getattr(database.config, "database", "")
        source_key = (f"postgresql://{database.config.host}:{database.config.port}/{source}" if self._postgresql
                      else os.path.normcase(os.path.abspath(source)) if source else "unconfigured")
        self._database_key = hashlib.sha256(source_key.encode("utf-8")).hexdigest()
        self._prefix = "pak-python-auto-" + self._database_key[:16] + "-"
        self._extension = ".dump" if self._postgresql else ".db"
        self._registry_kind = "pak-postgresql-automatic-backups" if self._postgresql else "pak-local-test-automatic-backups"
        self._owned_name = re.compile(re.escape(self._prefix) + r"\d{8}T\d{6}-[0-9a-f]{32}" + re.escape(self._extension) + r"\Z")
        self._registry_path = self.directory / (".pak-python-auto-" + self._database_key[:16] + ".json")
        self._registry_lock_path = self._registry_path.with_suffix(".lock")
        if automatic_enabled:
            try:
                self._record_registry_state(self._load_registry())
            except Exception:
                self._last_error = _REGISTRY_ERROR

    def status(self):
        with self._state_lock:
            completed = self._last_completed_at
            due = completed + timedelta(hours=self._interval_hours) if completed and self._automatic_enabled else None
            return {"enabled": self._automatic_enabled, "intervalHours": self._interval_hours,
                    "retentionCount": self._retention_count, "directory": str(self.directory), "running": self._running,
                    "lastCompletedAt": completed.isoformat() if completed else None,
                    "nextDueAt": due.isoformat() if due else None, "lastError": self._last_error,
                    "lastCleanupError": self._cleanup_error,
                    "lastManualCompletedAt": self._last_manual_completed_at.isoformat() if self._last_manual_completed_at else None,
                    "registeredAutomaticSnapshots": self._registered_count,
                    "manualAvailable": not self._postgresql or bool(self._dump_executable and self._restore_executable),
                    "method": "postgresql-custom-archive" if self._postgresql else "sqlite-offline-import-snapshot",
                    "minimumPostgreSQLVersion": 17 if self._postgresql else None,
                    "archiveDecodeVerified": self._postgresql,
                    "workerRunning": self._worker is not None and self._worker.is_alive(),
                    "prunesOnlyRegisteredUnchangedAutomaticSnapshots": True}

    def _local_path(self, path):
        candidate = Path(validate_file_path(str(path)))
        if candidate.resolve().parent != self.directory.resolve():
            raise ValueError("Backup path is outside the configured private directory")
        return candidate

    def _load_registry(self):
        path = self._local_path(self._registry_path)
        if not path.exists():
            return {"version": 1, "kind": self._registry_kind, "databaseKey": self._database_key,
                    "lastCompletedAt": None, "cleanupError": False, "snapshots": []}
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Automatic backup registry exceeds its bound")
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate registry key")
                result[key] = value
            return result
        registry = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
        if not isinstance(registry, dict) or registry.get("version") != 1 or registry.get("kind") != self._registry_kind or registry.get("databaseKey") != self._database_key:
            raise ValueError("Automatic backup registry identity does not match")
        entries = registry.get("snapshots")
        if not isinstance(entries, list) or len(entries) > 10000 or type(registry.get("cleanupError")) is not bool:
            raise ValueError("Automatic backup registry shape is invalid")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or self._owned_name.fullmatch(entry["file"]) is None or entry["file"] in seen:
                raise ValueError("Automatic backup basename is invalid")
            if type(entry.get("bytes")) is not int or entry["bytes"] < 1 or not isinstance(entry.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None:
                raise ValueError("Automatic backup size or hash is invalid")
            self._registry_time(entry.get("createdAt"))
            seen.add(entry["file"])
        completed = registry.get("lastCompletedAt")
        if completed is not None:
            self._registry_time(completed)
        return registry

    @staticmethod
    def _registry_time(value):
        if not isinstance(value, str):
            raise ValueError("Automatic backup timestamp is invalid")
        instant = datetime.fromisoformat(value)
        if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
            raise ValueError("Automatic backup timestamp must be UTC")
        return instant

    def _record_registry_state(self, registry):
        with self._state_lock:
            self._last_completed_at = self._registry_time(registry["lastCompletedAt"]) if registry["lastCompletedAt"] else None
            self._registered_count = len(registry["snapshots"])
            self._cleanup_error = _CLEANUP_ERROR if registry["cleanupError"] else None
            self._last_error = self._cleanup_error

    def _save_registry(self, registry):
        path = self._local_path(self._registry_path)
        temporary = self._local_path(self.directory / (path.name + "." + uuid4().hex + ".partial"))
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(registry, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            self._local_path(path)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                self._local_path(temporary).unlink()

    @contextmanager
    def _registry_exclusive(self):
        validate_file_path(str(self.directory))
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._local_path(self._registry_lock_path)
        with path.open("a+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            locked = False
            try:
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError:
                        raise ApiError(409, "پشتیبان خودکار دیگری در حال انجام است.") from None
                else:
                    import fcntl
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        raise ApiError(409, "پشتیبان خودکار دیگری در حال انجام است.") from None
                locked = True
                yield
            finally:
                if locked:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _hash_file(path, deadline):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Automatic backup cleanup deadline exceeded")
                digest.update(chunk)
        return digest.hexdigest()

    def _prune_registered(self, registry):
        ordered = sorted(registry["snapshots"], key=lambda row: (row["createdAt"], row["file"]), reverse=True)
        retained = ordered[:self._retention_count]
        failed = False
        deadline = time.monotonic() + 30
        for entry in ordered[self._retention_count:]:
            try:
                path = self._local_path(self.directory / entry["file"])
                if not path.exists():
                    continue
                before = path.stat()
                if not path.is_file() or before.st_size != entry["bytes"] or self._hash_file(path, deadline) != entry["sha256"]:
                    raise ValueError("Registered backup has changed")
                after = path.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError("Registered backup changed during verification")
                self._local_path(path).unlink()
            except Exception:
                retained.append(entry)
                failed = True
        registry["snapshots"] = retained
        registry["cleanupError"] = failed

    def _latest_registered_intact(self, registry):
        if not registry["snapshots"]:
            return False
        latest = max(registry["snapshots"], key=lambda row: (row["createdAt"], row["file"]))
        try:
            path = self._local_path(self.directory / latest["file"])
            return (path.is_file() and path.stat().st_size == latest["bytes"] and
                    self._hash_file(path, time.monotonic() + 30) == latest["sha256"])
        except Exception:
            return False

    def run_automatic_if_due(self):
        if not self._automatic_enabled or not self._automatic_tick_lock.acquire(blocking=False):
            return None
        try:
            with self._registry_exclusive():
                try:
                    registry = self._load_registry()
                except Exception:
                    with self._state_lock:
                        self._last_error = _REGISTRY_ERROR
                    raise
                self._record_registry_state(registry)
                completed = self._last_completed_at
                if completed is not None and datetime.now(timezone.utc) < completed + timedelta(hours=self._interval_hours) and self._latest_registered_intact(registry):
                    return None
                snapshot = self._create_snapshot(120, automatic=True)
                registry["snapshots"].append({"file": snapshot.path.name, "createdAt": snapshot.created_at.isoformat(),
                                              "bytes": snapshot.size, "sha256": snapshot.sha256})
                registry["lastCompletedAt"] = snapshot.created_at.isoformat()
                self._save_registry(registry)
                self._prune_registered(registry)
                self._save_registry(registry)
                self._record_registry_state(registry)
                return snapshot
        except ApiError as error:
            if error.status == 409:
                return None
            with self._state_lock:
                self._last_error = _BACKUP_ERROR
            raise
        except Exception:
            with self._state_lock:
                if self._last_error != _REGISTRY_ERROR:
                    self._last_error = _BACKUP_ERROR
            raise
        finally:
            self._automatic_tick_lock.release()

    def start_automatic(self):
        with self._state_lock:
            if not self._automatic_enabled or self._worker is not None and self._worker.is_alive():
                return False
            self._stop.clear()
            self._worker = threading.Thread(target=self._run_automatic, name="pak-database-backup", daemon=True)
            self._worker.start()
            return True

    def _run_automatic(self):
        # Match the previous app's startup grace period and five-minute retry.
        if self._stop.wait(15):
            return
        while not self._stop.is_set():
            try:
                self.run_automatic_if_due()
            except Exception:
                # create_snapshot records a safe, user-facing failure message.
                pass
            if self._stop.wait(300):
                return

    def stop_automatic(self, timeout=5):
        if type(timeout) not in (int, float) or not 0 <= timeout <= 30:
            raise ValueError("Invalid backup shutdown deadline")
        self._stop.set()
        with self._state_lock:
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout)
        return worker is None or not worker.is_alive()

    def create_snapshot(self, timeout=120):
        snapshot = self._create_snapshot(timeout, automatic=False)
        with self._state_lock:
            self._last_manual_completed_at = snapshot.created_at
        return snapshot

    def _create_snapshot(self, timeout, *, automatic):
        if self._postgresql and not (self._dump_executable and self._restore_executable):
            raise ApiError(503, "مسیر ابزارهای pg_dump و pg_restore در تنظیمات برنامه مشخص نشده است؛ مدیر سامانه باید هر دو ابزار PostgreSQL نسخهٔ ۱۷ یا بالاتر را معرفی کند.")
        if type(timeout) not in (int, float) or not 1 <= timeout <= 600:
            raise ValueError("Invalid backup deadline")
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "یک پشتیبان‌گیری دیگر در حال انجام است.")
        temporary = None
        with self._state_lock:
            self._running = True
        try:
            validate_file_path(str(self.directory))
            self.directory.mkdir(parents=True, exist_ok=True)
            # Recheck existing path components before file creation.
            validate_file_path(str(self.directory))
            created = datetime.now(timezone.utc)
            name = (self._prefix if automatic else "pak-postgresql-manual-" if self._postgresql else "pak-python-test-") + created.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
            temporary = self._local_path(self.directory / (name + ".partial"))
            destination = self._local_path(self.directory / (name + self._extension))
            deadline = time.monotonic() + timeout
            def progress(status, remaining, total):
                if self._worker is threading.current_thread() and self._stop.is_set():
                    raise ApiError(503, "پشتیبان‌گیری هنگام توقف برنامه لغو شد.")
                if time.monotonic() >= deadline:
                    raise ApiError(503, "زمان پشتیبان‌گیری به پایان رسید؛ دوباره تلاش کنید.")
            # Exclusive creation prevents accidental overwrite even on collision.
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            if self._postgresql:
                from .postgres_backup import create_archive
                create_archive(self.database, temporary, dump_executable=self._dump_executable,
                               restore_executable=self._restore_executable, deadline=deadline,
                               cancelled=lambda: self._worker is threading.current_thread() and self._stop.is_set())
            else:
                with self.database.connect() as source:
                    target = sqlite3.connect(temporary)
                    try:
                        source.raw.backup(target, pages=256, progress=progress, sleep=0.01)
                        target.execute("PRAGMA foreign_keys=ON")
                        if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                            raise ApiError(500, "صحت پشتیبان تأیید نشد.")
                        if target.execute("PRAGMA foreign_key_check").fetchone() is not None:
                            raise ApiError(500, "ارتباط اطلاعات پشتیبان معتبر نیست.")
                        target.commit()
                    finally:
                        target.close()
            digest = hashlib.sha256()
            with temporary.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    progress(None, None, None)
                    digest.update(chunk)
            digest = digest.hexdigest()
            temporary.rename(destination)
            temporary = None
            snapshot = Snapshot(destination, destination.stat().st_size, digest, created)
            with self._state_lock:
                self._last_error = self._cleanup_error
            return snapshot
        except Exception:
            with self._state_lock:
                self._last_error = _BACKUP_ERROR
            raise
        finally:
            try:
                if temporary is not None and temporary.exists():
                    self._local_path(temporary).unlink()
            finally:
                with self._state_lock:
                    self._running = False
                self._lock.release()
