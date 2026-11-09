"""Explicit offline preparation of a clean release database from a frozen backup.

No live deletion, trigger disabling, overwrite, startup migration or guessed PM
activation. The operator swaps the verified result only after stopping the app.
"""
from contextlib import closing
from datetime import date, datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .common import dumps, loads
from .database import Connection, Database, DatabaseConfig
from .migration import LEGACY_TABLES, _iter_rows, _normalize, _source_path, _verify_source, snapshot_sha256
from .network_policy import validate_file_path
from .pm_calendar import month_key

CONFIG_TABLES = ("assets", "parts", "pm_templates", "users", "plans", "pm_template_revisions", "pm_weekly_plans", "pm_plan_revisions")
EMPTY_TABLES = ("orders", "participants", "order_parts", "approvals", "participant_scores", "participant_head_scores", "completed_scores", "part_consumptions", "part_consumption_totals", "pm_occurrences", "pm_weekly_occurrences", "pm_weekly_assets", "request_claims", "stock_movements", "stock_request_claims", "pm_setup_requests", "pm_change_requests", "pm_submission_archive", "sessions", "legacy_records")
PASSWORD_MARKER = "!LOCAL_ACCEPTANCE_PASSWORD_REQUIRED!"
CONFIG_AUDIT_ENTITIES = ("asset", "part", "user", "pm-template", "pm-plan")


def _target_path(path, source):
    path = Path(validate_file_path(str(path)))
    if str(path).startswith("\\\\") or not path.parent.is_dir():
        raise ValueError("Reset output requires an existing local directory")
    if path.exists() or path.with_suffix(path.suffix + ".reset.json").exists() or path.resolve() == source.resolve():
        raise ValueError("Reset preparation never overwrites an existing database")
    return path


