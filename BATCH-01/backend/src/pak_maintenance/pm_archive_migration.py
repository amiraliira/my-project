"""Explicit, additive owner migration from PostgreSQL schema 7 to 8.

The caller must first create and verify a native backup and stop application
writes. Nothing in application startup invokes this maintenance operation.
"""
from pathlib import Path
import re
from uuid import uuid4

from .common import ApiError, dumps, loads, now
from .database import Database, SchemaMismatchError
from .migration import _verify_schema
from .pm_archive import _execution, _insert


def _legacy_submissions(c):
    """Reconstruct only verifiable old edit→submit pairs, with honest provenance.

    Old archives did not freeze historical equipment/person names. Those labels
    are captured at migration and explicitly marked as reconstructed. A missing
    old edit is never silently replaced by a newer/returned editable body.
    """
    inserted, skipped, examples = 0, 0, []
    cursor = None
    captured = now()
    from .pm_service import execution_end
    while True:
        args = dict(cursor=cursor or "", limit=100)
        orders = c.query("SELECT o.* FROM orders o JOIN pm_weekly_occurrences w ON w.order_id=o.id WHERE o.id>:cursor ORDER BY o.id LIMIT :limit", args)
        if not orders: break
        for order in orders:
            occurrence = c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id", dict(id=order["id"]))
            members = [dict(assetId=item["id"], code=item["code"], name=item["name"], hall=item["hall"]) for item in c.query("SELECT x.id,x.code,x.name,x.hall FROM pm_weekly_assets m JOIN assets x ON x.id=m.asset_id WHERE m.order_id=:id ORDER BY x.code,x.id", dict(id=order["id"]))]
            event_cursor = ""
            while True:
                events = c.query("SELECT id,actor_id,actor_name,details,created_at FROM audit WHERE entity='order' AND entity_id=:id AND action='submit' AND id>:cursor ORDER BY id LIMIT 100", dict(id=order["id"], cursor=event_cursor))
                if not events: break
                for event in events:
                    try:
                        revision = loads(event["details"]).get("revision")
                        if type(revision) is not int or not 1 <= revision <= order["workflow_revision"]:
                            raise ValueError("unknown-revision")
                        if c.scalar("SELECT COUNT(*) FROM pm_submission_archive WHERE order_id=:id AND workflow_revision=:revision", dict(id=order["id"], revision=revision)):
                            continue
                        edit = c.one("SELECT id,details FROM audit WHERE entity='order' AND entity_id=:id AND action IN ('create','edit') AND created_at<=:stamp ORDER BY created_at DESC,id DESC LIMIT 1", dict(id=order["id"], stamp=event["created_at"]))
                        edit_details = loads(edit["details"]) if edit else {}
                        if edit_details.get("revision") != revision - 1:
                            raise ValueError("ambiguous-edit-revision")
                        body = edit_details.get("values")
                        if not isinstance(body, dict):
                            raise ValueError("missing-retained-edit")
                        performers = body.get("participants", [])
                        if len(performers) != 1 or performers[0].get("personId") != event["actor_id"]:
                            raise ValueError("unverified-performer")
                        results = body.get("pmResults")
                        if not isinstance(results, dict) or not results:
                            raise ValueError("missing-recorded-results")
                        end = execution_end(body)
                        execution = _execution(c, body)
                        asset = c.one("SELECT code,name,hall FROM assets WHERE id=:id", dict(id=order["asset_id"]))
                        performer = c.one("SELECT id,name FROM users WHERE id=:id", dict(id=event["actor_id"]))
                        if performer is None or asset is None:
                            raise ValueError("missing-identity")
                        snapshot = dict(execution=execution, results=results, window=body.get("pmWindow"),
                            equipmentMembers=members, submittedByName=event["actor_name"], identityCapturedAt=captured,
                            provenance="legacy-reconstructed", legacyEvidence=dict(submitAuditId=event["id"],
                                editAuditId=edit["id"], identityLabels="captured-at-migration"))
                        _insert(c, dict(id=str(uuid4()), order_id=order["id"], workflow_revision=revision,
                            template_id=occurrence["template_id"], template_version=occurrence["template_version"],
                            plan_id=occurrence["plan_id"], plan_version=occurrence["plan_version"],
                            asset_id=order["asset_id"], performer_id=performer["id"], supervisor_id=occurrence["supervisor_id"],
                            submitted_by=event["actor_id"], number=order["number"], title=body.get("title", order["title"]),
                            hall=asset["hall"], asset_code=asset["code"], asset_name=asset["name"],
                            performer_name=performer["name"], specialty=occurrence["specialty"],
                            work_date=body["workDate"], executed_at=end, submitted_at=event["created_at"],
                            body=dumps(snapshot), evidence_source="legacy-reconstructed"))
                        inserted += 1
                    except (ApiError, KeyError, TypeError, ValueError) as error:
                        skipped += 1
                        if len(examples) < 100:
                            examples.append(dict(orderId=order["id"], submitAuditId=event["id"], reason=str(error)))
                event_cursor = events[-1]["id"]
        cursor = orders[-1]["id"]
    return dict(reconstructed=inserted, unavailableSubmissions=skipped, unavailableExamples=examples)


