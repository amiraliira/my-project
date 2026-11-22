"""Session/password compatibility and bounded login work; no hidden accounts."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import secrets
import threading
import time

from .common import ApiError, User, audit, iso_utc, now

COOKIE = "pak_session"
ITERATIONS = 600_000
ROLES = frozenset({"technician", "supervisor", "planner", "head", "manager", "admin"})
_timing_salt = secrets.token_bytes(16)


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 200:
        raise ApiError(400, "رمز جدید باید بین ۱۲ تا ۲۰۰ نویسه باشد.")
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS, dklen=32)
    return "pbkdf2-sha256$600000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(value).decode()


def _parse_hash(encoded: str):
    if not isinstance(encoded, str) or len(encoded) > 128:
        return None
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2-sha256" or iterations not in {"210000", "600000"}:
            return None
        salt = base64.b64decode(salt, validate=True)
        expected = base64.b64decode(expected, validate=True)
        if len(salt) != 16 or len(expected) != 32:
            return None
        return int(iterations), salt, expected
    except (ValueError, TypeError):
        return None


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 200:
        return False
    fields = _parse_hash(encoded)
    if fields is None:
        return False
    count, salt, expected = fields
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, count, dklen=32)
    return hmac.compare_digest(actual, expected)


def verify_login(password: str, encoded: str | None) -> bool:
    fields = _parse_hash(encoded) if encoded else None
    if fields is None:
        hashlib.pbkdf2_hmac("sha256", password.encode(), _timing_salt, ITERATIONS, dklen=32)
        return False
    count, salt, expected = fields
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, count, dklen=32)
    if count < ITERATIONS:
        hashlib.pbkdf2_hmac("sha256", password.encode(), _timing_salt, ITERATIONS - count, dklen=32)
    return hmac.compare_digest(actual, expected)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest().upper()


def require(user: User | None, *roles: str, registration: bool = False) -> User:
    if user is None or not user.active or user.role not in ROLES:
        raise ApiError(401, "ابتدا وارد سامانه شوید.")
    if user.role == "technician" and not registration:
        raise ApiError(403, "دسترسی تکنسین فقط به سامانه ثبت کار مجاز است.")
    if roles and user.role not in roles:
        raise ApiError(403, "این عملیات در سطح دسترسی شما نیست.")
    return user


def assert_live_actor(connection, user: User, *, lock: bool = True) -> User:
    """Recheck the account inside a mutation, holding its row until commit.

    A request can wait after session authentication while its account is reset,
    disabled or changes specialty. A User snapshot is not sufficient authority
    for a later write. PostgreSQL row locking orders the account change and the
    mutation; SQLite write transactions already serialize writers.
    """
    require(user, registration=True)
    suffix = " FOR UPDATE" if lock and not connection.is_sqlite else ""
    row = connection.one("SELECT role,specialty,active,session_epoch FROM users WHERE id=:id" + suffix,
                         {"id": user.id})
    if (not row or not row["active"] or row["role"] != user.role
            or row["specialty"] != user.specialty or row["session_epoch"] != user.session_epoch):
        raise ApiError(401, "حساب شما تغییر کرده است؛ دوباره وارد شوید.")
    return user


class LoginGuard:
    def __init__(self, *, window: float = 300, max_keys: int = 20_000,
                 max_account: int = 10, max_address: int = 120,
                 parallel_checks: int = 8, max_pending: int = 128,
                 wait_seconds: float = 10, clock=time.monotonic):
        self.window, self.max_keys = window, max_keys
        self.max_account, self.max_address = max_account, max_address
        self.max_pending, self.wait_seconds = max_pending, wait_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(parallel_checks)
        self._pending = 0
        self._next_prune = 0.0
        self._accounts, self._addresses = {}, {}

    @staticmethod
    def _busy():
        return ApiError(429, "تعداد درخواست ورود زیاد است؛ چند دقیقه بعد دوباره تلاش کنید.")

    def _bucket(self, entries, key, cutoff):
        queue = entries.get(key)
        if queue is None:
            if len(entries) >= self.max_keys:
                raise self._busy()
            queue = entries[key] = deque()
        while queue and queue[0] <= cutoff:
            queue.popleft()
        return queue

    @contextmanager
    def check(self, address: str, account: str):
        acquired = False
        with self._lock:
            if self._pending >= self.max_pending:
                raise self._busy()
            self._pending += 1
        try:
            instant = self._clock()
            cutoff = instant - self.window
            with self._lock:
                if instant >= self._next_prune:
                    for entries in (self._accounts, self._addresses):
                        for key in list(entries):
                            if not entries[key] or entries[key][-1] <= cutoff:
                                del entries[key]
                    self._next_prune = instant + min(60, self.window)
                user = self._bucket(self._accounts, token_digest(account), cutoff)
                remote = self._bucket(self._addresses, address, cutoff)
                if len(user) >= self.max_account or len(remote) >= self.max_address:
                    raise self._busy()
                user.append(instant)
                remote.append(instant)
            acquired = self._semaphore.acquire(timeout=self.wait_seconds)
            if not acquired:
                raise self._busy()
            yield
        finally:
            if acquired:
                self._semaphore.release()
            with self._lock:
                self._pending -= 1


class Authentication:
    def __init__(self, database, guard: LoginGuard | None = None):
        self.database = database
        self.guard = guard or LoginGuard()

    def authenticate(self, token: str | None) -> User | None:
        if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdefABCDEF" for c in token):
            return None
        with self.database.connect() as connection:
            row = connection.one(
                "SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id "
                "WHERE s.id=:id AND s.expires>:now AND s.epoch=u.session_epoch AND u.active=1",
                {"id": token_digest(token), "now": now()})
        return User.from_row(row) if row else None

    def login(self, address: str, code: str, password: str) -> tuple[User, str]:
        if not isinstance(code, str) or not isinstance(password, str) or not code.strip() or len(code.strip()) > 100 or len(password) > 200:
            raise ApiError(400, "ورودی ورود نامعتبر است.")
        code = code.strip()
        with self.guard.check(address, code):
            with self.database.connect() as connection:
                row = connection.one("SELECT * FROM users WHERE code=:code AND active=1", {"code": code})
            if not verify_login(password, row.get("password_hash") if row else None) or not row or row.get("role") not in ROLES:
                raise ApiError(401, "کد کاربری یا رمز اشتباه است.")
            user = User.from_row(row)
            token = secrets.token_hex(32).upper()
            expires = iso_utc(datetime.now(timezone.utc) + timedelta(hours=8))

            # Finish maintenance before taking the account lock. Keeping expired
            # session cleanup inside the account transaction can create lock
            # cycles with a concurrent password reset on another account.
            self.database.run_transaction(lambda c: c.execute("DELETE FROM sessions WHERE id IN (SELECT id FROM sessions WHERE expires<:now ORDER BY expires,id LIMIT 500)", {"now": now()}))

            def operation(connection):
                assert_live_actor(connection, user)
                lock = "" if connection.is_sqlite else " FOR UPDATE"
                valid = connection.one("SELECT id FROM users WHERE id=:id AND active=1 AND session_epoch=:epoch AND password_hash=:hash" + lock,
                                       {"id": user.id, "epoch": user.session_epoch, "hash": user.password_hash})
                if not valid:
                    raise ApiError(401, "حساب کاربری تغییر کرده است؛ دوباره وارد شوید.")
                connection.execute("INSERT INTO sessions(id,user_id,epoch,expires) VALUES(:id,:user,:epoch,:expires)",
                                   {"id": token_digest(token), "user": user.id, "epoch": user.session_epoch, "expires": expires})
                connection.execute("DELETE FROM sessions WHERE user_id=:user AND id<>:current AND id NOT IN (SELECT id FROM sessions WHERE user_id=:user AND id<>:current ORDER BY expires DESC,id DESC LIMIT 31)", {"user": user.id, "current": token_digest(token)})
                audit(connection, user, "login", "user", user.id, {})
            self.database.run_transaction(operation)
            return user, token

    def logout(self, user: User, token: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE id=:id AND user_id=:user", {"id": token_digest(token), "user": user.id})

    def change_password(self, user: User, current: str, new: str) -> None:
        if not verify_password(current, user.password_hash):
            raise ApiError(400, "رمز فعلی نادرست است.")
        self._replace_password(user, user.id, new, user.password_hash, user.session_epoch)

    def reset_password(self, actor: User, user_id: str, new: str) -> None:
        require(actor, "admin", "head")
        self._replace_password(actor, user_id, new)

    def _replace_password(self, actor: User, user_id: str, new: str,
                          old_hash: str | None = None, epoch: int | None = None):
        password_hash = hash_password(new)
        def operation(connection):
            # A reset locks actor and target in one stable order. Two admins
            # resetting each other must not acquire opposite account locks.
            lock = "" if connection.is_sqlite else " FOR UPDATE"
            if old_hash is None and not connection.is_sqlite:
                # Match user administration's last-active-admin lock order.
                connection.query("SELECT id FROM users WHERE role IN ('admin','head') AND active=1 ORDER BY id FOR UPDATE")
            connection.query("SELECT id FROM users WHERE id=:actor OR id=:target ORDER BY id" + lock,
                             {"actor": actor.id, "target": user_id})
            assert_live_actor(connection, actor, lock=False)
            if old_hash is None:
                require(actor, "admin", "head")
            suffix = " AND password_hash=:old AND session_epoch=:epoch" if old_hash is not None else ""
            count = connection.execute("UPDATE users SET password_hash=:hash,session_epoch=session_epoch+1 WHERE id=:id" + suffix,
                                       {"hash": password_hash, "id": user_id, "old": old_hash, "epoch": epoch})
            if count != 1:
                raise ApiError(409 if old_hash is not None else 404, "حساب کاربری تغییر کرده یا پیدا نشد.")
            connection.execute("DELETE FROM sessions WHERE user_id=:id", {"id": user_id})
            audit(connection, actor, "change-password" if old_hash is not None else "reset-password", "user", user_id, {})
        self.database.run_transaction(operation)
