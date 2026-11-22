"""Review PM proposals without exposing an unapproved revision to execution.

The same published mutation methods are used by direct chief edits and by
approved requests. A request decision, publication and both audit records share
one database transaction; a stale revision never partially publishes.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import re
from uuid import uuid4

from .common import ApiError, audit, dumps, loads, now, page_bounds, text
from .pm_hash_json import canonical_json
from .security import assert_live_actor, require
from .permissions import CHIEFS, PM_PROPOSERS


PROPOSERS = PM_PROPOSERS
OPERATIONS = frozenset(("create-template", "edit-template", "design-template",
                        "retire-template", "restore-template", "edit-plan"))


class _TransactionDatabase:
    """A domain operation participating in an already-owned transaction."""
    def __init__(self, connection):
        self.connection = connection
        self.is_sqlite = connection.is_sqlite

    def run_transaction(self, operation, **unused):
        return operation(self.connection)

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def transaction(self, write=True):
        yield self.connection


def _view(row, *, detail=True):
    payload = loads(row["payload"]) if detail else {}
    before = payload.pop("_publishedBefore", None)
    proposed_title = payload.get("title") if row["operation"] in ("create-template", "design-template") else None
    title = row.get("display_title") or proposed_title or (before or {}).get("title") or (before or {}).get("templateTitle") or "برگه PM"
    title = title[:200] if isinstance(title, str) else "برگه PM"
    result = dict(id=row["id"], operation=row["operation"], entityId=row["entity_id"],
                  baseVersion=row["base_version"], status=row["status"], version=row["version"],
                  requesterId=row["requester_id"], requesterName=row.get("requester_name", ""),
                  reviewerId=row.get("reviewer_id"), reviewerName=row.get("reviewer_name"),
                  decisionReason=row.get("decision_reason", ""), createdAt=row["created_at"],
                  decidedAt=row.get("decided_at"), title=title)
    if detail:
        result["before"] = before
        result["payload"] = payload
        result["result"] = loads(row["result"]) if row.get("result") else None
    else:
        result["payload"] = dict(title=title)
    return result


_SELECT = """SELECT p.*,u.name AS requester_name,r.name AS reviewer_name
FROM pm_change_requests p JOIN users u ON u.id=p.requester_id
LEFT JOIN users r ON r.id=p.reviewer_id"""


class PmGovernanceMixin:
    def request_change(self, user, operation, entity_id, payload):
        require(user, *PROPOSERS)
        if operation not in OPERATIONS or not isinstance(payload, dict):
            raise ApiError(400, "درخواست تغییر برگه معتبر نیست.")
        key = payload.get("requestKey")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{16,100}", key):
            raise ApiError(400, "شناسهٔ درخواست معتبر نیست؛ صفحه را تازه کنید.")
        body = deepcopy(payload)
        # Keys used only by privileged setup cannot be smuggled into a proposal.
        if any(key in body for key in ("setupRequestKey", "setupPayloadHash", "_publishedBefore")):
            raise ApiError(400, "تغییر برگه باید از فرم معمول درخواست شود.")
        why = text(body.get("reason", "طراحی برگه جدید" if operation == "create-template" else ""),
                   maximum=2000, field="علت تغییر")
        if len(why) < 3:
            raise ApiError(400, "علت درخواست تغییر را وارد کنید.")
        body["reason"] = why
        identifier = "" if entity_id is None else text(entity_id, maximum=100, field="شناسه برگه")
        creating = operation == "create-template"
        if creating != (not identifier):
            raise ApiError(400, "شناسهٔ برگه برای این درخواست معتبر نیست.")
        base = 0 if creating else body.get("version")
        if type(base) is not int or base < (0 if creating else 1):
            raise ApiError(400, "نسخهٔ برگه باید مشخص باشد.")
        canonical = canonical_json(dict(operation=operation, entityId=identifier, payload=body))
        if len(canonical.encode("utf-8")) > 900_000:
            raise ApiError(413, "درخواست تغییر برگه بیش از حد بزرگ است.")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        def persist(c):
            assert_live_actor(c, user)
            previous = c.one(_SELECT + " WHERE p.requester_id=:user AND p.request_key=:key",
                             dict(user=user.id, key=key))
            if previous:
                if previous["payload_hash"] != digest:
                    raise ApiError(409, "این شناسه قبلاً برای درخواست دیگری استفاده شده است.")
                return dict(pendingApproval=previous["status"] == "pending", replayed=True,
                            changeRequest=_view(previous), message="درخواست قبلاً ثبت شده است.")
            current = None
            if not creating:
                table = "pm_weekly_plans" if operation == "edit-plan" else "pm_templates"
                current = c.one("SELECT * FROM " + table + " WHERE id=:id", dict(id=identifier))
                if not current:
                    raise ApiError(404, "برگه یا برنامه پیدا نشد.")
                if user.role == "supervisor" and current["specialty"] != user.specialty:
                    raise ApiError(403, "درخواست فقط برای برگه‌های واحد شما مجاز است.")
                if current["version"] != base:
                    raise ApiError(409, "برگه تغییر کرده است؛ آن را تازه کنید.")
            if operation in ("create-template", "design-template"):
                design = self._design(body, loads(current["body"]) if current else None)
                if user.role == "supervisor" and design["specialty"] != user.specialty:
                    raise ApiError(403, "طراحی برگه فقط برای واحد شما مجاز است.")
            elif operation == "edit-template":
                if "activities" in body:
                    from .pm_activity_structure import apply_activity_structure
                    apply_activity_structure(dict(loads(current["body"]),version=current["version"]),body["activities"])
                else:
                    self._validate_activity_text_changes(current,body)
            stored_body = dict(body, _publishedBefore=loads(current["body"]) if current else None)
            if len(dumps(stored_body).encode("utf-8")) > 1_500_000:
                raise ApiError(413, "ساختار برگه برای این درخواست بیش از حد بزرگ است.")
            args = dict(id="pm-change-" + uuid4().hex, key=key, user=user.id,
                        operation=operation, entity=identifier, base=base,
                        payload=dumps(stored_body), hash=digest, created=now())
            inserted = c.execute("""INSERT INTO pm_change_requests
