"""Bound SQL, explicit transactions and closed connection lifetimes.

PostgreSQL 17 or newer is the sole application provider. SQLite remains an
offline import/maintenance and unit-fixture adapter, never an app fallback.
No schema, seed data, migration, or connection is created by
constructing a Database. PostgreSQL driver / SCRAM / TLS behavior must pass the
deployment acceptance tests before this adapter is used for company data.
"""

from __future__ import annotations

import importlib
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from .network_policy import (
    NetworkPolicyError,
    validate_database_host,
    validate_file_path,
    validate_internal_address,
    validate_port,
)


T = TypeVar("T")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}", re.ASCII)
_PARAMETER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)
_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", re.ASCII)
_RETRYABLE_ABORTS = frozenset(("40001", "40P01"))
_SQLITE_GUARDS = frozenset((
    "guard_pm_archive_correction_insert",
    "guard_pm_responsibility_transfer_insert", "guard_pm_responsibility_transfers_update", "guard_pm_responsibility_transfers_delete",
    "guard_participant_grading_policy_insert", "guard_participant_grading_policies_update", "guard_participant_grading_policies_delete",
    "guard_pm_archive_insert", "guard_pm_archive_update", "guard_pm_archive_delete",
    "guard_order_parts_units_insert", "guard_order_parts_units_update",
    "guard_movements_units_insert", "guard_movements_units_update",
    "guard_consumption_units_insert", "guard_consumption_history_update", "guard_consumption_history_delete",
    "guard_consumption_total_insert", "guard_consumption_total_update", "guard_consumption_total_delete",
    "guard_completed_scores_update", "guard_completed_scores_delete",
    "pak_pm_template_revision_no_update", "pak_pm_template_revision_no_delete",
    "pak_pm_plan_revision_no_update", "pak_pm_plan_revision_no_delete",
    "pak_pm_occurrence_no_delete", "pak_pm_occurrence_identity_no_update",
    "pak_pm_members_no_update", "pak_pm_members_no_delete", "pak_pm_setup_no_update", "pak_pm_setup_no_delete",
    "guard_approval_evidence_insert", "guard_participant_score_evidence_insert", "guard_consumption_evidence_insert",
    "guard_completed_score_evidence_insert", "guard_approvals_update", "guard_approvals_delete",
    "guard_participant_scores_update", "guard_participant_scores_delete", "guard_request_claims_update",
    "guard_request_claims_delete", "guard_audit_update", "guard_audit_delete",
    "guard_participant_head_score_evidence_insert", "guard_participant_head_scores_update", "guard_participant_head_scores_delete",
    "guard_pm_change_request_insert", "guard_pm_change_request_update", "guard_pm_change_request_delete",
))
_POSTGRESQL_GUARDS = frozenset((
    "guard_pm_archive_correction_insert",
    "guard_pm_responsibility_transfer_insert", "guard_pm_responsibility_transfers_history",
    "guard_participant_grading_policy_insert", "guard_participant_grading_policies_history",
    "guard_pm_archive_insert", "guard_pm_archive_history",
    "guard_consumption_history", "guard_consumption_total_delete", "guard_consumption_total_update", "guard_completed_scores_history",
    "pak_pm_template_revision_immutable", "pak_pm_plan_revision_immutable", "pak_pm_occurrence_no_delete",
    "pak_pm_occurrence_identity_guard", "pak_pm_members_immutable", "pak_pm_setup_immutable",
    "guard_approval_evidence_insert", "guard_participant_score_evidence_insert", "guard_consumption_evidence_insert",
    "guard_completed_score_evidence_insert", "guard_approvals_history", "guard_participant_scores_history",
    "guard_request_claims_history", "guard_audit_history",
    "guard_participant_head_score_evidence_insert", "guard_participant_head_scores_history", "guard_pm_change_request_history",
))
_REQUIRED_COLUMNS = {
    "pm_responsibility_transfers": frozenset(("id", "order_id", "order_version", "from_supervisor_id", "to_supervisor_id", "actor_id", "reason", "created_at")),
    "participant_grading_policies": frozenset(("order_id", "revision", "person_id", "specialty", "role_at_submission", "policy", "created_at")),
    "pm_submission_archive": frozenset(("id", "order_id", "workflow_revision", "template_id", "template_version", "plan_id", "plan_version", "asset_id", "performer_id", "supervisor_id", "submitted_by", "number", "title", "hall", "asset_code", "asset_name", "performer_name", "specialty", "work_date", "executed_at", "submitted_at", "body", "body_sha256", "evidence_source")),
    "users": frozenset(("id", "code", "role", "specialty", "active", "password_hash", "session_epoch")),
    "sessions": frozenset(("id", "user_id", "epoch", "expires")),
    "assets": frozenset(("id", "code", "name", "hall", "line", "body", "version")),
    "orders": frozenset(("id", "version", "workflow_revision", "status", "work_date", "body")),
    "parts": frozenset(("id", "code", "unit", "unit_cost", "version")),
    "participants": frozenset(("order_id", "person_id", "minutes", "specialty")),
    "order_parts": frozenset(("order_id", "part_id", "quantity_units")),
    "approvals": frozenset(("id", "order_id", "revision", "stage", "decision", "actor_id", "coefficient")),
    "participant_scores": frozenset(("order_id", "revision", "person_id", "coefficient", "approval_id")),
    "participant_head_scores": frozenset(("order_id", "revision", "person_id", "specialty", "coefficient", "actor_id", "approval_id", "created_at")),
    "completed_scores": frozenset(("order_id", "revision", "person_id", "supervisor_coefficient", "head_coefficient", "head_coefficient_exact", "score_raw", "score_cents", "source")),
    "pm_change_requests": frozenset(("id", "request_key", "requester_id", "operation", "entity_id", "base_version", "payload", "payload_hash", "status", "version", "reviewer_id", "decision_reason", "result", "created_at", "decided_at")),
    "request_claims": frozenset(("actor_id", "request_key", "order_id", "payload_hash")),
    "audit": frozenset(("id", "actor_id", "actor_name", "action", "entity", "entity_id", "details", "created_at")),
    "legacy_records": frozenset(("id", "body", "imported_at")),
    "part_consumptions": frozenset(("id", "order_id", "part_id", "approval_id", "quantity_units", "approved_at")),
    "part_consumption_totals": frozenset(("part_id", "quantity_units", "report_count")),
    "pm_templates": frozenset(("id", "version", "period", "mapping_status", "body")),
    "pm_template_revisions": frozenset(("template_id", "revision", "body", "actor_id", "created_at")),
    "pm_weekly_plans": frozenset(("id", "template_id", "version", "active", "mapping_confirmed", "start_date")),
    "pm_plan_revisions": frozenset(("plan_id", "revision", "body", "actor_id", "created_at")),
    "pm_weekly_occurrences": frozenset(("plan_id", "persian_year", "month", "week", "order_id", "plan_version", "template_version")),
    "pm_weekly_assets": frozenset(("order_id", "asset_id")),
    "pm_setup_requests": frozenset(("actor_id", "request_key", "payload_hash", "body", "created_at")),
    "plans": frozenset(("id", "asset_id", "interval_days", "next_due", "active", "version", "body")),
    "pm_occurrences": frozenset(("plan_id", "due_date", "order_id")),
    "stock_movements": frozenset(("id", "part_id", "quantity_units", "created_at")),
    "stock_request_claims": frozenset(("actor_id", "request_key", "movement_id", "payload_hash")),
}


