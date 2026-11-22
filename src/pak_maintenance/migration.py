"""Explicit, offline schema-6/7/8 snapshot migration into schema 8.

Only a verified frozen local SQLite snapshot and an already prepared EMPTY
target are accepted. The source is read-only, sessions are discarded, user
session epochs are incremented, and every copied table is verified before the
single target transaction commits. PostgreSQL integration remains an acceptance
gate; the SQLite target exists only for explicit development tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Iterator

from .database import (Connection, Database, _POSTGRESQL_GUARDS as _CURRENT_POSTGRESQL_GUARDS,
                       _SQLITE_GUARDS as _CURRENT_SQLITE_GUARDS)
from .network_policy import validate_file_path


class MigrationError(ValueError):
    """The snapshot or target cannot be verified; no migration is committed."""


# This archived snapshot copier deliberately still targets schema 8. Schema-9
# startup guards must never redefine what an old frozen snapshot contained.
_SQLITE_GUARDS = _CURRENT_SQLITE_GUARDS - frozenset((
    "guard_pm_archive_correction_insert",
    "guard_participant_grading_policy_insert", "guard_participant_grading_policies_update", "guard_participant_grading_policies_delete",
    "guard_pm_responsibility_transfer_insert", "guard_pm_responsibility_transfers_update", "guard_pm_responsibility_transfers_delete",
))
_POSTGRESQL_GUARDS = _CURRENT_POSTGRESQL_GUARDS - frozenset((
    "guard_pm_archive_correction_insert",
    "guard_participant_grading_policy_insert", "guard_participant_grading_policies_history",
    "guard_pm_responsibility_transfer_insert", "guard_pm_responsibility_transfers_history",
))


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    keys: tuple[str, ...]
    numeric: frozenset[str]
    integer: frozenset[str]
    bigint: frozenset[str]


# Fixed source-controlled schema-6 whitelist, in foreign-key dependency order.
# All generated SQL identifiers originate here, never in caller input or rows.
_DEFINITIONS = (
    ("assets", "id code name hall line body version", "id", "", "version", ""),
    ("legacy_records", "id body imported_at", "id", "", "", ""),
    ("parts", "id code name specification unit unit_cost version", "id", "unit_cost", "version", ""),
    ("pm_templates", "id title specialty hall period version mapping_status body", "id", "", "version", ""),
    ("schema_version", "version", "", "", "version", ""),
    ("users", "id code name role specialty active password_hash session_epoch", "id", "", "active session_epoch", ""),
    ("audit", "id actor_id actor_name action entity entity_id details created_at", "id", "", "", ""),
    ("orders", "id number version workflow_revision type status asset_id created_by assignee_id work_date due_date body created_at updated_at duration_minutes title description", "id", "", "version workflow_revision duration_minutes", ""),
    ("part_consumption_totals", "part_id quantity_units report_count", "part_id", "", "", "quantity_units report_count"),
    ("plans", "id asset_id next_due interval_days active version body", "id", "", "interval_days active version", ""),
    ("pm_setup_requests", "actor_id request_key payload_hash body created_at", "actor_id request_key", "", "", ""),
    ("pm_template_revisions", "template_id revision body reason actor_id created_at", "template_id revision", "", "revision", ""),
    ("pm_weekly_plans", "id template_id asset_id supervisor_id specialty hall version active mapping_confirmed start_date cursor_month next_scan body", "id", "", "version active mapping_confirmed cursor_month", ""),
    ("sessions", "id user_id epoch expires", "id", "", "epoch", ""),
    ("stock_movements", "id part_id quantity kind reference note actor_id created_at quantity_units", "id", "quantity", "", "quantity_units"),
    ("approvals", "id order_id revision stage specialty decision coefficient reason actor_id created_at", "id", "coefficient", "revision", ""),
    ("order_parts", "order_id part_id quantity quantity_units", "order_id part_id", "quantity", "", "quantity_units"),
    ("participants", "order_id person_id minutes specialty", "order_id person_id", "", "minutes", ""),
    ("pm_occurrences", "plan_id due_date order_id", "plan_id due_date", "", "", ""),
    ("pm_plan_revisions", "plan_id revision body reason actor_id created_at", "plan_id revision", "", "revision", ""),
    ("pm_weekly_assets", "order_id asset_id", "order_id asset_id", "", "", ""),
    ("request_claims", "actor_id request_key order_id payload_hash", "actor_id request_key", "", "", ""),
    ("stock_request_claims", "actor_id request_key movement_id payload_hash", "actor_id request_key", "", "", ""),
    ("part_consumptions", "id order_id part_id approval_id revision quantity_units part_code part_name unit order_number executed_at approved_at actor_id actor_name", "id", "", "revision", "quantity_units"),
    ("participant_scores", "order_id revision person_id specialty coefficient actor_id approval_id created_at", "order_id revision person_id", "coefficient", "revision", ""),
    # New-target evidence guards additionally depend on participants and scores.
    ("completed_scores", "order_id revision person_id supervisor_coefficient head_coefficient score_raw score_cents source", "order_id revision person_id", "supervisor_coefficient head_coefficient", "revision", "score_cents"),
    ("pm_weekly_occurrences", "plan_id persian_year month week order_id plan_version template_id template_version start_utc end_utc specialty supervisor_id submitted_at executed_at", "plan_id persian_year month week", "", "persian_year month week plan_version template_version", ""),
)
LEGACY_TABLES = tuple(TableSpec(name, tuple(cols.split()), tuple(keys.split()), frozenset(numbers.split()),
                        frozenset(integers.split()), frozenset(bigints.split()))
               for name, cols, keys, numbers, integers, bigints in _DEFINITIONS)
_HEAD_SCORES = TableSpec("participant_head_scores", tuple("order_id revision person_id specialty coefficient actor_id approval_id created_at".split()),
                         ("order_id", "revision", "person_id"), frozenset(), frozenset(("revision",)), frozenset())
_PM_CHANGES = TableSpec("pm_change_requests", tuple("id requester_id operation entity_id base_version payload payload_hash request_key status version decision_reason reviewer_id decided_at result created_at".split()),
                       ("id",), frozenset(), frozenset(("base_version", "version")), frozenset())
TABLES = tuple(TableSpec(spec.name, spec.columns + ("head_coefficient_exact",), spec.keys, spec.numeric, spec.integer, spec.bigint)
               if spec.name == "completed_scores" else spec for spec in LEGACY_TABLES)
# Both new tables depend only on objects already copied at this point; the
# immutable final facts additionally depend on the individual chief evidence.
V7_TABLES = tuple(item for spec in TABLES for item in ((_HEAD_SCORES, spec) if spec.name == "completed_scores" else (spec,))) + (_PM_CHANGES,)
_PM_ARCHIVE = TableSpec("pm_submission_archive", tuple(("id order_id workflow_revision template_id template_version plan_id plan_version asset_id performer_id supervisor_id submitted_by number title hall asset_code asset_name performer_name specialty work_date executed_at submitted_at body body_sha256 evidence_source").split()),
    ("id",), frozenset(), frozenset(("workflow_revision", "template_version", "plan_version")), frozenset())
TABLES = V7_TABLES + (_PM_ARCHIVE,)
_MIN_I64, _MAX_I64 = -(2 ** 63), 2 ** 63 - 1
_HASH = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)
_HEAD_COEFFICIENT = re.compile(r"(?:[0-3]|[0-2]\.[0-9]{1,28}|3\.0{1,28})", re.ASCII)
_BATCH = 100
_SOURCE_SQLITE_GUARDS = frozenset((
    # Names present in the reviewed legacy C# Db/ConsumptionService source.
    # They are admitted as inert metadata only; source writes/DDL are forbidden.
    "guard_order_parts_units_insert", "guard_order_parts_units_update",
    "guard_movements_units_insert", "guard_movements_units_update",
    "guard_consumption_units_insert", "guard_consumption_history_update",
    "guard_consumption_history_delete", "guard_consumption_total_insert",
    "guard_consumption_total_update", "guard_consumption_total_delete",
))
_PYTHON_V6_GUARDS = _SQLITE_GUARDS - frozenset((
    "guard_pm_archive_insert", "guard_pm_archive_update", "guard_pm_archive_delete",
    "guard_participant_head_score_evidence_insert", "guard_participant_head_scores_update", "guard_participant_head_scores_delete",
    "guard_pm_change_request_insert", "guard_pm_change_request_update", "guard_pm_change_request_delete",
))


@dataclass(frozen=True, slots=True)
class TableVerification:
    table: str
    rows: int
    sha256: str


@dataclass(frozen=True, slots=True)
class MigrationReport:
    source_sha256: str
    target_provider: str
    schema_version: int
    discarded_sessions: int
    tables: tuple[TableVerification, ...]
    source_schema_version: int


def snapshot_sha256(path: str | Path) -> str:
    """Hash a validated local regular snapshot without changing it."""
    source = _source_path(path)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_path(path: str | Path) -> Path:
    validated = validate_file_path(path)
    if validated.startswith("\\\\"):
        raise MigrationError("The frozen source must be a local snapshot, never a network share.")
    source = Path(validated)
    try:
        metadata = source.stat()
    except OSError:
        raise MigrationError("The frozen source snapshot is unavailable.") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size < 100:
        raise MigrationError("The source must be an existing regular SQLite snapshot.")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(source) + suffix).exists():
            raise MigrationError("Live SQLite sidecars are present. Use a separately verified consistent frozen backup.")
    return source


def _decimal(value: Any) -> Decimal:
    try:
        if type(value) is float:
            if not math.isfinite(value):
                raise InvalidOperation
            # Match the legacy C# SQLite numeric conversion (15 significant
            # digits); never route quantities or JSON through binary floats.
            result = Decimal(format(value, ".15g"))
        elif isinstance(value, Decimal):
            result = value
        elif type(value) is int:
            result = Decimal(value)
        else:
            raise InvalidOperation
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, OverflowError, ValueError):
        raise MigrationError("A NUMERIC source or target value is not finite and numeric.") from None


def _normalize(spec: TableSpec, row: dict[str, Any], *, source: bool) -> dict[str, Any]:
    result = {}
    for column in spec.columns:
        value = row[column]
        if value is None:
            if column in spec.keys:
                raise MigrationError("A primary key contains NULL.")
        elif column in spec.numeric:
            value = _decimal(value)
        elif column in spec.integer or column in spec.bigint:
            if type(value) is not int or not _MIN_I64 <= value <= _MAX_I64:
                raise MigrationError("An integer source or target value is outside signed 64-bit storage.")
            if source and spec.name == "users" and column == "session_epoch":
                if value < 0 or value == _MAX_I64:
                    raise MigrationError("A user session epoch cannot be safely incremented.")
                value += 1
        elif not isinstance(value, str):
            raise MigrationError("A TEXT source or target value has an incompatible storage type.")
        if value is not None and (spec.name == "participant_head_scores" and column == "coefficient" or spec.name == "completed_scores" and column == "head_coefficient_exact"):
            if not isinstance(value, str) or _HEAD_COEFFICIENT.fullmatch(value) is None:
                raise MigrationError("An exact individual chief coefficient is outside its decimal contract.")
        result[column] = value
    return result


def _feed(digest, spec: TableSpec, row: dict[str, Any]) -> None:
    for column in spec.columns:
        value = row[column]
        if value is None:
            payload = b"N"
        elif isinstance(value, Decimal):
            number = format(value, "f")
            if "." in number:
                number = number.rstrip("0").rstrip(".")
            if value == 0:
                number = "0"
            payload = b"D" + number.encode("ascii")
        elif type(value) is int:
            payload = b"I" + str(value).encode("ascii")
        else:
            payload = b"T" + value.encode("utf-8", "strict")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)


def _iter_rows(connection: Connection, spec: TableSpec) -> Iterator[list[dict[str, Any]]]:
    """Bounded keyset pages with provider-independent binary text ordering."""
    if not spec.keys:
        raise MigrationError("Only keyed data tables may be copied.")
    collation = "BINARY" if connection.is_sqlite else '"C"'
    ordered = [key if key in spec.integer or key in spec.bigint else f"{key} COLLATE {collation}" for key in spec.keys]
    last = None
    while True:
        params: dict[str, Any] = {"limit": _BATCH}
        where = ""
        if last is not None:
            comparisons = []
            for index, key in enumerate(ordered):
                prefix = [f"{ordered[previous]}=:key{previous}" for previous in range(index)]
                comparisons.append("(" + " AND ".join(prefix + [f"{key}>:key{index}"]) + ")")
                params[f"key{index}"] = last[spec.keys[index]]
            where = " WHERE " + " OR ".join(comparisons)
        rows = connection.query(f"SELECT {','.join(spec.columns)} FROM {spec.name}{where} ORDER BY {','.join(ordered)} LIMIT :limit", params)
        if not rows:
            return
        yield rows
        last = rows[-1]


def _verify_schema(connection: Connection, *, target: bool = False, version: int = 8, allow_optional_oee: bool = False) -> None:
    specs = TABLES if version == 8 else V7_TABLES if version == 7 else LEGACY_TABLES
    expected = {spec.name for spec in specs}
    if connection.is_sqlite:
        objects = connection.query("SELECT name,type FROM sqlite_master WHERE substr(name,1,7)<>'sqlite_' AND type IN ('table','view','trigger')")
        actual = {row["name"] for row in objects if row["type"] == "table"}
        triggers = {row["name"] for row in objects if row["type"] == "trigger"}
        v7_guards = _SQLITE_GUARDS - frozenset(("guard_pm_archive_insert", "guard_pm_archive_update", "guard_pm_archive_delete"))
        valid_triggers = triggers == (_SQLITE_GUARDS if version == 8 else v7_guards) if target or version in (7, 8) else triggers <= _SOURCE_SQLITE_GUARDS or triggers == _PYTHON_V6_GUARDS
        if actual != expected or any(row["type"] == "view" for row in objects) or not valid_triggers:
            raise MigrationError(f"The database contains unknown or missing schema-{version} objects.")
    else:
        objects = connection.query("SELECT table_name,table_type FROM information_schema.tables WHERE table_schema='public'")
        optional = {"oee_module", "oee_lines", "oee_sheets", "oee_sheet_revisions"}
        actual = {row["table_name"] for row in objects}
        oee = allow_optional_oee and optional <= actual
        if actual != expected | (optional if oee else set()) or any(row["table_type"] != "BASE TABLE" for row in objects):
            raise MigrationError(f"The PostgreSQL target must contain only the prepared schema-{version} tables.")
        if oee and connection.query("SELECT version FROM oee_module") != [{"version": 1}]:
            raise MigrationError("The optional OEE module marker is not version 1.")
        triggers = connection.query("SELECT t.tgname AS name,t.tgenabled AS enabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal")
        required_guards = _POSTGRESQL_GUARDS if version == 8 else _POSTGRESQL_GUARDS - frozenset(("guard_pm_archive_insert", "guard_pm_archive_history"))
        if oee:
            required_guards = required_guards | {"oee_revisions_immutable", "oee_sheets_no_delete"}
        if {row["name"] for row in triggers} != required_guards or any(row["enabled"] not in ("O", "A") for row in triggers):
            raise MigrationError("The target requires exactly the reviewed enabled evidence guards.")
    for spec in specs:
        if connection.is_sqlite:
            columns = connection.query(f"PRAGMA table_info({spec.name})")
            actual_columns = {row["name"]: row["type"].upper() for row in columns}
            keys = tuple(row["name"] for row in sorted(columns, key=lambda item: item["pk"]) if row["pk"])
            types = {column: "NUMERIC" if column in spec.numeric else "INTEGER" if column in spec.integer else "BIGINT" if column in spec.bigint else "TEXT" for column in spec.columns}
        else:
            columns = connection.query("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=:table", {"table": spec.name})
            actual_columns = {row["column_name"]: row["data_type"] for row in columns}
            keys = tuple(row["column_name"] for row in connection.query("SELECT k.column_name FROM information_schema.table_constraints t JOIN information_schema.key_column_usage k ON k.constraint_name=t.constraint_name AND k.constraint_schema=t.constraint_schema AND k.table_name=t.table_name WHERE t.table_schema='public' AND t.table_name=:table AND t.constraint_type='PRIMARY KEY' ORDER BY k.ordinal_position", {"table": spec.name}))
            types = {column: "numeric" if column in spec.numeric else "integer" if column in spec.integer else "bigint" if column in spec.bigint else "text" for column in spec.columns}
        if actual_columns != types or keys != spec.keys:
            raise MigrationError(f"The schema-{version} column or primary-key contract differs for {spec.name}.")
    rows = connection.query("SELECT version FROM schema_version")
    if rows != [{"version": version}]:
        raise MigrationError(f"Exactly one schema-version {version} marker is required.")


def verify_schema8_snapshot_target(database: Database, *, require_guards: bool = True, runtime_role: bool = False) -> None:
    """Owner-only admission for the unchanged offline schema-8 snapshot tool."""
    if require_guards is not True or runtime_role is not False:
        raise MigrationError("The legacy snapshot target requires full guards and explicit owner authority.")
    with database.transaction(write=False) as connection:
        _verify_schema(connection, target=True, version=8)
        if not database.is_sqlite:
            functions = {row["name"] for row in connection.query("SELECT p.proname AS name FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'")}
            if not {"pak_round_even", "pak_decimal", "pak_participant_score", "pak_decimal_sum"} <= functions:
                raise MigrationError("Exact PostgreSQL score functions are absent from the schema-8 snapshot target.")
            constraints = {row["name"] for row in connection.query("SELECT conname AS name FROM pg_constraint WHERE connamespace='public'::regnamespace AND convalidated")}
            if not {"ck_order_parts_units", "ck_movements_units"} <= constraints:
                raise MigrationError("Validated schema-8 quantity constraints are required.")


def _verify_source(connection: Connection) -> int:
    rows = connection.query("SELECT version FROM schema_version")
    if len(rows) != 1 or type(rows[0].get("version")) is not int or rows[0]["version"] not in (6, 7, 8):
        raise MigrationError("Exactly one supported schema-version 6, 7 or 8 marker is required.")
    version = rows[0]["version"]
    _verify_schema(connection, version=version)
    if connection.query("PRAGMA integrity_check") != [{"integrity_check": "ok"}]:
        raise MigrationError("The source failed SQLite integrity verification.")
    if connection.query("PRAGMA foreign_key_check"):
        raise MigrationError("The source contains broken foreign-key references.")
    return version


@contextmanager
def _suspended_target_guards(target: Connection):
    """Restore historical evidence only in the owner's empty-target transaction.

    Runtime guards validate *new* transitions against today's order state. A
    frozen history also contains completed orders and decided PM requests.
    Foreign keys and CHECK constraints remain enforced throughout the copy.
    Any failure skips restoration here and the outer transaction rolls back
    both copied rows and trigger DDL, including failed final verification.
    """
    if target.is_sqlite:
        triggers = target.query("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")
        if {row["name"] for row in triggers} != _SQLITE_GUARDS or any(not isinstance(row["sql"], str) for row in triggers):
            raise MigrationError("The target has unknown or missing reviewed guards.")
        for row in triggers:
            target.execute('DROP TRIGGER "' + row["name"] + '"')
    else:
        triggers = target.query("SELECT t.tgname AS name,c.relname AS table_name,t.tgenabled AS enabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY t.tgname")
        tables = {spec.name for spec in TABLES}
        if {row["name"] for row in triggers} != _POSTGRESQL_GUARDS or any(row["table_name"] not in tables or row["enabled"] not in ("O", "A") for row in triggers):
            raise MigrationError("The target has unknown or missing reviewed guards.")
        for row in triggers:
            target.execute('ALTER TABLE "' + row["table_name"] + '" DISABLE TRIGGER "' + row["name"] + '"')
    yield
    for row in triggers:
        if target.is_sqlite:
            target.execute(row["sql"])
        else:
            enabled = "ENABLE ALWAYS" if row["enabled"] == "A" else "ENABLE"
            target.execute('ALTER TABLE "' + row["table_name"] + '" ' + enabled + ' TRIGGER "' + row["name"] + '"')


def _copy_rows(source: Connection, target: Connection, source_version: int) -> tuple[TableVerification, ...]:
    source_specs = {spec.name: spec for spec in (TABLES if source_version == 8 else V7_TABLES if source_version == 7 else LEGACY_TABLES)}
    expected_tables = []
    for current in TABLES:
        if current.name in ("schema_version", "sessions"):
            continue
        spec = source_specs.get(current.name, current)
        digest = hashlib.sha256()
        count = 0
        if current.name in source_specs:
            insert = f"INSERT INTO {spec.name}({','.join(spec.columns)}) VALUES({','.join(':'+column for column in spec.columns)})"
            for rows in _iter_rows(source, spec):
                normalized = [_normalize(spec, row, source=True) for row in rows]
                for row in normalized:
                    _feed(digest, spec, row)
                target.executemany(insert, normalized)
                count += len(normalized)
        expected_tables.append((spec, count, digest.hexdigest()))
    results = []
    for spec, expected_count, expected_hash in expected_tables:
        digest = hashlib.sha256()
        count = 0
        for rows in _iter_rows(target, spec):
            for row in rows:
                _feed(digest, spec, _normalize(spec, row, source=False))
            count += len(rows)
        if count != expected_count or digest.hexdigest() != expected_hash:
            raise MigrationError(f"Copied table verification failed for {spec.name}; the entire target transaction will roll back.")
        results.append(TableVerification(spec.name, count, expected_hash))
    if source_version == 6 and target.scalar("SELECT COUNT(*) FROM completed_scores WHERE head_coefficient_exact IS NOT NULL"):
        raise MigrationError("Legacy chief grades must retain their original scalar evidence without fabricated individual grades.")
    return tuple(results)


def _copy(source: Connection, target: Connection, source_path: Path, expected: str, source_version: int) -> MigrationReport:
    _verify_schema(target, target=True)
    if not target.is_sqlite:
        # Block concurrent inserts while emptiness, copy and verification run.
        target.execute("LOCK TABLE " + ",".join(spec.name for spec in TABLES) + " IN EXCLUSIVE MODE")
    for spec in TABLES:
        if spec.name != "schema_version" and target.scalar(f"SELECT COUNT(*) FROM {spec.name}") != 0:
            raise MigrationError("The target is not empty. Migration never overwrites or merges existing data.")
    skipped = source.scalar("SELECT COUNT(*) FROM sessions")
    with _suspended_target_guards(target):
        results = _copy_rows(source, target, source_version)
    _verify_schema(target, target=True)
    if target.scalar("SELECT COUNT(*) FROM sessions") != 0:
        raise MigrationError("The migrated target unexpectedly contains active sessions.")
    if target.is_sqlite and target.query("PRAGMA foreign_key_check"):
        raise MigrationError("The target failed foreign-key verification.")
    if snapshot_sha256(source_path) != expected:
        raise MigrationError("The source snapshot changed during migration; the target transaction will roll back.")
    return MigrationReport(expected, "sqlite-test" if target.is_sqlite else "postgresql", 8, skipped, results, source_version)


def migrate_snapshot(source_path: str | Path, target: Database, *, expected_sha256: str) -> MigrationReport:
    """Copy a frozen snapshot into an explicitly prepared empty target.

    This function never creates schema, changes configuration, starts services,
    cuts over the application, deletes files, or touches a live source. A failed
    final COMMIT is propagated; an unknown commit result must be inspected by an
    operator, never automatically retried or described as rolled back.
    """
    if not isinstance(expected_sha256, str) or _HASH.fullmatch(expected_sha256) is None:
        raise MigrationError("An independently recorded SHA-256 of the frozen snapshot is required.")
    expected = expected_sha256.lower()
    source = _source_path(source_path)
    if target.is_sqlite:
        if not target.config.development:
            raise MigrationError("SQLite migration targets are allowed only for explicit development tests.")
        try:
            same_file = Path(target.config.database).samefile(source)
        except OSError:
            raise MigrationError("The target must be an existing explicitly prepared database.") from None
        if same_file:
            raise MigrationError("The source and target must be different files.")
    if snapshot_sha256(source) != expected:
        raise MigrationError("The source snapshot does not match the independently recorded SHA-256.")
    raw = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True, isolation_level=None)
    try:
        connection = Connection(raw, is_sqlite=True)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        try:
            source_version = _verify_source(connection)
            # Admission checks the complete initialization contract, including
            # target guards and PostgreSQL exact scoring functions. Only this
            # explicit migration owner may suspend target guards transactionally;
            # the application runtime never invokes this operation.
            # This explicit offline operator operation needs table-lock
            # authority that the restricted runtime account must not have.
            # Only runtime-account admission is skipped; schema, guard and
            # exact-function verification remain mandatory.
            verify_schema8_snapshot_target(target, require_guards=True, runtime_role=False)
            # No automatic retries: migration is an explicit operator operation.
            with target.transaction(write=True) as destination:
                result = _copy(connection, destination, source, expected, source_version)
                if not destination.is_sqlite:
                    # Publish planner statistics together with the bulk import.
                    for spec in TABLES:
                        destination.execute("ANALYZE " + spec.name)
        finally:
            connection.execute("ROLLBACK")
    finally:
        raw.close()
    return result