def upgrade_postgresql_v7(database: Database, *, runtime_user: str,
                          schema_directory: Path | None = None, backfill: bool = True):
    """All DDL, reconstruction, grants and marker update commit atomically."""
    if not isinstance(database, Database) or database.is_sqlite:
        raise ValueError("This explicit migration requires PostgreSQL 17 or newer")
    if not isinstance(runtime_user, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", runtime_user):
        raise ValueError("An explicit simple runtime role is required")
    if type(backfill) is not bool:
        raise ValueError("Backfill must be explicit boolean")
    scripts = Path(schema_directory) if schema_directory else Path(__file__).resolve().parents[2] / "schema"
    with database.transaction() as c:
        c.execute("SELECT pg_advisory_xact_lock(7421986)")
        marker = c.query("SELECT version FROM schema_version")
        if marker == [{"version": 8}]:
            _verify_schema(c, target=True, version=8)
            return dict(schemaVersion=8, alreadyApplied=True, reconstructed=0, unavailableSubmissions=0)
        if marker != [{"version": 7}]:
            raise SchemaMismatchError("Only a verified schema-7 database can be upgraded")
        _verify_schema(c, version=7)
        if not c.scalar("SELECT pg_has_role(current_user,(SELECT relowner FROM pg_class WHERE oid='orders'::regclass),'MEMBER')"):
            raise SchemaMismatchError("The explicit migration owner must own the existing application tables")
        runtime = c.one("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role", dict(role=runtime_user))
        if runtime is None or any(runtime.values()) or runtime_user == database.config.user:
            raise SchemaMismatchError("A separate non-administrative runtime role is required")
        # Exclusive maintenance lock fails within the normal configured lock
        # timeout if the application is still writing; no concurrent backfill.
        c.execute("LOCK TABLE orders,participants,approvals,audit,pm_weekly_occurrences,pm_weekly_plans IN EXCLUSIVE MODE")
        c.raw.execute((scripts / "postgresql-pm-archive.sql").read_text(encoding="utf-8-sig"))
        c.raw.execute((scripts / "postgresql-archive-guards.sql").read_text(encoding="utf-8-sig"))
        c.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON pm_submission_archive TO "{runtime_user}"')
        c.execute(f'GRANT EXECUTE ON FUNCTION pak_guard_pm_archive_insert() TO "{runtime_user}"')
        result = _legacy_submissions(c) if backfill else dict(reconstructed=0, unavailableSubmissions=0, unavailableExamples=[])
        c.execute("UPDATE schema_version SET version=8 WHERE version=7")
        _verify_schema(c, target=True, version=8)
        c.execute("ANALYZE pm_submission_archive")
        return dict(schemaVersion=8, alreadyApplied=False, **result)