(id,request_key,requester_id,operation,entity_id,base_version,payload,payload_hash,status,version,created_at)
VALUES(:id,:key,:user,:operation,:entity,:base,:payload,:hash,'pending',1,:created)
ON CONFLICT(requester_id,request_key) DO NOTHING""", args)
            row = c.one(_SELECT + " WHERE p.requester_id=:user AND p.request_key=:key", args)
            if row["payload_hash"] != digest:
                raise ApiError(409, "این شناسه قبلاً برای درخواست دیگری استفاده شده است.")
            if not inserted:
                return dict(pendingApproval=row["status"] == "pending", replayed=True,
                            changeRequest=_view(row), message="درخواست قبلاً ثبت شده است.")
            audit(c, user, "propose-pm-change", "pm-change", row["id"],
                  dict(operation=operation, entityId=identifier, baseVersion=base))
            return dict(pendingApproval=True, changeRequest=_view(row),
                        message="درخواست برای تأیید رئیس ثبت شد.")
        return self.db.run_transaction(persist)

    @staticmethod
    def _validate_activity_text_changes(current,body):
        from . import pm_form_info
        cells = {cell["id"]: cell for cell in pm_form_info.cells(loads(current["body"]))}
        changes = body.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= 1000:
            raise ApiError(400, "فهرست اصلاح فعالیت معتبر نیست.")
        seen = set()
        for change in changes:
            if not isinstance(change, dict):
                raise ApiError(400, "اصلاح فعالیت معتبر نیست.")
            cell = text(change.get("cellId"), maximum=120, field="شناسه فعالیت")
            value = text(change.get("text"), maximum=10000, field="شرح فعالیت")
            if cell in seen or cell not in cells or not cells[cell].get("activity") or not value:
                raise ApiError(400, "فقط شرح فعالیت موجود قابل درخواست است.")
            seen.add(cell)

    def change_requests(self, user, *, page=None, page_size=None, status="pending"):
        require(user, *CHIEFS, *PROPOSERS)
        page, size = page_bounds(page, page_size)
        if status not in (None, "", "all", "pending", "approved", "rejected"):
            raise ApiError(400, "وضعیت درخواست معتبر نیست.")
        scope = "1=1" if user.role in CHIEFS else "p.requester_id=:user"
        where = " WHERE " + scope + (" AND p.status=:status" if status in ("pending", "approved", "rejected") else "")
        args = dict(user=user.id, status=status, size=size, offset=(page - 1) * size)
        with self.db.transaction(write=False) as c:
            total = c.scalar("SELECT COUNT(*) FROM pm_change_requests p" + where, args)
            # The review queue does not materialize large before/after documents.
            title = ("substr(COALESCE(CASE WHEN p.operation IN ('create-template','design-template') THEN json_extract(p.payload,'$.title') END,json_extract(p.payload,'$._publishedBefore.title'),json_extract(p.payload,'$._publishedBefore.templateTitle'),'برگه PM'),1,200)"
                     if c.is_sqlite else "left(COALESCE(CASE WHEN p.operation IN ('create-template','design-template') THEN p.payload::jsonb->>'title' END,p.payload::jsonb->'_publishedBefore'->>'title',p.payload::jsonb->'_publishedBefore'->>'templateTitle','برگه PM'),200)")
            summary = ("SELECT p.id,p.operation,p.entity_id,p.base_version,p.status,p.version,p.requester_id,p.reviewer_id,p.decision_reason,p.created_at,p.decided_at,"
                       "u.name AS requester_name,r.name AS reviewer_name," + title + " AS display_title FROM pm_change_requests p JOIN users u ON u.id=p.requester_id LEFT JOIN users r ON r.id=p.reviewer_id")
            rows = c.query(summary + where + " ORDER BY p.created_at DESC,p.id DESC LIMIT :size OFFSET :offset", args)
            return dict(items=[_view(row, detail=False) for row in rows], page=page, pageSize=size,
                        total=total, totalPages=(total + size - 1) // size)

    def change_request(self, user, identifier):
        require(user, *CHIEFS, *PROPOSERS)
        with self.db.connect() as c:
            row = c.one(_SELECT + " WHERE p.id=:id", dict(id=identifier))
            if not row or user.role not in CHIEFS and row["requester_id"] != user.id:
                raise ApiError(404, "درخواست پیدا نشد.")
            return _view(row)

    def decide_change(self, user, identifier, body):
        require(user, *CHIEFS)
        if not isinstance(body, dict) or body.get("decision") not in ("approve", "reject"):
            raise ApiError(400, "تصمیم درخواست معتبر نیست.")
        expected = body.get("version")
        if type(expected) is not int or expected < 1:
            raise ApiError(400, "نسخهٔ درخواست معتبر نیست.")
        why = text(body.get("reason"), maximum=2000, field="علت تصمیم")
        if len(why) < 3:
            raise ApiError(400, "علت تصمیم را وارد کنید.")
        status = "approved" if body["decision"] == "approve" else "rejected"
        def decide(c):
            assert_live_actor(c, user)
            # Lock the request before checking its durable version and status.
            row = c.one("SELECT * FROM pm_change_requests WHERE id=:id" +
                        ("" if c.is_sqlite else " FOR UPDATE"), dict(id=identifier))
            if not row:
                raise ApiError(404, "درخواست پیدا نشد.")
            if row["status"] != "pending":
                if (row["status"] == status and row["reviewer_id"] == user.id
                        and row["decision_reason"] == why and row["version"] == expected + 1):
                    result = _view(c.one(_SELECT + " WHERE p.id=:id", dict(id=identifier)))
                    result["replayed"] = True
                    return result
                raise ApiError(409, "این درخواست قبلاً بررسی شده است.")
            if row["version"] != expected:
                raise ApiError(409, "درخواست تغییر کرده است؛ آن را تازه کنید.")
            result = None
            if status == "approved":
                from .pm_service import PmService
                scoped = PmService(_TransactionDatabase(c), self.work)
                payload = loads(row["payload"])
                payload.pop("_publishedBefore", None)
                canonical = canonical_json(dict(operation=row["operation"], entityId=row["entity_id"], payload=payload))
                if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != row["payload_hash"]:
                    raise ApiError(409, "محتوای درخواست تغییر معتبر نیست؛ درخواست جدید ثبت کنید.")
                operations = {"create-template": scoped.create_template,
                              "edit-template": scoped.edit_template, "design-template": scoped.design_template,
                              "retire-template": scoped.retire_template, "restore-template": scoped.restore_template,
                              "edit-plan": scoped.edit_plan}
                method = operations[row["operation"]]
                # Deterministic key is scoped to the approved proposal rather than
                # the proposer's key, which may also be used by another person.
                if row["operation"] == "create-template":
                    payload["requestKey"] = row["id"]
                    result = method(user, payload)
                else:
                    result = method(user, row["entity_id"], payload)
            args = dict(id=identifier, expected=expected, status=status, reviewer=user.id,
                        reason=why, result=dumps(result) if result is not None else None, decided=now())
            if c.execute("""UPDATE pm_change_requests SET status=:status,version=version+1,
reviewer_id=:reviewer,decision_reason=:reason,result=:result,decided_at=:decided
WHERE id=:id AND status='pending' AND version=:expected""", args) != 1:
                raise ApiError(409, "درخواست هم‌زمان تغییر کرده است.")
            audit(c, user, "approve-pm-change" if status == "approved" else "reject-pm-change",
                  "pm-change", identifier, dict(operation=row["operation"], entityId=row["entity_id"],
                                                requesterId=row["requester_id"], reason=why))
            return _view(c.one(_SELECT + " WHERE p.id=:id", dict(id=identifier)))
        return self.db.run_transaction(decide)
