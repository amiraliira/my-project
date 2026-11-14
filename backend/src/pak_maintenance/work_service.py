"""Role-scoped, optimistic and transactional maintenance workflow."""
import hashlib
import re
from collections import defaultdict
from datetime import date as Date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo
from .common import ApiError, audit, now, today, dumps as json_dump, loads as json_load
from .quantity import decimal_value, units, value
from .score_facts import participant_score, round_score, stored_decimal, decimal_add, capture as capture_scores
from .consumption_service import capture as capture_consumption
from .pm_responsibility import effective_owner, owner_sql, execution_started
from .work_review_correction import ReviewCorrectionMixin

ROLES = {"technician", "supervisor", "planner", "head", "manager", "admin"}


def text(body, key, maximum=4000):
    raw = body.get(key)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ApiError(400, f"مقدار {key} باید متن باشد.")
    result = raw.strip()
    if len(result) > maximum or "\x00" in result:
        raise ApiError(400, f"طول {key} بیش از حد مجاز است.")
    return result


def integer(raw, default=0):
    if raw is None:
        return default
    if type(raw) is not int:
        raise ApiError(400, "عدد صحیح معتبر وارد کنید.")
    return raw


def boolean(raw, default=False):
    if raw is None:
        return default
    if type(raw) is not bool:
        raise ApiError(400, "مقدار بله یا خیر معتبر وارد کنید.")
    return raw


def items(body, key, maximum):
    result = body.get(key)
    if result is None:
        result = []
    if not isinstance(result, list) or len(result) > maximum or any(not isinstance(v, dict) for v in result):
        raise ApiError(400, f"فهرست {key} نامعتبر یا بیش از حد مجاز است.")
    return result


def _in_list(values, name="key"):
    return "(" + ",".join(":" + name + str(i) for i in range(len(values))) + ")", {name + str(i): v for i, v in enumerate(values)}


def _needs_supervisor(alias="p", revision=":rev"):
    # Identifiers are source-controlled SQL fragments, never request values.
    return ("NOT EXISTS(SELECT 1 FROM participant_grading_policies gp WHERE gp.order_id=" + alias +
            ".order_id AND gp.revision=" + revision + " AND gp.person_id=" + alias +
            ".person_id AND gp.policy='head-only-v1')")


