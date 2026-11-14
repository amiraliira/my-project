"""Append-only correction of routing for untouched, already issued PM forms.

Occurrence identity and the original issuing supervisor remain immutable. Only
the execution responsibility resolves through this ledger.
"""
from copy import deepcopy
from uuid import uuid4

from .common import ApiError, audit, dumps, loads, now, text


def owner_sql(occurrence="p"):
    if occurrence not in {"p", "pm_scope", "pm_owner", "w"}:
        raise ValueError("Unapproved occurrence alias")
    return ("COALESCE((SELECT rt.to_supervisor_id FROM pm_responsibility_transfers rt "
            "WHERE rt.order_id=" + occurrence + ".order_id ORDER BY rt.order_version DESC LIMIT 1),"
            + occurrence + ".supervisor_id)")


def effective_owner(c, order_id, original=None):
    result = c.scalar("SELECT to_supervisor_id FROM pm_responsibility_transfers WHERE order_id=:id "
                      "ORDER BY order_version DESC LIMIT 1", {"id": order_id})
    if result is not None:
        return result
    return original if original is not None else c.scalar(
        "SELECT supervisor_id FROM pm_weekly_occurrences WHERE order_id=:id", {"id": order_id})


def execution_started(row, occurrence, body, participants=(), approvals=()):
    """Cheap batch projection; the mutation also checks every persisted evidence table."""
    results = body.get("pmResults", {})
    return (row["status"] != "draft" or row["workflow_revision"] != 1 or
            occurrence.get("executed_at") is not None or occurrence.get("submitted_at") is not None or
            bool(participants) or bool(approvals) or row.get("duration_minutes", 0) != 0 or
            any(body.get(key) for key in ("participants", "parts", "checklist", "description", "startTime", "endTime",
                                        "durationMinutes", "downtimeMinutes", "productionStopped")) or
            not isinstance(results, dict) or any(str(value).strip() for value in results.values()))


def blocked_reason(c, row, occurrence):
    if execution_started(row, occurrence, loads(row["body"])):
        return "فقط برگهٔ پیش‌نویس بدون ثبت کار، نتیجه یا ارسال قابل اصلاح ارجاع است."
    for table in ("participants", "order_parts", "approvals", "participant_scores", "participant_head_scores",
                  "participant_grading_policies", "completed_scores", "pm_submission_archive", "part_consumptions"):
        if c.scalar("SELECT COUNT(*) FROM " + table + " WHERE order_id=:id", {"id": row["id"]}):
            return "این برگه سابقهٔ کار، ارسال یا تأیید دارد و قابل اصلاح ارجاع نیست."
    return ""


def require_chief(user):
    if not user or not user.active or user.role not in {"head", "manager"}:
        raise ApiError(403, "اصلاح ارجاع برگهٔ صادرشده فقط با رئیس یا مدیر است.")