class DatabaseConfigurationError(ValueError):
    """A database destination or configuration is not explicitly safe."""


class DatabaseDependencyError(RuntimeError):
    """The explicitly requested provider is not available; never fall back."""


class SchemaMismatchError(RuntimeError):
    """Existing schema does not match the service's required schema 9."""


class DatabaseCleanupError(RuntimeError):
    """A transaction could not be reliably rolled back or its connection closed."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    provider: str
    database: str
    host: str = "127.0.0.1"
    port: int = 5432
    user: str = ""
    password: str = field(default="", repr=False)
    sslmode: str = "verify-full"
    connect_timeout: int = 5
    development: bool = False
    sslrootcert: str | None = None
    busy_timeout_ms: int = 5000
    allow_loopback_plaintext: bool = False
    allow_lan_plaintext: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or self.provider.lower() not in ("sqlite", "postgresql"):
            raise DatabaseConfigurationError("The database provider must explicitly be sqlite or postgresql.")
        object.__setattr__(self, "provider", self.provider.lower())
        if type(self.development) is not bool:
            raise DatabaseConfigurationError("The development flag must be a boolean.")
        if type(self.allow_loopback_plaintext) is not bool:
            raise DatabaseConfigurationError("The loopback plaintext exception must be an explicit boolean.")
        if type(self.allow_lan_plaintext) is not bool:
            raise DatabaseConfigurationError("The LAN plaintext exception must be an explicit boolean.")
        if type(self.connect_timeout) is not int or not 1 <= self.connect_timeout <= 30:
            raise DatabaseConfigurationError("Database connect_timeout must be an integer from 1 to 30 seconds.")
        if type(self.busy_timeout_ms) is not int or not 1 <= self.busy_timeout_ms <= 60000:
            raise DatabaseConfigurationError("Database busy_timeout_ms must be an integer from 1 to 60000.")
        if self.provider == "sqlite":
            path = validate_file_path(self.database)
            if path.startswith("\\\\"):
                raise DatabaseConfigurationError("SQLite requires a local disk; never a network share.")
            object.__setattr__(self, "database", path)
            return

        object.__setattr__(self, "host", validate_database_host(self.host))
        validate_port(self.port)
        if not isinstance(self.database, str) or _IDENTIFIER.fullmatch(self.database) is None:
            raise DatabaseConfigurationError("PostgreSQL database must be an explicit simple database name, never conninfo.")
        if not isinstance(self.user, str) or _IDENTIFIER.fullmatch(self.user) is None:
            raise DatabaseConfigurationError("An explicit simple PostgreSQL user name is required.")
        if not isinstance(self.password, str) or not self.password or "\x00" in self.password:
            raise DatabaseConfigurationError("An explicit PostgreSQL password is required.")
        if self.sslmode == "disable":
            address = validate_internal_address(self.host)
            loopback_allowed = address.is_loopback and (self.development or self.allow_loopback_plaintext)
            lan_allowed = not address.is_loopback and self.allow_lan_plaintext
            if not (loopback_allowed or lan_allowed):
                raise DatabaseConfigurationError("TLS is disabled: loopback needs its explicit local exception; remote private LAN testing needs allow_lan_plaintext=true.")
            if self.sslrootcert is not None:
                raise DatabaseConfigurationError("A TLS root certificate cannot be configured while TLS is disabled.")
        elif self.sslmode == "verify-full":
            if self.sslrootcert is None:
                raise DatabaseConfigurationError("PostgreSQL verify-full requires an explicit local root certificate path.")
            path = validate_file_path(self.sslrootcert)
            if path.startswith("\\\\"):
                raise DatabaseConfigurationError("The TLS trust root must be on a local disk.")
            object.__setattr__(self, "sslrootcert", path)
        else:
            raise DatabaseConfigurationError("PostgreSQL requires verify-full TLS; weaker negotiation modes are prohibited.")


def compile_sql(sql: str, *, postgresql: bool) -> tuple[str, tuple[str, ...]]:
    """Compile only real ``:name`` bindings, preserving SQL lexical contexts.

    Single/double/backtick/bracket quoting, E-string escapes, PostgreSQL casts,
    line comments, nested block comments and dollar-quoted strings are handled.
    PostgreSQL percent literals are doubled for psycopg's binding protocol.
    This is a placeholder compiler, not a validator for untrusted SQL: query
    structure and identifiers must remain source constants.
    """
    if not isinstance(sql, str) or not sql.strip() or "\x00" in sql:
        raise ValueError("A nonempty SQL source string is required.")
    # Cache the immutable statement structure only. Bindings are checked and
    # allocated afresh by Connection._bindings for every execution.
    return _compile_sql_cached(sql, bool(postgresql))


@lru_cache(maxsize=1024)
def _compile_sql_cached(sql: str, postgresql: bool) -> tuple[str, tuple[str, ...]]:
    output: list[str] = []
    names: list[str] = []
    seen: set[str] = set()
    index = 0
    length = len(sql)

    def append(fragment: str) -> None:
        output.append(fragment.replace("%", "%%") if postgresql else fragment)

    while index < length:
        start = index
        character = sql[index]
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            append(sql[start:index])
            continue
        if sql.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                raise ValueError("An SQL block comment is not terminated.")
            append(sql[start:index])
            continue
        if character in ("'", '"', "`", "["):
            closing = "]" if character == "[" else character
            escape_string = character == "'" and index > 0 and sql[index - 1] in "eE" and (
                index < 2 or not (sql[index - 2].isalnum() or sql[index - 2] in "_$")
            )
            index += 1
            terminated = False
            while index < length:
                if escape_string and sql[index] == "\\":
                    index += 2
                elif sql[index] == closing:
                    index += 1
                    if index < length and sql[index] == closing:
                        index += 1
                    else:
                        terminated = True
                        break
                else:
                    index += 1
            if not terminated:
                raise ValueError("An SQL quoted value or identifier is not terminated.")
            append(sql[start:index])
            continue
        if character == "$" and (index == 0 or not (sql[index - 1].isalnum() or sql[index - 1] in "_$")):
            marker = _DOLLAR_QUOTE.match(sql, index)
            if marker:
                closing = sql.find(marker[0], marker.end())
                if closing < 0:
                    raise ValueError("An SQL dollar-quoted string is not terminated.")
                index = closing + len(marker[0])
                append(sql[start:index])
                continue
        if sql.startswith("::", index):
            append("::")
            index += 2
            continue
        if character == ":":
            binding = _PARAMETER.match(sql, index + 1)
            if binding:
                name = binding[0]
                if name not in seen:
                    seen.add(name)
                    names.append(name)
                output.append(f"%({name})s" if postgresql else ":" + name)
                index = binding.end()
                continue
        append(character)
        index += 1
    return "".join(output), tuple(names)


class Connection:
    """Small DB-API wrapper; cursors are closed after every operation."""

    __slots__ = ("raw", "is_sqlite")

    def __init__(self, raw: Any, *, is_sqlite: bool):
        self.raw = raw
        self.is_sqlite = is_sqlite

    def _statement(self, sql: str, parameters: Mapping[str, Any] | None):
        compiled, names = compile_sql(sql, postgresql=not self.is_sqlite)
        return compiled, self._bindings(names, parameters)

    def _bindings(self, names: tuple[str, ...], parameters: Mapping[str, Any] | None):
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping):
            raise TypeError("SQL parameters must be a mapping of bound names to values.")
        values = {}
        for name in names:
            if name not in parameters:
                raise ValueError(f"Missing SQL parameter: {name}")
            value = parameters[name]
            values[name] = str(value) if self.is_sqlite and isinstance(value, Decimal) else value
        return values

    @staticmethod
    def _row(cursor, row) -> dict[str, Any]:
        if isinstance(row, Mapping):
            return dict(row)
        names = [column.name if hasattr(column, "name") else column[0] for column in cursor.description]
        return dict(zip(names, row, strict=True))

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        statement, values = self._statement(sql, params)
        cursor = self.raw.cursor()
        try:
            cursor.execute(statement, values)
            return [self._row(cursor, row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def one(self, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        statement, values = self._statement(sql, params)
        cursor = self.raw.cursor()
        try:
            cursor.execute(statement, values)
            first = cursor.fetchone()
            if first is None:
                return None
            result = self._row(cursor, first)
            if cursor.fetchone() is not None:
                raise ValueError("A single-row query returned more than one row.")
            return result
        finally:
            cursor.close()

    def scalar(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        row = self.one(sql, params)
        if row is None:
            return None
        if len(row) != 1:
            raise ValueError("A scalar query returned more than one column.")
        return next(iter(row.values()))

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> int:
        statement, values = self._statement(sql, params)
        cursor = self.raw.cursor()
        try:
            cursor.execute(statement, values)
            return max(cursor.rowcount, 0)
        finally:
            cursor.close()

    def executemany(self, sql: str, rows: Iterable[Mapping[str, Any]]) -> int:
        statement, names = compile_sql(sql, postgresql=not self.is_sqlite)
        def bound_rows():
            for row in rows:
                yield self._bindings(names, row)
        cursor = self.raw.cursor()
        try:
            cursor.executemany(statement, bound_rows())
            return max(cursor.rowcount, 0)
        finally:
            cursor.close()


class Database:
    schema_version = 10

    def __init__(self, config: DatabaseConfig | Mapping[str, Any]):
        self.config = config if isinstance(config, DatabaseConfig) else DatabaseConfig(**dict(config))
        self.is_sqlite = self.config.provider == "sqlite"
        self._driver = None
        self._server_version_num = None

    @property
    def server_version_num(self):
        """Last server version validated during a successful PostgreSQL open."""
        return self._server_version_num

    @property
    def server_version(self):
        version = self._server_version_num
        return f"{version // 10000}.{version % 10000}" if version is not None else None

    def _open(self):
        if self.is_sqlite:
            # Match the existing release's WAL-reset corruption fix gate.
            # Construction never loads an alternate native library or downloads
            # an engine; an unsupported Python runtime must fail explicitly.
            version = sqlite3.sqlite_version_info
            patched = (
                version >= (3, 51, 3)
                or version[:2] == (3, 50) and version[2] >= 7
                or version[:2] == (3, 44) and version[2] >= 6
            )
            if not patched:
                raise DatabaseDependencyError("The SQLite runtime lacks the required WAL-reset corruption fix. Use an approved patched offline Python runtime.")
            # mode=rw makes a mistyped / missing data file fail instead of
            # silently initializing a second empty company database.
            uri = Path(self.config.database).as_uri() + "?mode=rw"
            raw = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=self.config.busy_timeout_ms / 1000)
            try:
                cursor = raw.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.execute(f"PRAGMA busy_timeout={self.config.busy_timeout_ms}")
                    cursor.execute("PRAGMA synchronous=FULL")
                finally:
                    cursor.close()
                try:
                    reporting = importlib.import_module(".reporting_service", package=__package__)
                except ModuleNotFoundError as error:
                    if error.name != f"{__package__}.reporting_service":
                        raise
                else:
                    reporting.ReportingService.register_functions(raw)
                # The offline correction-archive guard must compare the raw
                # JSON quantity token with exact stored micro-units. SQLite's
                # json_extract would first round it to a binary float.
                def archive_quantity_units(token):
                    from .common import ApiError, loads
                    from .quantity import units
                    try:
                        result = units(loads(token))
                        return result if result > 0 else None
                    except (ApiError, ArithmeticError, TypeError, ValueError):
                        return None
                raw.create_function('pak_archive_quantity_units', 1, archive_quantity_units, deterministic=True)
                return raw
            except BaseException:
                raw.close()
                raise

        inherited = sorted(key for key in os.environ if key.upper().startswith("PG"))
        if inherited:
            raise DatabaseConfigurationError("Inherited PG* environment configuration is prohibited; use only explicit validated service settings.")
        unsafe_tls_environment = {"OPENSSL_CONF", "OPENSSL_CONF_INCLUDE", "OPENSSL_MODULES", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        if any(key.upper() in unsafe_tls_environment for key in os.environ):
            raise DatabaseConfigurationError("Inherited TLS profile configuration is prohibited; use only the explicit local trust certificate.")
        if self._driver is None:
            try:
                self._driver = importlib.import_module("psycopg")
            except ImportError:
                raise DatabaseDependencyError(
                    "PostgreSQL requires the approved offline psycopg 3 package. Install its verified offline wheel; no SQLite fallback is permitted."
                ) from None
        # sslcertmode=disable was introduced in libpq 17. An older or unknown
        # native client must not silently read a profile's default client key.
        try:
            native_version = self._driver.pq.version()
        except (AttributeError, TypeError):
            raise DatabaseDependencyError("The PostgreSQL native client version cannot be verified; approved libpq 17 or newer is required.") from None
        if type(native_version) is not int or native_version < 170000:
            raise DatabaseDependencyError("Approved libpq 17 or newer is required to disable implicit client certificate lookup.")
        options = {
            "host": self.config.host,
            "hostaddr": self.config.host,
            "port": self.config.port,
            "dbname": self.config.database,
            "user": self.config.user,
            "password": self.config.password,
            "sslmode": self.config.sslmode,
            "connect_timeout": self.config.connect_timeout,
            "application_name": "PakMaintenance-Python",
            "client_encoding": "UTF8",
            "gssencmode": "disable",
            "sslcertmode": "disable",
            "require_auth": "scram-sha-256",
            "channel_binding": "require" if self.config.sslmode == "verify-full" else "disable",
            "ssl_min_protocol_version": "TLSv1.2",
            "options": "-c search_path=public -c standard_conforming_strings=on -c statement_timeout=15000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=30000",
            "autocommit": True,
        }
        if self.config.sslrootcert is not None:
            options["sslrootcert"] = validate_file_path(self.config.sslrootcert)
            # Explicit optional, local CRL next to the configured CA. Never
            # inherit %APPDATA%/postgresql/root.crl or fetch revocation online.
            # libpq ignores an absent CRL; certificate-chain and host checks
            # remain required. Native behavior is an integration acceptance gate.
            options["sslcrl"] = validate_file_path(self.config.sslrootcert + ".crl")
        raw = self._driver.connect(**options)
        try:
            version = getattr(getattr(raw, "info", None), "server_version", None)
            if type(version) is not int or version < 170000:
                raise DatabaseDependencyError("PostgreSQL server version 17 or newer is required; an older or unverifiable server is not supported.")
            self._server_version_num = version
            return raw
        except BaseException:
            raw.close()
            raise

    @contextmanager
    def connect(self):
        raw = self._open()
        try:
            yield Connection(raw, is_sqlite=self.is_sqlite)
        finally:
            try:
                raw.close()
            except Exception:
                raise DatabaseCleanupError("The database connection could not be reliably closed.") from None

    @contextmanager
    def transaction(self, write: bool = True):
        if type(write) is not bool:
            raise TypeError("The transaction write flag must be a boolean.")
        with self.connect() as connection:
            if self.is_sqlite:
                if not write:
                    connection.execute("PRAGMA query_only=ON")
                begin = "BEGIN IMMEDIATE" if write else "BEGIN"
            else:
                begin = "BEGIN ISOLATION LEVEL SERIALIZABLE READ WRITE" if write else "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
            connection.execute(begin)
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except Exception:
                    raise DatabaseCleanupError("The failed transaction could not be reliably rolled back; it will not be retried.") from None
                raise

    def run_transaction(self, operation: Callable[[Connection], T], *, max_attempts: int = 3) -> T:
        """Retry only confirmed PostgreSQL aborts, after rollback and close.

        Operations must contain database work only. External effects cannot be
        made exactly-once by replaying a transaction. Unknown commit outcomes,
        connection failures, application conflicts, and SQLite locks never retry.
        """
        if type(max_attempts) is not int or not 1 <= max_attempts <= 6:
            raise ValueError("max_attempts must be an integer from 1 to 6.")
        for attempt in range(max_attempts):
            try:
                with self.transaction(write=True) as connection:
                    result = operation(connection)
                return result
            except Exception as error:
                confirmed_abort = (
                    not self.is_sqlite
                    and self._driver is not None
                    and isinstance(error, self._driver.Error)
                    and getattr(error, "sqlstate", None) in _RETRYABLE_ABORTS
                )
                if not confirmed_abort or attempt + 1 == max_attempts:
                    raise
                time.sleep(min(0.02 * (2 ** attempt), 0.2))
        raise AssertionError("Transaction retry bounds were not enforced.")

    def verify_schema(self, *, require_guards: bool = True, runtime_role: bool = True) -> None:
        """Inspect schema 10 without creating, migrating, seeding or repairing it."""
        if type(require_guards) is not bool:
            raise TypeError("The schema guard flag must be a boolean.")
        if type(runtime_role) is not bool:
            raise TypeError("The runtime role policy flag must be a boolean.")
        with self.transaction(write=False) as connection:
            self._verify_schema_connection(connection, require_guards=require_guards, runtime_role=runtime_role)

    def _verify_schema_connection(self, connection: Connection, *, require_guards: bool = True, runtime_role: bool = True, expected_version: int | None = None) -> None:
        """Verify within an owner migration transaction before its commit."""
        expected_version = self.schema_version if expected_version is None else expected_version
        if expected_version not in (9, 10):
            raise ValueError("Only the explicit schema-9 migration source or current schema-10 contract is supported")
        try:
            versions = connection.query("SELECT version FROM schema_version")
            if len(versions) != 1 or type(versions[0]["version"]) is not int or versions[0]["version"] != expected_version:
                raise SchemaMismatchError(f"Database schema must have exactly one version-{expected_version} marker. Apply the supplied explicit upgrades while writers are stopped; startup never migrates data.")
            for table, required in _REQUIRED_COLUMNS.items():
                if self.is_sqlite:
                    columns = {row["name"] for row in connection.query(f"PRAGMA table_info({table})")}
                else:
                    columns = {row["column_name"] for row in connection.query(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table", {"table": table},
                    )}
                if not required <= columns:
                    raise SchemaMismatchError(f"Database schema-{expected_version} table {table} is incomplete. No repair has been run.")
                if table == "parts" and columns & {"stock", "stock_units"}:
                    raise SchemaMismatchError("Database still contains active stock-balance columns; the current schema is required.")
            if require_guards:
                if self.is_sqlite:
                    present = {row["name"] for row in connection.query("SELECT name FROM sqlite_master WHERE type='trigger'")}
                    required = _SQLITE_GUARDS
                    if expected_version == 10 and 'supervisor-correction-snapshot' not in (connection.scalar("SELECT sql FROM sqlite_master WHERE type='table' AND name='pm_submission_archive'") or ''):
                        raise SchemaMismatchError("Schema 10 requires the explicit supervisor-correction archive evidence contract.")
                else:
                    present = {row["name"] for row in connection.query("SELECT t.tgname AS name FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')")}
                    required = _POSTGRESQL_GUARDS
                    if expected_version == 10 and not connection.scalar("SELECT COUNT(*) FROM pg_constraint WHERE conrelid='public.pm_submission_archive'::regclass AND contype='c' AND convalidated AND pg_get_constraintdef(oid) LIKE '%supervisor-correction-snapshot%'"):
                        raise SchemaMismatchError("Schema 10 requires the validated supervisor-correction archive evidence constraint.")
                    types = {row["table_name"]: row["data_type"] for row in connection.query("SELECT table_name,data_type FROM information_schema.columns WHERE table_schema='public' AND column_name='quantity_units'")}
                    if any(types.get(table) != "bigint" for table in ("order_parts", "stock_movements", "part_consumptions", "part_consumption_totals")):
                        raise SchemaMismatchError("PostgreSQL quantity evidence requires signed 64-bit integer columns.")
                    constraints = {row["name"] for row in connection.query("SELECT conname AS name FROM pg_constraint WHERE connamespace='public'::regnamespace AND convalidated")}
                    if not {"ck_order_parts_units", "ck_movements_units"} <= constraints:
                        raise SchemaMismatchError("PostgreSQL quantity constraints are absent or unvalidated.")
                    functions = {row["name"] for row in connection.query("SELECT p.proname AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'")}
                    if not {"pak_round_even", "pak_decimal", "pak_participant_score", "pak_decimal_sum"} <= functions:
                        raise SchemaMismatchError("Exact PostgreSQL scoring functions are absent.")
                    if runtime_role:
                        role = connection.one("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user")
                        if role is None or any(role.values()):
                            raise SchemaMismatchError("The runtime database account must not have administrative, replication or row-security bypass privileges.")
                        if connection.scalar("SELECT has_schema_privilege(current_user,'public','CREATE')"):
                            raise SchemaMismatchError("The runtime database account must not be able to create schema objects.")
                        if connection.scalar("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND pg_has_role(current_user,c.relowner,'MEMBER')"):
                            raise SchemaMismatchError("The runtime database account must not own or be a member of an owner role for application tables.")
                        bindings = {f"table_{index}": name for index, name in enumerate(_REQUIRED_COLUMNS)}
                        whitelist = ",".join(":" + name for name in bindings)
                        privileges = connection.query(
                            "SELECT c.relname AS table_name,"
                            "has_table_privilege(current_user,c.oid,'SELECT') AS can_select,"
                            "has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,"
                            "has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,"
                            "has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,"
                            "has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,"
                            "has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger "
                            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname IN (" + whitelist + ")",
                            bindings,
                        )
                        # A comma-separated has_table_privilege list means
                        # ANY privilege in PostgreSQL, not ALL. Check each
                        # required and forbidden privilege independently.
                        if ({row["table_name"] for row in privileges} != set(_REQUIRED_COLUMNS)
                                or any(any(row[permission] is not True for permission in ("can_select", "can_insert", "can_update", "can_delete"))
                                       or row["can_truncate"] is not False or row["can_trigger"] is not False for row in privileges)):
                            raise SchemaMismatchError("The runtime database account requires SELECT, INSERT, UPDATE and DELETE on every application table, and must not have TRUNCATE or TRIGGER privileges. No permissions have been changed.")
                if expected_version == 9:
                    required = required - {'guard_pm_archive_correction_insert'}
                if not required <= present:
                    raise SchemaMismatchError("Required immutable-history and quantity guards are missing. Apply the reviewed initialization/migration SQL before running this software.")
        except Exception as error:
            driver_error = not self.is_sqlite and self._driver is not None and isinstance(error, self._driver.Error)
            if isinstance(error, (sqlite3.DatabaseError, KeyError)) or driver_error:
                raise SchemaMismatchError(f"The existing database cannot be verified as schema {expected_version}. No migration has been run.") from None
            raise