class WorkService(ReviewCorrectionMixin):
    participant_score = staticmethod(participant_score)

    def __init__(self, db, pm_service=None):
        self.db, self.pm_service = db, pm_service

    @staticmethod
    def _role(user):
        if user is None or user.role not in ROLES or not user.active:
            raise ApiError(403, "دسترسی مجاز نیست.")

    @staticmethod
    def management(user):
        return user is not None and user.role in {"head", "manager", "planner", "admin"}

    @staticmethod
    def can_see_grade(user, person, specialty):
        from .permissions import can_view_grades
        return can_view_grades(user, specialty)

    @staticmethod
    def date(raw, field, optional=False):
        if optional and not raw:
            return ""
        try:
            if not isinstance(raw, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) is None:
                raise ValueError()
            parsed = Date.fromisoformat(raw)
            if not 2000 <= parsed.year <= 9998:
                raise ValueError()
            return raw
        except ValueError:
            raise ApiError(400, f"تاریخ {field} باید میلادی معتبر به قالب YYYY-MM-DD باشد.") from None

    @staticmethod
    def version(row, version):
        if type(version) is not int or row["version"] != version:
            raise ApiError(409, "اطلاعات این کار تغییر کرده است؛ صفحه را تازه کنید.")

    @staticmethod
    def page_bounds(page=None, page_size=None, maximum=100):
        p, size = 1 if page is None else page, 25 if page_size is None else page_size
        if type(p) is not int or type(size) is not int or not 1 <= p <= 1_000_000 or not 1 <= size <= maximum:
            raise ApiError(400, f"شماره صفحه معتبر و اندازه صفحه بین ۱ تا {maximum} انتخاب کنید.")
        return p, size

    @staticmethod
    def can_read(c, row, user):
        WorkService._role(user)
        if WorkService.management(user) or user.role == "planner":
            return True
        occurrence = c.one("SELECT supervisor_id FROM pm_weekly_occurrences WHERE order_id=:id", {"id": row["id"]})
        if occurrence is not None:
            return effective_owner(c, row["id"], occurrence["supervisor_id"]) == user.id if user.role == "supervisor" else row["assignee_id"] == user.id
        if user.id in {row["created_by"], row["assignee_id"]}:
            return True
        if user.role == "supervisor":
            return bool(user.specialty) and bool(c.scalar("""SELECT COUNT(*) FROM participants WHERE order_id=:id AND specialty=:spec""", {"id": row["id"], "spec": user.specialty}) or c.scalar("SELECT COUNT(*) FROM users WHERE id=:assignee AND specialty=:spec", {"assignee": row["assignee_id"], "spec": user.specialty}))
        return bool(c.scalar("SELECT COUNT(*) FROM participants WHERE order_id=:id AND person_id=:person", {"id": row["id"], "person": user.id}))

    @staticmethod
    def get_row(c, order_id, user):
        WorkService._role(user)
        row = c.one("SELECT * FROM orders WHERE id=:id", {"id": order_id})
        if row is None:
            raise ApiError(404, "کار پیدا نشد.")
        if not WorkService.can_read(c, row, user):
            raise ApiError(403, "این کار در حوزه دسترسی شما نیست.")
        return row

    @staticmethod
    def can_edit(c, row, user):
        if row["status"] not in {"draft", "returned"}:
            return False
        if c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences WHERE order_id=:id", {"id": row["id"]}):
            return user.role == "technician" and row["assignee_id"] == user.id
        return WorkService.management(user) or user.role == "planner" or user.id in {row["created_by"], row["assignee_id"]} or (user.role == "supervisor" and WorkService.can_read(c, row, user))

    @staticmethod
    def all_participants_graded(c, row):
        args = {"id": row["id"], "rev": row["workflow_revision"]}
        return bool(c.scalar("SELECT COUNT(*) FROM participants WHERE order_id=:id", args)) and not c.scalar("""SELECT COUNT(*) FROM participants p WHERE p.order_id=:id AND
""" + _needs_supervisor() + """ AND (NOT EXISTS(SELECT 1 FROM participant_scores s WHERE s.order_id=p.order_id AND s.revision=:rev AND s.person_id=p.person_id AND s.specialty=p.specialty)
OR NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=p.order_id AND a.revision=:rev AND a.stage='supervisor' AND a.specialty=p.specialty AND a.decision='approve'))""", args)

    @staticmethod
    def can_decide(c, row, user):
        if row["status"] == "head":
            return user.role == "head" and WorkService.all_participants_graded(c, row)
        if row["status"] != "supervisor" or user.role != "supervisor" or not user.specialty:
            return False
        args = {"id": row["id"], "rev": row["workflow_revision"], "spec": user.specialty, "user": user.id}
        return not c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences p WHERE p.order_id=:id AND " + owner_sql("p") + "<>:user", args) and bool(c.scalar("SELECT COUNT(*) FROM participants p WHERE p.order_id=:id AND p.specialty=:spec AND " + _needs_supervisor(), args)) and not c.scalar("SELECT COUNT(*) FROM approvals WHERE order_id=:id AND revision=:rev AND stage='supervisor' AND specialty=:spec AND decision='approve'", args)

    @staticmethod
    def read_scope(user, alias="o"):
        WorkService._role(user)
        if alias not in {"o"}:
            raise ValueError("Unapproved SQL alias")
        if WorkService.management(user) or user.role == "planner":
            return "1=1"
        direct = "o.created_by=:viewer OR o.assignee_id=:viewer"
        if user.role == "supervisor":
            if not user.specialty:
                return "1=0"
            return "(EXISTS(SELECT 1 FROM pm_weekly_occurrences pm_scope WHERE pm_scope.order_id=o.id AND " + owner_sql("pm_scope") + "=:viewer) OR (NOT EXISTS(SELECT 1 FROM pm_weekly_occurrences pm_scope WHERE pm_scope.order_id=o.id) AND (" + direct + " OR EXISTS(SELECT 1 FROM participants scope_p WHERE scope_p.order_id=o.id AND scope_p.specialty=:specialty) OR EXISTS(SELECT 1 FROM users scope_u WHERE scope_u.id=o.assignee_id AND scope_u.specialty=:specialty))))"
        # Weekly PM remains restricted to its assigned technician even if a stale
        # participant/creator exists; the list and detail scopes must agree.
        return "((NOT EXISTS(SELECT 1 FROM pm_weekly_occurrences pm_scope WHERE pm_scope.order_id=o.id) AND (" + direct + " OR EXISTS(SELECT 1 FROM participants scope_p WHERE scope_p.order_id=o.id AND scope_p.person_id=:viewer))) OR o.assignee_id=:viewer)"

    @staticmethod
    def decision_scope(user):
        WorkService._role(user)
        if user.role == "supervisor" and user.specialty:
            return "o.status='supervisor' AND NOT EXISTS(SELECT 1 FROM pm_weekly_occurrences pm_owner WHERE pm_owner.order_id=o.id AND " + owner_sql("pm_owner") + "<>:viewer) AND EXISTS(SELECT 1 FROM participants p WHERE p.order_id=o.id AND p.specialty=:specialty AND " + _needs_supervisor(revision="o.workflow_revision") + ") AND NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id AND a.revision=o.workflow_revision AND a.stage='supervisor' AND a.specialty=:specialty AND a.decision='approve')"
        if user.role == "head":
            return "o.status='head' AND EXISTS(SELECT 1 FROM participants p WHERE p.order_id=o.id) AND NOT EXISTS(SELECT 1 FROM participants p WHERE p.order_id=o.id AND " + _needs_supervisor(revision="o.workflow_revision") + " AND (NOT EXISTS(SELECT 1 FROM participant_scores s WHERE s.order_id=o.id AND s.revision=o.workflow_revision AND s.person_id=p.person_id AND s.specialty=p.specialty) OR NOT EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id AND a.revision=o.workflow_revision AND a.stage='supervisor' AND a.specialty=p.specialty AND a.decision='approve')))"
        return "1=0"

    @staticmethod
    def query_parameters(user, q=None):
        q = (q or "").strip()
        if len(q) > 300:
            raise ApiError(400, "عبارت جست‌وجو حداکثر ۳۰۰ نویسه باشد.")
        return {"viewer": user.id, "specialty": user.specialty, "today": today(),
                "search": "%" + q.lower().replace("~", "~~").replace("%", "~%").replace("_", "~_") + "%" if q else None}

    @staticmethod
    def filters(args, type=None, status=None, asset_id=None, hall=None, overdue=False):
        if type and type not in {"PM", "EM", "PROJECT"}:
            raise ApiError(400, "نوع کار نامعتبر است.")
        if len(status or "") > 30 or len(asset_id or "") > 100 or len(hall or "") > 100:
            raise ApiError(400, "فیلتر کار نامعتبر است.")
        args.update(type=type or None, status=status or None, asset=asset_id or None, hall=hall or None)
        result = " AND (CAST(:type AS TEXT) IS NULL OR o.type=:type) AND (CAST(:status AS TEXT) IS NULL OR o.status=:status) AND (CAST(:asset AS TEXT) IS NULL OR o.asset_id=:asset OR EXISTS(SELECT 1 FROM pm_weekly_assets pa WHERE pa.order_id=o.id AND pa.asset_id=:asset)) AND (CAST(:hall AS TEXT) IS NULL OR EXISTS(SELECT 1 FROM assets fa WHERE fa.id=o.asset_id AND fa.hall=:hall))"
        if overdue:
            result += " AND o.due_date IS NOT NULL AND o.due_date<:today"
        return result + " AND (CAST(:search AS TEXT) IS NULL OR LOWER(o.number) LIKE :search ESCAPE '~' OR LOWER(o.title) LIKE :search ESCAPE '~' OR LOWER(o.description) LIKE :search ESCAPE '~' OR EXISTS(SELECT 1 FROM assets sa WHERE sa.id=o.asset_id AND (LOWER(sa.code) LIKE :search ESCAPE '~' OR LOWER(sa.name) LIKE :search ESCAPE '~' OR LOWER(sa.hall) LIKE :search ESCAPE '~')))"

    def list_page(self, user, type=None, status=None, q=None, page=None, page_size=None, asset_id=None, view=None, hall=None, overdue=False):
        page, size = self.page_bounds(page, page_size)
        args = self.query_parameters(user, q)
        args.update(size=size, offset=(page - 1) * size)
        scope = self.read_scope(user)
        filters = self.filters(args, type, status, asset_id, hall, overdue)
        views = {None: "", "": "", "all": "", "assigned": " AND o.type='PM' AND o.created_by=:viewer AND o.assignee_id IS NOT NULL", "returned": " AND o.status='returned'", "assignedPending": " AND o.type='PM' AND o.assignee_id=:viewer AND o.status IN ('draft','returned')", "registrationAssignedPending": " AND o.type='PM' AND o.assignee_id=:viewer AND o.status IN ('draft','returned') AND NOT EXISTS(SELECT 1 FROM pm_weekly_occurrences p WHERE p.order_id=o.id)"}
        if view not in views:
            raise ApiError(400, "نمای فهرست نامعتبر است.")
        filters += views[view]
        with self.db.transaction(write=False) as c:
            counts = c.one("SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN o.type='PM' AND o.created_by=:viewer AND o.assignee_id IS NOT NULL THEN 1 ELSE 0 END),0) AS assigned,COALESCE(SUM(CASE WHEN o.status='returned' THEN 1 ELSE 0 END),0) AS returned,COALESCE(SUM(CASE WHEN o.type='PM' AND o.assignee_id=:viewer AND o.status IN ('draft','returned') THEN 1 ELSE 0 END),0) AS assigned_pending FROM orders o WHERE " + scope, args)
            total = c.scalar("SELECT COUNT(*) FROM orders o WHERE " + scope + filters, args)
            rows = c.query("SELECT o.* FROM orders o WHERE " + scope + filters + " ORDER BY o.created_at DESC,o.id DESC LIMIT :size OFFSET :offset", args)
            orders = self.view_batch(c, rows, user)
        return {"orders": orders, "total": total, "page": page, "pageSize": size, "totalPages": (total + size - 1) // size,
                "counts": {"total": counts["total"], "assigned": counts["assigned"], "returned": counts["returned"], "assignedPending": counts["assigned_pending"]}}

    def inbox_page(self, user, page=None, page_size=None, type=None, q=None, overdue=False, hall=None):
        page, size = self.page_bounds(page, page_size)
        args = self.query_parameters(user, q)
        args.update(size=size, offset=(page - 1) * size)
        scope = self.decision_scope(user)
        filters = self.filters(args, type=type, hall=hall, overdue=overdue)
        with self.db.transaction(write=False) as c:
            counts = c.one("SELECT COUNT(*) AS total,COALESCE(SUM(CASE WHEN o.type='PM' THEN 1 ELSE 0 END),0) AS pm,COALESCE(SUM(CASE WHEN o.type='EM' THEN 1 ELSE 0 END),0) AS em,COALESCE(SUM(CASE WHEN o.type='PROJECT' THEN 1 ELSE 0 END),0) AS project,COALESCE(SUM(CASE WHEN o.due_date IS NOT NULL AND o.due_date<:today THEN 1 ELSE 0 END),0) AS overdue FROM orders o WHERE " + scope, args)
            total = c.scalar("SELECT COUNT(*) FROM orders o WHERE " + scope + filters, args)
            orders = self.view_batch(c, c.query("SELECT o.* FROM orders o WHERE " + scope + filters + " ORDER BY o.updated_at,o.created_at,o.id LIMIT :size OFFSET :offset", args), user)
        return {"orders": orders, "total": total, "page": page, "pageSize": size, "totalPages": (total + size - 1) // size,
                "counts": counts, "stage": "supervisor" if user.role == "supervisor" else "head" if user.role == "head" else None,
                "specialty": user.specialty if user.role == "supervisor" else None}

    def list(self, user, type=None, status=None, q=None, asset_id=None):
        args = self.query_parameters(user, q)
        scope = self.read_scope(user) + self.filters(args, type, status, asset_id)
        with self.db.transaction(write=False) as c:
            if c.scalar("SELECT COUNT(*) FROM orders o WHERE " + scope, args) > 1000:
                raise ApiError(413, "فهرست بزرگ است؛ از نسخه جدید با صفحه‌بندی استفاده کنید.")
            rows = c.query("SELECT o.* FROM orders o WHERE " + scope + " ORDER BY o.created_at DESC,o.id DESC", args)
            return [item for start in range(0, len(rows), 100) for item in self.view_batch(c, rows[start:start + 100], user)]

    def inbox(self, user):
        args = self.query_parameters(user)
        scope = self.decision_scope(user)
        with self.db.transaction(write=False) as c:
            if c.scalar("SELECT COUNT(*) FROM orders o WHERE " + scope, args) > 1000:
                raise ApiError(413, "کارتابل بزرگ است؛ از نسخه جدید با صفحه‌بندی استفاده کنید.")
            rows = c.query("SELECT o.* FROM orders o WHERE " + scope + " ORDER BY o.updated_at,o.created_at,o.id", args)
            orders = [item for start in range(0, len(rows), 100) for item in self.view_batch(c, rows[start:start + 100], user)]
        counts = {"total": len(orders), "pm": sum(o["type"] == "PM" for o in orders), "em": sum(o["type"] == "EM" for o in orders), "project": sum(o["type"] == "PROJECT" for o in orders), "overdue": sum(bool(o.get("dueDate")) and o["dueDate"] < today() for o in orders)}
        return {"orders": orders, "counts": counts, "stage": "supervisor" if user.role == "supervisor" else "head" if user.role == "head" else None, "specialty": user.specialty if user.role == "supervisor" else None}

    def export(self, user, type=None, status=None, q=None, asset_id=None):
        args = self.query_parameters(user, q)
        scope = self.read_scope(user) + self.filters(args, type, status, asset_id)
        def rows():
            with self.db.transaction(write=False) as c:
                created = identifier = None
                while True:
                    parameters = dict(args, cursor_created=created, cursor_id=identifier)
                    cursor = "" if created is None else " AND (o.created_at<:cursor_created OR (o.created_at=:cursor_created AND o.id<:cursor_id))"
                    batch = c.query("SELECT o.* FROM orders o WHERE " + scope + cursor + " ORDER BY o.created_at DESC,o.id DESC LIMIT 100", parameters)
                    if not batch:
                        return
                    yield from self.view_batch(c, batch, user)
                    created, identifier = batch[-1]["created_at"], batch[-1]["id"]
        return rows()

    def get(self, user, order_id, history_page=1, history_page_size=50):
        with self.db.transaction(write=False) as c:
            return self.view(c, self.get_row(c, order_id, user), user, detail=True, history_page=history_page, history_page_size=history_page_size)

    @staticmethod
    def _load_views(c, rows):
        if len(rows) > 100:
            raise ValueError("Order hydration batch exceeds its limit")
        if not rows:
            return {}
        membership, args = _in_list([row["id"] for row in rows])
        assets_membership, assets_args = _in_list(list({row["asset_id"] for row in rows}))
        users_membership, users_args = _in_list(list({identifier for row in rows for identifier in (row["created_by"], row["assignee_id"]) if identifier}))
        def grouped(query):
            result = defaultdict(list)
            for row in c.query(query, args):
                result[row["order_id"]].append(row)
            return result
        carry_revision = "CAST(json_extract(a.details,'$.revision') AS INTEGER)" if c.is_sqlite else "CAST(a.details::jsonb->>'revision' AS INTEGER)"
        return {"assets": {row["id"]: row for row in c.query("SELECT id,code,name,hall,line FROM assets WHERE id IN " + assets_membership, assets_args)},
                "users": {row["id"]: row for row in c.query("SELECT id,name,specialty FROM users WHERE id IN " + users_membership, users_args)},
                "participants": grouped("SELECT p.order_id,p.person_id,p.minutes,p.specialty,u.name FROM participants p JOIN users u ON u.id=p.person_id WHERE p.order_id IN " + membership + " ORDER BY p.order_id,p.person_id"),
                "parts": grouped("SELECT op.order_id,op.part_id,op.quantity_units,p.code,p.name,p.unit FROM order_parts op JOIN parts p ON p.id=op.part_id WHERE op.order_id IN " + membership + " ORDER BY op.order_id,op.part_id"),
                "approvals": grouped("SELECT a.*,u.name FROM approvals a JOIN users u ON u.id=a.actor_id JOIN orders o ON o.id=a.order_id AND o.workflow_revision=a.revision WHERE a.order_id IN " + membership + " ORDER BY a.order_id,a.created_at,a.id"),
                "carried_approvals": grouped("SELECT a.entity_id AS order_id,a.actor_name,a.created_at,a.details FROM audit a JOIN orders o ON o.id=a.entity_id WHERE a.entity='order' AND a.action='carry-supervisor-approval' AND " + carry_revision + "=o.workflow_revision AND a.entity_id IN " + membership),
                "grades": grouped("SELECT s.*,u.name AS actor_name FROM participant_scores s JOIN users u ON u.id=s.actor_id JOIN orders o ON o.id=s.order_id AND o.workflow_revision=s.revision WHERE s.order_id IN " + membership + " ORDER BY s.order_id,s.person_id"),
                "policies": grouped("SELECT gp.* FROM participant_grading_policies gp JOIN orders o ON o.id=gp.order_id AND o.workflow_revision=gp.revision WHERE gp.order_id IN " + membership),
                "head_grades": grouped("SELECT s.*,u.name AS actor_name FROM participant_head_scores s JOIN users u ON u.id=s.actor_id JOIN orders o ON o.id=s.order_id AND o.workflow_revision=s.revision WHERE s.order_id IN " + membership + " ORDER BY s.order_id,s.person_id"),
                "occurrences": {row["order_id"]: row for row in c.query("SELECT p.order_id,p.supervisor_id,p.end_utc,p.executed_at,p.submitted_at," + owner_sql("p") + " AS effective_supervisor_id FROM pm_weekly_occurrences p WHERE p.order_id IN " + membership, args)}}

    def view_batch(self, c, rows, user):
        data = self._load_views(c, rows)
        return [self._render(c, row, data, user, False) for row in rows]

    def view(self, c, row, user=None, detail=False, history_page=1, history_page_size=200):
        return self._render(c, row, self._load_views(c, [row]), user, detail, history_page, history_page_size)

    def _render(self, c, row, data, user, detail, history_page=1, history_page_size=200):
        j = json_load(row["body"])
        if user is None or user.role != "head":
            j = self._without_personnel_scores(j)
        identifier = row["id"]
        asset = data["assets"][row["asset_id"]]
        creator = data["users"][row["created_by"]]
        assignee = data["users"].get(row["assignee_id"])
        j.update(id=identifier, number=row["number"], version=row["version"], workflowRevision=row["workflow_revision"], type=row["type"], status=row["status"], assetId=row["asset_id"], assetCode=asset["code"], assetName=asset["name"], hall=asset["hall"], line=asset["line"], createdBy=row["created_by"], createdByName=creator["name"], assigneeId=row["assignee_id"], assigneeName=assignee["name"] if assignee else None, workDate=row["work_date"], dueDate=row["due_date"], createdAt=row["created_at"], updatedAt=row["updated_at"])
        participants, approvals, grades, head_grades = (data[name].get(identifier, []) for name in ("participants", "approvals", "grades", "head_grades"))
        j["parts"] = [{"partId": p["part_id"], "code": p["code"], "name": p["name"], "unit": p["unit"], "quantity": value(p["quantity_units"])} for p in data["parts"].get(identifier, [])]
        j["approvals"] = []
        head = next((a for a in reversed(approvals) if a["stage"] == "head" and a["decision"] == "approve"), None)
        head_coefficient = stored_decimal(head["coefficient"]) if head and head["coefficient"] is not None else None
        any_grade_visible = any(self.can_see_grade(user, p["person_id"], p["specialty"]) for p in participants)
        head_grade_visible = bool(user and user.role == "head")
        j["headCoefficient"] = head_coefficient if head_grade_visible else None
        carried_approvals = {}
        for event in data["carried_approvals"].get(identifier, []):
            provenance = json_load(event["details"])
            carried_approvals[provenance["approvalId"]] = dict(
                carriedFromApprovalId=provenance["sourceApprovalId"], carriedFromRevision=provenance["sourceRevision"],
                carriedByName=event["actor_name"], carriedAt=event["created_at"], sourceActorName=provenance["sourceActorName"])
        for approval in approvals:
            visible = head_grade_visible if approval["stage"] == "head" else any(p["specialty"] == approval["specialty"] and self.can_see_grade(user, p["person_id"], p["specialty"]) for p in participants)
            j["approvals"].append({"id": approval["id"], "stage": approval["stage"], "specialty": approval["specialty"], "decision": approval["decision"], "coefficient": stored_decimal(approval["coefficient"]) if visible and approval["coefficient"] is not None else None, "reason": approval["reason"], "actorName": approval["name"], "createdAt": approval["created_at"]})
            if approval["id"] in carried_approvals:
                j["approvals"][-1].update(carried_approvals[approval["id"]])
                j["approvals"][-1]["actorName"] = carried_approvals[approval["id"]]["sourceActorName"]
        j["participants"], j["participantScores"], j["participantHeadScores"] = [], [], []
        score = Decimal(0)
        policies = {p["person_id"]: p["policy"] for p in data["policies"].get(identifier, [])}
        for person in participants:
            policy = policies.get(person["person_id"], "two-stage-v1")
            grade = next((g for g in grades if g["person_id"] == person["person_id"] and g["specialty"] == person["specialty"]), None)
            legacy = next((a for a in reversed(approvals) if a["stage"] == "supervisor" and a["specialty"] == person["specialty"] and a["decision"] == "approve" and a["coefficient"] is not None), None) if row["status"] == "completed" and grade is None else None
            visible = self.can_see_grade(user, person["person_id"], person["specialty"])
            coefficient = stored_decimal((grade or legacy)["coefficient"]) if visible and (grade or legacy) else None
            source = "individual" if coefficient is not None and grade else "legacy-specialty" if coefficient is not None else None
            head_grade = next((g for g in head_grades if g["person_id"] == person["person_id"] and g["specialty"] == person["specialty"]), None)
            personal_head = stored_decimal(head_grade["coefficient"]) if head_grade_visible and head_grade else head_coefficient if head_grade_visible else None
            head_source = "individual" if head_grade and head_grade_visible else "legacy-order" if personal_head is not None else None
            raw = participant_score(person["minutes"], coefficient, personal_head, policy) if row["status"] == "completed" else None
            j["participants"].append({"personId": person["person_id"], "personName": person["name"], "specialty": person["specialty"], "minutes": person["minutes"], "gradingPolicy": policy, "headGradeMaximum": 9 if policy == "head-only-v1" else 3, "supervisorCoefficient": coefficient, "supervisorScoreSource": source, "headCoefficient": personal_head, "headScoreSource": head_source, "finalScore": round_score(raw)})
            if raw is not None:
                score = decimal_add(score, round_score(raw) if source == "individual" or policy == "head-only-v1" else raw)
            if coefficient is not None:
                evidence = grade or legacy
                j["participantScores"].append({"personId": person["person_id"], "specialty": person["specialty"], "coefficient": coefficient, "actorName": evidence["actor_name"] if grade else evidence["name"], "createdAt": evidence["created_at"], "source": source})
            if personal_head is not None:
                evidence = head_grade or head
                j["participantHeadScores"].append({"personId": person["person_id"], "specialty": person["specialty"], "coefficient": personal_head, "actorName": evidence["actor_name"] if head_grade else evidence["name"], "createdAt": evidence["created_at"], "source": head_source})
        j["score"] = round_score(score) if head_grade_visible else None
        approved = {a["specialty"] for a in approvals if a["stage"] == "supervisor" and a["decision"] == "approve"}
        j["pendingSupervisorSpecialties"] = sorted({p["specialty"] for p in participants if policies.get(p["person_id"]) != "head-only-v1"} - approved)
        occurrence = data["occurrences"].get(identifier)
        can_edit = user is not None and row["status"] in {"draft", "returned"} and (self.management(user) or user.role == "planner" or user.id in {row["created_by"], row["assignee_id"]} or user.role == "supervisor" and (any(p["specialty"] == user.specialty for p in participants) or assignee and assignee["specialty"] == user.specialty))
        if occurrence:
            j.update(pmOriginalSupervisorId=occurrence["supervisor_id"], pmEffectiveSupervisorId=occurrence["effective_supervisor_id"],
                     pmCanTransferResponsibility=bool(user and user.active and user.role in {"head", "manager"} and not execution_started(row, occurrence, j, participants, approvals)))
            can_edit = bool(can_edit and user.role == "technician" and row["assignee_id"] == user.id)
            deadline = datetime.fromisoformat(occurrence["end_utc"].replace("Z", "+00:00"))
            clock = datetime.now(timezone.utc)
            overdue = row["status"] in {"draft", "returned"} and clock >= deadline
            executed = occurrence["executed_at"]
            late = row["status"] != "cancelled" and executed is not None and datetime.fromisoformat(executed.replace("Z", "+00:00")) >= deadline
            tehran = ZoneInfo("Asia/Tehran")
            state = "completed" if row["status"] == "completed" else "cancelled" if row["status"] == "cancelled" else "awaiting-approval" if row["status"] in {"head", "supervisor"} else "returned" if row["status"] == "returned" else ("unassigned-overdue" if clock >= deadline else "unassigned") if row["assignee_id"] is None else "unperformed" if clock >= deadline else "assigned"
            j.update(pmOverdue=overdue, pmOverdueDays=max(1, (clock.astimezone(tehran).date() - deadline.astimezone(tehran).date()).days + 1) if overdue else 0, pmLateExecution=late, pmSubmittedAt=occurrence["submitted_at"], pmExecutedAt=executed, pmExecutionState=state)
        all_graded = bool(participants) and all(any(g["person_id"] == p["person_id"] and g["specialty"] == p["specialty"] for g in grades) and p["specialty"] in approved for p in participants if policies.get(p["person_id"]) != "head-only-v1")
        can_decide = bool(user and (row["status"] == "supervisor" and user.role == "supervisor" and bool(user.specialty) and user.specialty in {p["specialty"] for p in participants if policies.get(p["person_id"]) != "head-only-v1"} and user.specialty not in approved or row["status"] == "head" and user.role == "head" and all_graded))
        if occurrence and user and user.role == "supervisor" and occurrence["effective_supervisor_id"] != user.id:
            can_decide = False
        j.update(canEdit=bool(can_edit), canSubmit=bool(can_edit), canDecide=can_decide,
                 canReviewCorrect=bool(can_decide and user and user.role == "supervisor" and row["status"] == "supervisor"),
                 decisionStage=row["status"] if can_decide else None, decisionSpecialty=user.specialty if can_decide and row["status"] == "supervisor" else None, canCancel=bool(user and self.management(user) and row["status"] not in {"completed", "cancelled"}))
        if detail:
            page, size = self.page_bounds(history_page, history_page_size, maximum=200)
            args = {"id": identifier, "size": size, "offset": (page - 1) * size}
            total = c.scalar("SELECT COUNT(*) FROM audit WHERE entity='order' AND entity_id=:id", args)
            history = []
            for entry in reversed(c.query("SELECT action,actor_name,details,created_at FROM audit WHERE entity='order' AND entity_id=:id ORDER BY created_at DESC,id DESC LIMIT :size OFFSET :offset", args)):
                raw_details = json_load(entry["details"])
                details = raw_details if head_grade_visible else self._without_personnel_scores(raw_details)
                if isinstance(raw_details.get("grades"), list):
                    details["grades"] = [g for g in raw_details["grades"] if (head_grade_visible if raw_details.get("stage") == "head" else self.can_see_grade(user, g.get("personId"), g.get("specialty")))]
                if raw_details.get("stage") == "supervisor" and any(p["specialty"] == raw_details.get("specialty") and self.can_see_grade(user, p["person_id"], p["specialty"]) for p in participants):
                    details["coefficient"] = raw_details.get("coefficient")
                if details.get("stage") == "head" and not head_grade_visible or details.get("stage") == "supervisor" and not any(p["specialty"] == details.get("specialty") and self.can_see_grade(user, p["person_id"], p["specialty"]) for p in participants):
                    details["coefficient"] = None
                history.append({"action": entry["action"], "actorName": entry["actor_name"], "details": details, "createdAt": entry["created_at"]})
            j.update(history=history, historyTotal=total, historyPage=page, historyPageSize=size, historyHasMore=args["offset"] + len(history) < total)
            if occurrence:
                if self.pm_service is None:
                    raise ApiError(503, "سرویس برگه PM آماده نیست.")
                sheet = self.pm_service.issued_template(c, identifier)
                j["pmSheet"] = sheet if head_grade_visible else self._without_personnel_scores(sheet)
        else:
            j.pop("pmSheet", None)
            j.pop("pmResults", None)
        return self._without_personnel_scores(j) if not any_grade_visible else j

    @staticmethod
    def _without_personnel_scores(value):
        """Scrub legacy embedded payloads as well as the current grade DTO.

        Operational names and times remain available to maintenance planners;
        personnel evaluations never cross that boundary, even in old snapshots.
        """
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                name = key.replace("_", "").casefold()
                if name in {"grades", "participantgrades", "participantscores", "participantheadscores"}:
                    cleaned[key] = []
                elif any(term in name for term in ("coefficient", "score", "grade", "performance")) or name in {"ranking", "rank"}:
                    cleaned[key] = None
                else:
                    cleaned[key] = WorkService._without_personnel_scores(item)
            return cleaned
        if isinstance(value, list):
            return [WorkService._without_personnel_scores(item) for item in value]
        return value

    @staticmethod
    def normalize(c, body, user, executed=False, *, preserve_assignment=False):
        WorkService._role(user)
        if not isinstance(body, dict):
            raise ApiError(400, "اطلاعات کار باید یک شیء معتبر باشد.")
        participant_items = items(body, "participants", 3 if user.role == "technician" else 20)
        part_items, checklist_items = items(body, "parts", 200), items(body, "checklist", 200)
        kind, asset = text(body, "type", 20), text(body, "assetId", 60)
        if kind not in {"PM", "EM", "PROJECT"}:
            raise ApiError(400, "نوع کار نامعتبر است.")
        if not c.scalar("SELECT COUNT(*) FROM assets WHERE id=:id", {"id": asset}):
            raise ApiError(400, "دستگاه معتبر انتخاب کنید.")
        title, description = text(body, "title", 300), text(body, "description", 10000)
        if len(title) < 3:
            raise ApiError(400, "عنوان کار حداقل سه نویسه باشد.")
        if executed and len(description) < 3:
            raise ApiError(400, "شرح کار تکمیل شود.")
        work_date, due = WorkService.date(text(body, "workDate", 10), "کار"), text(body, "dueDate", 10)
        WorkService.date(due, "موعد", True)
        start, end, duration = text(body, "startTime", 5), text(body, "endTime", 5), 0
        if start or end or executed:
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", end):
                raise ApiError(400, "زمان شروع و پایان را به قالب HH:mm وارد کنید.")
            first = int(start[:2]) * 60 + int(start[3:])
            last = int(end[:2]) * 60 + int(end[3:])
            duration = (last - first) % 1440
            if duration == 0:
                raise ApiError(400, "زمان شروع و پایان نباید یکسان باشد.")
        if executed:
            # A future planned draft is valid, but submitted execution must
            # already have finished. Resolve Tehran time and overnight work
            # consistently, including historical ambiguous/missing DST times.
            from .pm_calendar import local_execution_utc
            end_day = Date.fromisoformat(work_date) + timedelta(days=int(last < first))
            local_end = datetime.combine(end_day, datetime.min.time()) + timedelta(minutes=last)
            try:
                finished = local_execution_utc(local_end)
            except ValueError:
                raise ApiError(400, 'زمان واقعی پایان کار معتبر یا بدون ابهام نیست.') from None
            if finished > datetime.now(timezone.utc):
                raise ApiError(400, 'زمان پایان کار در آینده است؛ ساعت واقعی را ثبت کنید.')
        assignee = text(body, "assigneeId", 60)
        if assignee and not c.scalar("SELECT COUNT(*) FROM users WHERE id=:id AND active=1", {"id": assignee}):
            raise ApiError(400, "مجری تخصیص نامعتبر است.")
        if user.role == "technician" and assignee and assignee != user.id:
            raise ApiError(403, "تخصیص به دیگران با سرپرست است.")
        if user.role == "supervisor" and assignee and not preserve_assignment and not c.scalar("SELECT COUNT(*) FROM users WHERE id=:id AND specialty=:spec", {"id": assignee, "spec": user.specialty}):
            raise ApiError(403, "تخصیص فقط به مجریان تخصص شما مجاز است.")
        participants, seen = [], set()
        identifiers = [text(p, "personId", 60) for p in participant_items]
        membership, parameters = _in_list(identifiers)
        people = {p["id"]: p for p in c.query("SELECT id,specialty FROM users WHERE active=1 AND id IN " + membership, parameters)} if identifiers else {}
        for line, person_id in zip(participant_items, identifiers):
            if person_id in seen:
                raise ApiError(400, "پرسنل تکراری است.")
            seen.add(person_id)
            person = people.get(person_id)
            if person is None:
                raise ApiError(400, "پرسنل معتبر انتخاب کنید.")
            minutes = integer(line.get("minutes"))
            if not 1 <= minutes <= 1440 or duration and minutes > duration:
                raise ApiError(400, "زمان هر فرد باید بین ۱ تا ۱۴۴۰ دقیقه و حداکثر بازه کار باشد.")
            if not person["specialty"].strip():
                raise ApiError(400, "تخصص فرد در اطلاعات پایه مشخص نشده است.")
            participants.append({"personId": person_id, "minutes": minutes, "specialty": person["specialty"]})
        if executed and not participants:
            raise ApiError(400, "حداقل یک مجری انتخاب کنید.")
        if user.role == "technician" and participants and user.id not in seen:
            raise ApiError(403, "نام ثبت‌کننده باید در مجریان باشد.")
        part_ids = [text(p, "partId", 60) for p in part_items]
        membership, parameters = _in_list(part_ids)
        valid_parts = {p["id"] for p in c.query("SELECT id FROM parts WHERE id IN " + membership, parameters)} if part_ids else set()
        parts, seen_parts = [], set()
        for line, part_id in zip(part_items, part_ids):
            if part_id in seen_parts:
                raise ApiError(400, "قطعه تکراری است.")
            seen_parts.add(part_id)
            if part_id not in valid_parts:
                raise ApiError(400, "قطعه معتبر انتخاب کنید.")
            quantity = decimal_value(line.get("quantity", 0))
            if quantity <= 0:
                raise ApiError(400, "مقدار قطعه باید مثبت باشد.")
            units(quantity)
            parts.append({"partId": part_id, "quantity": quantity})
        downtime = integer(body.get("downtimeMinutes"))
        if not 0 <= downtime <= 1440:
            raise ApiError(400, "توقف باید بین صفر و ۱۴۴۰ دقیقه باشد.")
        checklist = []
        for item in checklist_items:
            label, done = text(item, "text", 500), boolean(item.get("done"))
            if not label:
                raise ApiError(400, "متن بند چک‌لیست نباید خالی باشد.")
            checklist.append({"text": label, "done": done})
        if executed and kind == "PM" and any(not item["done"] for item in checklist):
            raise ApiError(400, "همه موارد چک‌لیست PM باید بررسی و علامت‌گذاری شود.")
        return {"type": kind, "assetId": asset, "title": title, "description": description, "workDate": work_date, "dueDate": due or None, "startTime": start, "endTime": end, "durationMinutes": duration, "assigneeId": assignee or None, "participants": participants, "parts": parts, "noteType": text(body, "noteType", 100), "note": text(body, "note", 4000), "component": text(body, "component", 300), "downtimeMinutes": downtime, "productionStopped": boolean(body.get("productionStopped")), "checklist": checklist}

    @staticmethod
    def save_lines(c, identifier, body):
        c.execute("DELETE FROM participants WHERE order_id=:id", {"id": identifier})
        c.execute("DELETE FROM order_parts WHERE order_id=:id", {"id": identifier})
        for table, columns, lines in (("participants", ("person_id", "minutes", "specialty"), body["participants"]), ("order_parts", ("part_id", "quantity", "quantity_units"), body["parts"])):
            for start in range(0, len(lines), 100):
                args, values = {"order": identifier}, []
                for i, line in enumerate(lines[start:start + 100]):
                    if table == "participants":
                        args.update({f"v{i}a": line["personId"], f"v{i}b": line["minutes"], f"v{i}c": line["specialty"]})
                    else:
                        args.update({f"v{i}a": line["partId"], f"v{i}b": line["quantity"], f"v{i}c": units(line["quantity"])})
                    values.append(f"(:order,:v{i}a,:v{i}b,:v{i}c)")
                c.execute("INSERT INTO " + table + "(order_id," + ",".join(columns) + ") VALUES " + ",".join(values), args)

    def _require_edit(self, c, row, user):
        if row["status"] not in {"draft", "returned"}:
            raise ApiError(409, "ویرایش فقط برای پیش‌نویس یا کار برگشتی مجاز است.")
        if not self.can_edit(c, row, user):
            raise ApiError(403, "مجوز ویرایش این کار را ندارید.")

    def _preserve_pm(self, c, old, supplied, normalized, user, executed):
        if self.pm_service is not None:
            self.pm_service.preserve_execution(c, old, supplied, normalized, user, executed)
        elif c.scalar("SELECT COUNT(*) FROM pm_weekly_occurrences WHERE order_id=:id", {"id": old["id"]}):
            raise ApiError(503, "سرویس برگه PM آماده نیست.")

    def save(self, user, body, order_id=None, request_key=None):
        self._role(user)
        if not isinstance(body, dict):
            raise ApiError(400, "اطلاعات کار باید یک شیء معتبر باشد.")
        if request_key is not None and (not isinstance(request_key, str) or not 1 <= len(request_key) <= 128):
            raise ApiError(400, "شناسه درخواست نامعتبر است.")
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c, user)
            old = self.get_row(c, order_id, user) if order_id is not None else None
            if old:
                self.version(old, body.get("version", -1))
                self._require_edit(c, old, user)
            j = self.normalize(c, body, user)
            if old:
                original = json_load(old["body"])
                plan_id = original.get("planId")
                if user.role == "technician" and old["created_by"] != user.id and (j["assigneeId"] != old["assignee_id"] or j["dueDate"] != old["due_date"]):
                    raise ApiError(403, "مجری نمی‌تواند تخصیص یا موعد دستورکار را تغییر دهد.")
                if plan_id or user.role == "technician" and old["created_by"] != user.id:
                    if j["type"] != old["type"] or j["assetId"] != old["asset_id"]:
                        raise ApiError(403, "نوع و دستگاه دستورکار تخصیصی را مجری نمی‌تواند تغییر دهد.")
                    if [i["text"] for i in j["checklist"]] != [i["text"] for i in original.get("checklist", [])]:
                        raise ApiError(403, "بندهای چک‌لیست دستورکار باید حفظ شوند؛ فقط نتیجه بررسی را ثبت کنید.")
                if plan_id:
                    j["planId"] = plan_id
                self._preserve_pm(c, old, body, j, user, False)
            from .pm_hash_json import canonical_json
            digest = hashlib.sha256(canonical_json(j).encode("utf-8")).hexdigest().upper()
            if old is None and request_key:
                claim = c.one("SELECT order_id,payload_hash FROM request_claims WHERE actor_id=:actor AND request_key=:key", {"actor": user.id, "key": request_key})
                if claim:
                    if claim["payload_hash"] != digest:
                        raise ApiError(409, "این درخواست قبلاً با محتوای دیگری ثبت شده است. فهرست کارها را بررسی کنید.")
                    return self.view(c, self.get_row(c, claim["order_id"], user), user, detail=True)
            identifier = order_id or str(uuid4())
            stamp = now()
            args = {"id": identifier, "type": j["type"], "asset": j["assetId"], "assignee": j["assigneeId"], "date": j["workDate"], "due": j["dueDate"], "body": json_dump(j), "title": j["title"], "description": j["description"], "duration": j["durationMinutes"], "now": stamp}
            if old is None:
                args.update(number=f"{j['type']}-{j['workDate'].replace('-', '')}-{identifier.replace('-', '')}", creator=user.id)
                c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