def prepare_reset_copy(source, target, *, expected_sha256, schema_directory, operator_code="admin", redact_credentials=False, reset_id=None):
    source = _source_path(source)
    target = _target_path(target, source)
    if snapshot_sha256(source) != expected_sha256:
        raise ValueError("Frozen backup hash does not match")
    if type(redact_credentials) is not bool:
        raise ValueError("Credential redaction must be explicit")
    schema_directory = Path(validate_file_path(str(schema_directory)))
    reset_id = reset_id or uuid4().hex
    if len(reset_id) != 32 or any(c not in "0123456789abcdef" for c in reset_id):
        raise ValueError("Reset operation id must be a canonical UUID hex value")
    stamp = datetime.now(timezone.utc).isoformat()
    specs = {spec.name: spec for spec in LEGACY_TABLES}
    counts = {}
    reason = "بازنشانی سوابق آزمایشی با دستور مالک سامانه؛ حفظ فرم و زمان‌بندی و توقف صدور تا تعیین شروع نهایی"
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True, isolation_level=None)) as raw_source:
        raw_source.row_factory = sqlite3.Row
        raw_source.execute("PRAGMA query_only=ON")
        raw_source.execute("PRAGMA trusted_schema=OFF")
        source_connection = Connection(raw_source, is_sqlite=True)
        source_version = _verify_source(source_connection)
        operator = source_connection.one("SELECT id,active,role FROM users WHERE code=:code", {"code": operator_code})
        if not operator or operator["active"] != 1 or operator["role"] not in ("admin", "head"):
            raise ValueError("An existing active maintenance operator is required")
        original_counts = {name: source_connection.scalar("SELECT COUNT(*) FROM " + name) for name in EMPTY_TABLES if (name != "pm_submission_archive" or source_version >= 8) and (name not in ("participant_head_scores", "pm_change_requests") or source_version >= 7)}
        # Exclusive creation prevents accidental replacement even if a path is
        # created after validation. A failed output is kept for inspection.
        with target.open("xb"):
            pass
        with closing(sqlite3.connect(target)) as fresh:
            fresh.execute("PRAGMA foreign_keys=ON")
            fresh.executescript((schema_directory / "sqlite-v8.sql").read_text(encoding="utf-8"))
            for name in ("sqlite-ledger-guards.sql", "sqlite-pm-guards.sql", "sqlite-evidence-guards.sql", "sqlite-archive-guards.sql"):
                fresh.executescript((schema_directory / name).read_text(encoding="utf-8"))
        database = Database(DatabaseConfig("sqlite", str(target), development=True))
        with database.transaction() as destination:
            for table in CONFIG_TABLES:
                spec = specs[table]
                rows_copied = 0
                for page in _iter_rows(source_connection, spec):
                    values = [_normalize(spec, row, source=True) for row in page]
                    if table == "users" and redact_credentials:
                        for row in values:
                            row["password_hash"] = PASSWORD_MARKER
                    if table == "plans":
                        for row in values:
                            body = loads(row["body"])
                            body.update(active=False, version=row["version"] + 1)
                            row.update(active=0, version=row["version"] + 1, body=dumps(body))
                    if table == "pm_weekly_plans":
                        for row in values:
                            body = loads(row["body"])
                            body.update(active=False, version=row["version"] + 1)
                            for key in ("approvedAt", "activatedAt", "effectiveFromUtc", "issueFromDate", "setupRequestKey", "setupPayloadHash"):
                                body.pop(key, None)
                            row.update(active=0, version=row["version"] + 1, cursor_month=month_key(date.fromisoformat(row["start_date"])), next_scan=row["start_date"], body=dumps(body))
                    insert = "INSERT INTO " + table + "(" + ",".join(spec.columns) + ") VALUES(" + ",".join(":" + column for column in spec.columns) + ")"
                    destination.executemany(insert, values)
                    rows_copied += len(values)
                counts[table] = rows_copied
            # Prior configuration revisions are retained; pausing receives its
            # own explicit technical-maintenance revision instead of rewriting it.
            for plan in destination.query("SELECT id,version,body FROM pm_weekly_plans"):
                destination.execute("INSERT INTO pm_plan_revisions(plan_id,revision,body,reason,actor_id,created_at) VALUES(:id,:version,:body,:reason,:actor,:stamp)", {**plan, "reason": reason, "actor": operator["id"], "stamp": stamp})
            audit_spec = specs["audit"]
            kept_audit = 0
            for page in _iter_rows(source_connection, audit_spec):
                for row in page:
                    keep_checkpoint = row["id"] == "local-test-credentials-v1" and not redact_credentials
                    if row["entity"] in CONFIG_AUDIT_ENTITIES or keep_checkpoint:
                        destination.execute("INSERT INTO audit(" + ",".join(audit_spec.columns) + ") VALUES(" + ",".join(":" + key for key in audit_spec.columns) + ")", row)
                        kept_audit += 1
            destination.execute("INSERT INTO audit(id,actor_id,actor_name,action,entity,entity_id,details,created_at) VALUES(:id,:actor,'عملیات نگهداری مجاز','reset-operations','system',:reset,:details,:stamp)", {"id": "reset-" + reset_id, "actor": operator["id"], "reset": reset_id, "details": dumps({"purpose": reason, "sourceBackupSha256": expected_sha256, "removedRowCounts": original_counts, "allPmPlansPaused": True, "credentialsRedacted": redact_credentials}), "stamp": stamp})
            if destination.query("PRAGMA foreign_key_check"):
                raise ValueError("Reset output has broken foreign keys")
            if any(destination.scalar("SELECT COUNT(*) FROM " + table) for table in EMPTY_TABLES):
                raise ValueError("Operational rows remain in the reset output")
            if destination.scalar("SELECT COUNT(*) FROM pm_weekly_plans WHERE active<>0") or destination.scalar("SELECT COUNT(*) FROM plans WHERE active<>0"):
                raise ValueError("PM issuance was not fully paused")
        database.verify_schema()
        with database.connect() as checked:
            if checked.scalar("PRAGMA integrity_check") != "ok":
                raise ValueError("Reset output failed integrity verification")
        if snapshot_sha256(source) != expected_sha256:
            raise ValueError("Frozen backup changed during preparation")
    report = {"resetId": reset_id, "createdUtc": stamp, "schemaVersion": 8, "sourceSnapshotSha256": expected_sha256, "sourceUnchanged": True,
        "outputSha256": snapshot_sha256(target), "preservedConfigurationRows": counts,
        "removedOperationalRows": original_counts, "retainedConfigurationAuditRows": kept_audit,
        "allOperationalTablesEmpty": True, "allPmPlansPaused": True,
        "configurationRevisionsPreserved": True, "credentialsRedacted": redact_credentials,
        "sessionsDiscarded": True, "integrity": "ok", "foreignKeyViolations": 0}
    target.with_suffix(target.suffix + ".reset.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare a new clean paused database from an explicit frozen backup; never overwrite live data")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--schema-directory", required=True)
    parser.add_argument("--operator-code", default="admin")
    parser.add_argument("--redact-credentials", action="store_true")
    args = parser.parse_args(argv)
    report = prepare_reset_copy(args.source, args.target, expected_sha256=args.expected_sha256, schema_directory=args.schema_directory, operator_code=args.operator_code, redact_credentials=args.redact_credentials)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