class PmResponsibilityMixin:
    def responsibility(self, user, id):
        require_chief(user)
        with self.db.transaction(write=False) as c:
            row = c.one("SELECT * FROM orders WHERE id=:id", {"id": id})
            occurrence = c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id", {"id": id})
            if row is None or occurrence is None:
                raise ApiError(404, "برگهٔ هفتگی پیدا نشد.")
            owner = effective_owner(c, id, occurrence["supervisor_id"])
            why = blocked_reason(c, row, occurrence)
            history = c.query("SELECT t.*,f.name AS from_name,n.name AS to_name,a.name AS actor_name "
                "FROM pm_responsibility_transfers t JOIN users f ON f.id=t.from_supervisor_id "
                "JOIN users n ON n.id=t.to_supervisor_id JOIN users a ON a.id=t.actor_id "
                "WHERE t.order_id=:id ORDER BY t.order_version DESC", {"id": id})
            return dict(orderId=id, version=row["version"], number=row["number"], title=row["title"],
                specialty=occurrence["specialty"], originalSupervisorId=occurrence["supervisor_id"],
                originalSupervisorName=c.scalar("SELECT name FROM users WHERE id=:id", {"id": occurrence["supervisor_id"]}),
                supervisorId=owner, supervisorName=c.scalar("SELECT name FROM users WHERE id=:id", {"id": owner}),
                assigneeId=row["assignee_id"], eligible=not why, blockedReason=why,
                history=[dict(id=h["id"], fromSupervisorId=h["from_supervisor_id"], fromSupervisorName=h["from_name"],
                    toSupervisorId=h["to_supervisor_id"], toSupervisorName=h["to_name"], actorName=h["actor_name"],
                    reason=h["reason"], createdAt=h["created_at"], orderVersion=h["order_version"]) for h in history])

    def transfer_responsibility(self, user, id, input):
        require_chief(user)
        if not isinstance(input, dict):
            raise ApiError(400, "اطلاعات درخواست معتبر نیست.")
        expected = input.get("version")
        if type(expected) is not int:
            raise ApiError(400, "نسخهٔ برگه باید عدد صحیح باشد.")
        target = text(input.get("supervisorId", ""), maximum=100)
        why = text(input.get("reason", ""), maximum=2000)
        if not target or len(why) < 3:
            raise ApiError(400, "سرپرست جدید و علت اصلاح ارجاع را وارد کنید.")

        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c, user)
            lock = "" if c.is_sqlite else " FOR UPDATE"
            row = c.one("SELECT * FROM orders WHERE id=:id" + lock, {"id": id})
            if row is None:
                raise ApiError(404, "برگه پیدا نشد.")
            if row["version"] != expected:
                raise ApiError(409, "برگه هم‌زمان تغییر کرده است؛ تازه‌سازی کنید.")
            occurrence = c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id", {"id": id})
            if occurrence is None:
                raise ApiError(404, "برگهٔ هفتگی پیدا نشد.")
            blocked = blocked_reason(c, row, occurrence)
            if blocked:
                raise ApiError(409, blocked)
            owner = effective_owner(c, id, occurrence["supervisor_id"])
            if target == owner:
                raise ApiError(409, "این سرپرست هم‌اکنون مسئول برگه است.")
            person = c.one("SELECT id FROM users WHERE id=:target AND active=1 AND role='supervisor' "
                           "AND specialty=:specialty" + lock, {"target": target, "specialty": occurrence["specialty"]})
            if not person:
                raise ApiError(400, "یک سرپرست فعال از همان تخصص برگه انتخاب کنید.")
            stamp = now()
            transfer_id = str(uuid4())
            c.execute("INSERT INTO pm_responsibility_transfers(id,order_id,order_version,from_supervisor_id,"
                      "to_supervisor_id,actor_id,reason,created_at) VALUES(:id,:order,:version,:previous,:target,:actor,:reason,:stamp)",
                      dict(id=transfer_id, order=id, version=expected, previous=owner, target=target, actor=user.id, reason=why, stamp=stamp))
            body = deepcopy(loads(row["body"]))
            body["assigneeId"] = None
            if c.execute("UPDATE orders SET assignee_id=NULL,body=:body,version=version+1,updated_at=:stamp "
                         "WHERE id=:id AND version=:version", dict(id=id, version=expected, body=dumps(body), stamp=stamp)) != 1:
                raise ApiError(409, "برگه هم‌زمان تغییر کرده است.")
            audit(c, user, "transfer-weekly-pm-responsibility", "order", id,
                  dict(transferId=transfer_id, originalSupervisorId=occurrence["supervisor_id"],
                       fromSupervisorId=owner, toSupervisorId=target, previousAssigneeId=row["assignee_id"],
                       previousVersion=expected, version=expected + 1, reason=why), created_at=stamp)
            if self.work is None:
                raise RuntimeError("WorkService must be injected for responsibility transfer API")
            return self.work.view(c, c.one("SELECT * FROM orders WHERE id=:id", {"id": id}), user, detail=True)

        return self.db.run_transaction(operation)