VALUES(:id,:number,1,1,:type,'draft',:asset,:creator,:assignee,:date,:due,:body,:title,:description,:duration,:now,:now)""", args)
                if request_key:
                    c.execute("INSERT INTO request_claims(actor_id,request_key,order_id,payload_hash) VALUES(:actor,:key,:id,:hash)", {"actor": user.id, "key": request_key, "id": identifier, "hash": digest})
            else:
                args["version"] = old["version"]
                changed = c.execute("""UPDATE orders SET type=:type,asset_id=:asset,assignee_id=:assignee,work_date=:date,due_date=:due,body=:body,title=:title,description=:description,duration_minutes=:duration,updated_at=:now,version=version+1 WHERE id=:id AND version=:version""", args)
                if changed != 1:
                    raise ApiError(409, "کار هم‌زمان تغییر کرده است.")
            self.save_lines(c, identifier, j)
            audit(c, user, "create" if old is None else "edit", "order", identifier, {"revision": old["workflow_revision"] if old else 1, "values": j})
            return self.view(c, c.one("SELECT * FROM orders WHERE id=:id", {"id": identifier}), user, detail=True)
        return self.db.run_transaction(operation)

    @staticmethod
    def _submission_policies(c, order_id, revision, stamp):
        # Freeze actual performer roles for this submitted revision. Current
        # account roles must never reinterpret an old evaluation policy.
        suffix = "" if c.is_sqlite else " FOR SHARE OF u"
        rows = c.query("SELECT p.person_id,p.specialty,u.role,u.active,u.specialty AS current_specialty FROM participants p JOIN users u ON u.id=p.person_id WHERE p.order_id=:id ORDER BY u.id" + suffix, {"id": order_id})
        if not rows or any(not p["active"] or p["specialty"] != p["current_specialty"] for p in rows):
            raise ApiError(409, "اطلاعات انجام‌دهندگان تغییر کرده است؛ گزارش را بازبینی کنید.")
        policies = []
        for person in rows:
            policy = "head-only-v1" if person["role"] == "supervisor" else "two-stage-v1"
            c.execute("INSERT INTO participant_grading_policies(order_id,revision,person_id,specialty,role_at_submission,policy,created_at) VALUES(:id,:rev,:person,:spec,:role,:policy,:stamp)",
                      {"id": order_id, "rev": revision, "person": person["person_id"], "spec": person["specialty"], "role": person["role"], "policy": policy, "stamp": stamp})
            policies.append(policy)
        return policies

    def submit(self, user, order_id, version):
        self._role(user)
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c, user)
            row = self.get_row(c, order_id, user)
            self.version(row, version)
            self._require_edit(c, row, user)
            original = json_load(row["body"])
            j = self.normalize(c, original, user, executed=True)
            if original.get("planId"):
                j["planId"] = original["planId"]
            self._preserve_pm(c, row, original, j, user, True)
            args = {"id": order_id, "version": version, "body": json_dump(j), "title": j["title"], "description": j["description"], "duration": j["durationMinutes"], "now": now()}
            if c.execute("UPDATE orders SET status='supervisor',body=:body,title=:title,description=:description,duration_minutes=:duration,version=version+1,workflow_revision=workflow_revision+1,updated_at=:now WHERE id=:id AND version=:version", args) != 1:
                raise ApiError(409, "کار هم‌زمان تغییر کرده است.")
            self.save_lines(c, order_id, j)
            policies = self._submission_policies(c, order_id, row["workflow_revision"] + 1, args["now"])
            if all(policy == "head-only-v1" for policy in policies):
                c.execute("UPDATE orders SET status='head' WHERE id=:id", {"id": order_id})
            executed_at = self.pm_service.execution_end(j) if j.get("pmWeeklyPlanId") and self.pm_service else None
            c.execute("UPDATE pm_weekly_occurrences SET submitted_at=:now,executed_at=:date WHERE order_id=:id", {"id": order_id, "now": args["now"], "date": executed_at})
            from .pm_archive import capture_submission
            submitted_row = c.one("SELECT * FROM orders WHERE id=:id", {"id": order_id})
            capture_submission(c, submitted_row, j, user, args["now"])
            audit(c, user, "submit", "order", order_id, {"revision": row["workflow_revision"] + 1}, created_at=args["now"])
            return self.view(c, c.one("SELECT * FROM orders WHERE id=:id", {"id": order_id}), user, detail=True)
        return self.db.run_transaction(operation)

    def decision(self, user, order_id, body):
        self._role(user)
        if not isinstance(body, dict):
            raise ApiError(400, "تصمیم نامعتبر است.")
        return self.db.run_transaction(lambda c: self._decision_in_transaction(c, user, order_id, body))

    def _decision_in_transaction(self, c, user, order_id, body):
        """Record a decision inside the caller's existing atomic transaction."""
        from .security import assert_live_actor
        assert_live_actor(c, user)
        row = self.get_row(c, order_id, user)
        self.version(row, body.get("version", -1))
        stage, spec = row["status"], ""
        if stage == "supervisor":
            if user.role != "supervisor" or not user.specialty:
                raise ApiError(403, "این مرحله به تأیید سرپرست تخصص نیاز دارد.")
            spec = user.specialty
            if not c.scalar("SELECT COUNT(*) FROM participants p WHERE p.order_id=:id AND p.specialty=:spec AND " + _needs_supervisor(), {"id": order_id, "spec": spec, "rev": row["workflow_revision"]}):
                raise ApiError(403, "تخصص این کار با سرپرستی شما تطبیق ندارد.")
            if c.scalar("SELECT COUNT(*) FROM approvals WHERE order_id=:id AND revision=:rev AND stage='supervisor' AND specialty=:spec AND decision='approve'", {"id": order_id, "rev": row["workflow_revision"], "spec": spec}):
                raise ApiError(409, "تخصص شما در این نسخه قبلاً تأیید شده است.")
        elif stage == "head":
            if user.role != "head":
                raise ApiError(403, "این مرحله به تأیید رئیس نیاز دارد.")
        else:
            raise ApiError(409, "کار در مرحله قابل تصمیم‌گیری نیست.")
        if not self.can_decide(c, row, user):
            raise ApiError(403, "این کار در کارتابل تأیید شما نیست.")
        decision, reason = text(body, "decision", 20), text(body, "reason", 2000)
        if decision != "approve":
            raise ApiError(400, "مسیر برگشت برای اصلاح حذف شده است؛ سرپرست اصلاح و تأیید را همان‌جا ثبت می‌کند.")
        coefficient, grades = None, []
        if decision == "approve" and stage == "supervisor":
            if body.get("coefficient") is not None:
                raise ApiError(400, "برای هر مجری تخصص خود نمره مستقل ثبت کنید؛ ضریب گروهی مجاز نیست.")
            if not isinstance(body.get("grades"), list) or len(body["grades"]) > 20:
                raise ApiError(400, "نمره مستقل همه مجریان تخصص خود را وارد کنید.")
            expected = {p["person_id"] for p in c.query("SELECT p.person_id FROM participants p WHERE p.order_id=:id AND p.specialty=:spec AND " + _needs_supervisor(), {"id": order_id, "spec": spec, "rev": row["workflow_revision"]})}
            seen = set()
            for grade in body["grades"]:
                if not isinstance(grade, dict):
                    raise ApiError(400, "نمره معتبر وارد کنید.")
                person = text(grade, "personId", 60)
                if person not in expected:
                    raise ApiError(403, "ارزیابی فقط برای مجریان تخصص شما در همین گزارش مجاز است.")
                if person == user.id:
                    raise ApiError(403, "نمره‌دادن به کار خود مجاز نیست.")
                if person in seen:
                    raise ApiError(400, "نمره مجری تکراری است.")
                seen.add(person)
                score = decimal_value(grade.get("coefficient"))
                if not 0 <= score <= 3:
                    raise ApiError(400, "نمره هر مجری باید عددی بین صفر و سه باشد.")
                grades.append({"personId": person, "specialty": spec, "coefficient": score})
            if seen != expected:
                raise ApiError(400, "پیش از تأیید باید همه مجریان تخصص خود را جداگانه ارزیابی کنید.")
        elif decision == "approve" and stage == "head":
            if body.get("grades") is not None or body.get("coefficient") is not None:
                raise ApiError(400, "رئیس باید برای هر مجری نمره مستقل ثبت کند؛ ضریب گروهی مجاز نیست.")
            if not isinstance(body.get("participantScores"), list) or len(body["participantScores"]) > 20:
                raise ApiError(400, "نمره مستقل همه مجریان را وارد کنید.")
            expected = {p["person_id"]: p for p in c.query("SELECT p.person_id,p.specialty,COALESCE(gp.policy,'two-stage-v1') AS policy FROM participants p LEFT JOIN participant_grading_policies gp ON gp.order_id=p.order_id AND gp.person_id=p.person_id AND gp.revision=:rev WHERE p.order_id=:id", {"id": order_id, "rev": row["workflow_revision"]})}
            seen = set()
            for grade in body["participantScores"]:
                if not isinstance(grade, dict):
                    raise ApiError(400, "نمره معتبر وارد کنید.")
                person = text(grade, "personId", 60)
                if person not in expected:
                    raise ApiError(403, "ارزیابی فقط برای مجریان همین گزارش مجاز است.")
                if person == user.id:
                    raise ApiError(403, "نمره‌دادن به کار خود مجاز نیست.")
                if person in seen:
                    raise ApiError(400, "نمره مجری تکراری است.")
                seen.add(person)
                score = decimal_value(grade.get("coefficient"))
                maximum = 9 if expected[person]["policy"] == "head-only-v1" else 3
                if not 0 <= score <= maximum:
                    raise ApiError(400, f"نمره این انجام‌دهنده باید بین صفر و {maximum} باشد.")
                digits, exponent = score.as_tuple().digits, score.as_tuple().exponent
                fractional_places = max(0, -exponent)
                trailing_zeros = 0
                for digit in reversed(digits):
                    if digit != 0:
                        break
                    trailing_zeros += 1
                if score and fractional_places - trailing_zeros > 28:
                    raise ApiError(400, "نمره هر مجری حداکثر ۲۸ رقم اعشار دارد.")
                canonical = format(score, "f").rstrip("0").rstrip(".") if score and exponent < 0 else format(score, "f") if score else "0"
                grades.append({"personId": person, "specialty": expected[person]["specialty"], "coefficient": Decimal(canonical)})
            if seen != set(expected):
                raise ApiError(400, "پیش از تأیید باید همه مجریان را جداگانه ارزیابی کنید.")
        approval_id, stamp = str(uuid4()), now()
        c.execute("INSERT INTO approvals(id,order_id,revision,stage,specialty,decision,coefficient,reason,actor_id,created_at) VALUES(:approval,:id,:rev,:stage,:spec,:decision,:coefficient,:reason,:actor,:now)", {"approval": approval_id, "id": order_id, "rev": row["workflow_revision"], "stage": stage, "spec": spec, "decision": decision, "coefficient": coefficient, "reason": reason, "actor": user.id, "now": stamp})
        for grade in grades:
            table = "participant_head_scores" if stage == "head" else "participant_scores"
            c.execute("INSERT INTO " + table + "(order_id,revision,person_id,specialty,coefficient,actor_id,approval_id,created_at) VALUES(:id,:rev,:person,:spec,:coefficient,:actor,:approval,:now)", {"id": order_id, "rev": row["workflow_revision"], "person": grade["personId"], "spec": grade["specialty"], "coefficient": format(grade["coefficient"], "f") if stage == "head" else grade["coefficient"], "actor": user.id, "approval": approval_id, "now": stamp})
        next_stage = "head" if stage == "supervisor" and self.all_participants_graded(c, row) else "completed" if stage == "head" else stage
        if c.execute("UPDATE orders SET status=:next,version=version+1,updated_at=:now WHERE id=:id AND version=:version", {"id": order_id, "version": row["version"], "next": next_stage, "now": now()}) != 1:
            raise ApiError(409, "کار هم‌زمان تغییر کرده است.")
        if next_stage == "completed":
            capture_consumption(c, order_id)
            capture_scores(c, order_id)
        audit(c, user, decision, "order", order_id, {"approvalId": approval_id, "stage": stage, "specialty": spec, "coefficient": coefficient, "grades": grades, "reason": reason, "from": stage, "to": next_stage, "revision": row["workflow_revision"]}, created_at=stamp)
        return self.view(c, c.one("SELECT * FROM orders WHERE id=:id", {"id": order_id}), user, detail=True)

    def cancel(self, user, order_id, body):
        self._role(user)
        if not self.management(user):
            raise ApiError(403, "لغو با مدیر واحد است.")
        if not isinstance(body, dict):
            raise ApiError(400, "اطلاعات لغو معتبر نیست.")
        def operation(c):
            from .security import assert_live_actor
            assert_live_actor(c, user)
            row = self.get_row(c, order_id, user)
            self.version(row, body.get("version", -1))
            if row["status"] in {"completed", "cancelled"}:
                raise ApiError(409, "کار نهایی یا لغوشده قابل لغو نیست؛ سابقه تأییدشده باید حفظ شود.")
            reason = text(body, "reason", 2000)
            if len(reason) < 3:
                raise ApiError(400, "علت لغو الزامی است.")
            if c.execute("UPDATE orders SET status='cancelled',version=version+1,updated_at=:now WHERE id=:id AND version=:version", {"id": order_id, "version": row["version"], "now": now()}) != 1:
                raise ApiError(409, "کار هم‌زمان تغییر کرده است.")
            audit(c, user, "cancel", "order", order_id, {"reason": reason})
            return self.view(c, c.one("SELECT * FROM orders WHERE id=:id", {"id": order_id}), user, detail=True)
        return self.db.run_transaction(operation)

    def generate_pm(self, actor=None):
        """Legacy interval-day plans; missed occurrences advance one interval."""
        if actor is not None:
            self._role(actor)
            if actor.role == "technician":
                raise ApiError(403, "صدور برنامه با سرپرست یا مدیر واحد است.")
        def operation(c):
            if actor is not None:
                from .security import assert_live_actor
                assert_live_actor(c, actor)
            assigned = "json_extract(p.body,'$.assigneeId')" if c.is_sqlite else "(p.body::jsonb->>'assigneeId')"
            specialty_scope = " AND assigned.specialty=:specialty" if actor and actor.role == "supervisor" else ""
            plans = c.query("SELECT p.* FROM plans p JOIN users assigned ON assigned.id=" + assigned + " AND assigned.active=1 WHERE p.active=1 AND p.next_due<=:today" + specialty_scope + " ORDER BY p.next_due,p.id LIMIT 200", {"today": today(), "specialty": actor.specialty if actor else ""})
            creator = c.scalar("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1") if plans else None
            if plans and creator is None:
                raise ApiError(409, "مدیر فعال وجود ندارد.")
            count = 0
            for plan in plans:
                due, plan_id, body = plan["next_due"], plan["id"], json_load(plan["body"])
                interval = plan["interval_days"]
                if type(interval) is not int or not 1 <= interval <= 3660:
                    raise ApiError(409, "فاصله برنامه PM معتبر نیست؛ برنامه را بازبینی کنید.")
                next_due = (Date.fromisoformat(self.date(due, "موعد")) + timedelta(days=interval)).isoformat()
                existing = c.scalar("SELECT COUNT(*) FROM pm_occurrences WHERE plan_id=:plan AND due_date=:due", {"plan": plan_id, "due": due})
                if not existing:
                    identifier, stamp = str(uuid4()), now()
                    assignee = body.get("assigneeId") or None
                    j = {"type": "PM", "assetId": plan["asset_id"], "title": body.get("title", ""), "description": body.get("description", ""), "workDate": due, "dueDate": due, "startTime": "", "endTime": "", "durationMinutes": 0, "assigneeId": assignee, "participants": [], "parts": [], "checklist": body.get("checklist", []), "planId": plan_id, "downtimeMinutes": 0, "productionStopped": False}
                    c.execute("""INSERT INTO orders(id,number,version,workflow_revision,type,status,asset_id,created_by,assignee_id,work_date,due_date,body,title,description,duration_minutes,created_at,updated_at)
VALUES(:id,:number,1,1,'PM','draft',:asset,:creator,:assignee,:due,:due,:body,:title,:description,0,:stamp,:stamp)""", {"id": identifier, "number": "PM-" + due.replace("-", "") + "-" + identifier.replace("-", ""), "asset": plan["asset_id"], "creator": creator, "assignee": assignee, "due": due, "body": json_dump(j), "title": j["title"], "description": j["description"], "stamp": stamp})
                    c.execute("INSERT INTO pm_occurrences(plan_id,due_date,order_id) VALUES(:plan,:due,:order)", {"plan": plan_id, "due": due, "order": identifier})
                    audit(c, actor, "generate-pm", "order", identifier, {"planId": plan_id, "due": due})
                    count += 1
                body.update(nextDueDate=next_due, version=plan["version"] + 1)
                if c.execute("UPDATE plans SET next_due=:next,version=version+1,body=:body WHERE id=:id AND version=:version", {"next": next_due, "body": json_dump(body), "id": plan_id, "version": plan["version"]}) != 1:
                    raise ApiError(409, "برنامه PM هم‌زمان تغییر کرده است؛ ایجاد کار دوباره اجرا شود.")
                if existing:
                    audit(c, actor, "advance-existing-pm", "plan", plan_id, {"due": due, "next": next_due})
            return count
        return self.db.run_transaction(operation)
