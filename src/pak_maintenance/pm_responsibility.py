"""Append-only correction of routing for untouched, already issued PM forms.

Occurrence identity and the original issuing supervisor remain immutable. Only
the execution responsibility resolves through this ledger.
"""
from copy import deepcopy
from uuid import uuid4

from .common import ApiError, audit, dumps, loads, now, page_bounds, text
from .permissions import CHIEFS


_UTILITIES_LABELS = ("تاسیسات", "تأسیسات", "تاسيسات", "تأسيسات", "utilities")


def _technical_filter(specialty):
    if not specialty:
        return "", {}
    labels = _UTILITIES_LABELS if specialty in _UTILITIES_LABELS else (specialty,)
    params = {f"unit{index}": label for index, label in enumerate(labels)}
    return " AND p.specialty IN (" + ",".join(":" + key for key in params) + ")", params


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
    def routing_review(self, user, target_id="", source_id="", specialty="", section="plans",
                       page=None, size=None):
        """Review routes relative to a selected active supervisor in one read snapshot."""
        require_chief(user)
        target_id = text(target_id, maximum=100)
        source_id = text(source_id, maximum=100)
        specialty = text(specialty, maximum=200)
        if section not in ("plans", "issued", "history") or target_id and target_id == source_id:
            raise ApiError(400, "بخش بررسی یا سرپرست‌های انتخاب‌شده معتبر نیستند.")
        page, size = page_bounds(page, size, maximum=50)
        technical_filter, params = _technical_filter(specialty)
        args = dict(params, target=target_id, source=source_id, size=size, offset=(page - 1) * size)
        with self.db.transaction(write=False) as c:
            from .security import assert_live_actor
            assert_live_actor(c, user, lock=False)
            if not target_id:
                specialties = c.query(
                    "SELECT specialty FROM pm_weekly_plans WHERE trim(specialty)<>'' "
                    "UNION SELECT specialty FROM pm_weekly_occurrences WHERE trim(specialty)<>'' "
                    "ORDER BY specialty LIMIT 1000")
                return {"specialties": [row["specialty"] for row in specialties]}
            target = c.one("SELECT id,name,code,specialty FROM users WHERE id=:target "
                           "AND active=1 AND role='supervisor' AND trim(specialty)<>''", args)
            if target is None:
                raise ApiError(400, "سرپرست مقصد باید فعال و دارای واحد مشخص باشد.")
            if source_id and not c.scalar("SELECT COUNT(*) FROM users WHERE id=:source", args):
                raise ApiError(400, "مسئول فعلی در فهرست کاربران موجود نیست.")
            plan_from = (
                " FROM pm_weekly_plans p JOIN pm_templates f ON f.id=p.template_id "
                "LEFT JOIN users owner ON owner.id=p.supervisor_id WHERE f.mapping_status<>'retired'"
                + technical_filter + " AND COALESCE(p.supervisor_id,'')<>:target"
                + (" AND p.supervisor_id=:source" if source_id else ""))
            # The effective owner includes prior transfers. Resolving it once
            # per row keeps prior ownership from reappearing after correction.
            issued = ("WITH routes AS (SELECT o.id,o.status,o.title,p.specialty,p.end_utc,"
                      "p.plan_id AS plan_id," + owner_sql("p") + " AS supervisor_id,"
                      "p.executed_at,p.submitted_at FROM pm_weekly_occurrences p "
                      "JOIN orders o ON o.id=p.order_id WHERE 1=1" + technical_filter + ") ")
            issue_where = (" WHERE routes.supervisor_id<>:target"
                           + (" AND routes.supervisor_id=:source" if source_id else ""))
            counts = c.one(issued + "SELECT COUNT(CASE WHEN status IN ('completed','cancelled') THEN 1 END)"
                           " AS history,COUNT(CASE WHEN status NOT IN ('completed','cancelled') THEN 1 END)"
                           " AS issued FROM routes" + issue_where, args)
            counts["plans"] = c.scalar("SELECT COUNT(*)" + plan_from, args)
            if section == "plans":
                rows = c.query("SELECT p.id,p.hall,p.specialty,p.active,p.version,f.title,p.supervisor_id,"
                               "owner.name AS supervisor_name,owner.code AS supervisor_code" + plan_from
                               + " ORDER BY f.title,p.id LIMIT :size OFFSET :offset", args)
                items = [dict(id=r["id"], title=r["title"], hall=r["hall"], specialty=r["specialty"],
                              version=r["version"], supervisorId=r["supervisor_id"], supervisorName=r["supervisor_name"],
                              supervisorCode=r["supervisor_code"], active=bool(r["active"])) for r in rows]
            else:
                status = "IN" if section == "history" else "NOT IN"
                rows = c.query(issued + "SELECT routes.*,p.hall,owner.name AS supervisor_name,"
                               "owner.code AS supervisor_code FROM routes "
                               "JOIN pm_weekly_plans p ON p.id=routes.plan_id "
                               "JOIN users owner ON owner.id=routes.supervisor_id" + issue_where
                               + " AND routes.status " + status + " ('completed','cancelled') "
                               "ORDER BY routes.end_utc DESC,routes.id LIMIT :size OFFSET :offset", args)
                items = [dict(id=r["id"], title=r["title"], hall=r["hall"], specialty=r["specialty"],
                              supervisorId=r["supervisor_id"], supervisorName=r["supervisor_name"],
                              supervisorCode=r["supervisor_code"], status=r["status"],
                              canReviewTransfer=(r["status"] == "draft" and not r["executed_at"]
                                                 and not r["submitted_at"])) for r in rows]
        return dict(target=target, section=section, counts=counts, items=items, page=page, pageSize=size,
                    total=counts[section], totalPages=(counts[section] + size - 1) // size)

    def transfer_plan_responsibility(self, user, identifier, body):
        """Change only the owner used by new issues, keeping schedule and history."""
        if not user or not user.active or user.role not in CHIEFS:
            raise ApiError(403, "اصلاح مسئول نوبت‌های آتی فقط با رئیس است.")
        if not isinstance(body, dict) or type(body.get("version")) is not int or "previousSupervisorId" not in body:
            raise ApiError(400, "نسخه و مسئول فعلی برنامه را از بازبینی انتخاب کنید.")
        previous = body["previousSupervisorId"]
        if previous is not None:
            previous = text(previous, maximum=100)
        target = text(body.get("supervisorId", ""), maximum=100)
        why = text(body.get("reason", ""), maximum=2000)
        expected = body["version"]
        if expected < 1 or not target or target == previous or len(why) < 3:
            raise ApiError(400, "سرپرست جدید و علت اصلاح مسئول را وارد کنید.")

        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c, user)
            lock = "" if c.is_sqlite else " FOR UPDATE OF p"
            row = c.one("SELECT p.version,p.supervisor_id,p.body,f.mapping_status FROM pm_weekly_plans p "
                        "JOIN pm_templates f ON f.id=p.template_id WHERE p.id=:id" + lock, {"id": identifier})
            if row is None:
                raise ApiError(404, "برنامهٔ PM پیدا نشد.")
            if row["version"] != expected or row["supervisor_id"] != previous:
                raise ApiError(409, "مسئول یا نسخهٔ برنامه تغییر کرده است. بازبینی را دوباره اجرا کنید.")
            if row["mapping_status"] == "retired":
                raise ApiError(409, "تغییر مسئول برنامهٔ بایگانی‌شده مجاز نیست.")
            target_lock = "" if c.is_sqlite else " FOR UPDATE"
            if not c.one("SELECT id FROM users WHERE id=:target AND active=1 AND role='supervisor' "
                         "AND trim(specialty)<>''" + target_lock, {"target": target}):
                raise ApiError(400, "سرپرست مقصد باید فعال و دارای واحد مشخص باشد.")
            stamp = now()
            updated = loads(row["body"])
            updated.update(supervisorId=target, version=expected + 1)
            params = dict(id=identifier, owner=target, version=expected, revision=expected + 1,
                          body=dumps(updated), reason=why, actor=user.id, stamp=stamp)
            if c.execute("UPDATE pm_weekly_plans SET supervisor_id=:owner,version=:revision,body=:body "
                         "WHERE id=:id AND version=:version", params) != 1:
                raise ApiError(409, "برنامه هم‌زمان تغییر کرده است.")
            c.execute("INSERT INTO pm_plan_revisions(plan_id,revision,body,reason,actor_id,created_at) "
                      "VALUES(:id,:revision,:body,:reason,:actor,:stamp)", params)
            audit(c, user, "transfer-weekly-pm-plan-responsibility", "pm-plan", identifier,
                  dict(fromSupervisorId=previous, toSupervisorId=target, previousVersion=expected,
                       version=expected + 1, reason=why), created_at=stamp)
            return dict(id=identifier, version=expected + 1, supervisorId=target)

        return self.db.run_transaction(operation)

    def responsibility(self, user, id):
        require_chief(user)
        with self.db.transaction(write=False) as c:
            from .security import assert_live_actor
            assert_live_actor(c, user, lock=False)
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
                           "AND trim(specialty)<>''" + lock, {"target": target})
            if not person:
                raise ApiError(400, "یک سرپرست فعال با تیم مشخص انتخاب کنید.")
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
