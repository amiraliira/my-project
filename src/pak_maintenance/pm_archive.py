"""Immutable PM submissions and bounded, grade-free retrieval for PM planners.

Only execution answers and frozen human/equipment labels are copied. The large
original layout remains in its immutable, foreign-keyed template revision.
"""
from copy import deepcopy
import hashlib
from uuid import uuid4

from .common import ApiError, dumps, loads
from .permissions import may_view_pm_archive


ARCHIVE_COLUMNS = ("id order_id workflow_revision template_id template_version plan_id plan_version asset_id performer_id supervisor_id submitted_by number title hall asset_code asset_name performer_name specialty work_date executed_at submitted_at body body_sha256 evidence_source").split()
SUMMARY_COLUMNS = tuple(column for column in ARCHIVE_COLUMNS if column != "body")
STATUS_SQL = "CASE WHEN a.workflow_revision<o.workflow_revision THEN 'superseded' ELSE o.status END"


def require_archive(user):
    if not may_view_pm_archive(user):
        raise ApiError(403, "بایگانی برگه‌های PM فقط در دسترس کارشناس نت است.")


def _execution(c, body):
    execution = {key: deepcopy(body.get(key)) for key in
                 ("description", "workDate", "startTime", "endTime", "durationMinutes", "downtimeMinutes", "productionStopped")}
    execution["participants"] = []
    for person in body.get("participants", []):
        name = c.scalar("SELECT name FROM users WHERE id=:id", {"id": person["personId"]})
        execution["participants"].append({"personId": person["personId"], "personName": name,
                                           "specialty": person["specialty"], "minutes": person["minutes"]})
    execution["parts"] = []
    parts = body.get("parts", [])
    if parts:
        from .work_service import _in_list
        membership, args = _in_list([part["partId"] for part in parts])
        labels = {row["id"]: row for row in c.query("SELECT id,code,name,unit FROM parts WHERE id IN " + membership, args)}
        for part in parts:
            row = labels[part["partId"]]
            execution["parts"].append(dict(partId=part["partId"], code=row["code"], name=row["name"], unit=row["unit"], quantity=part["quantity"]))
    return execution


def capture_submission(c, row, body, user, stamp, *, correction=None):
    """Called after the revision transition, inside the submission transaction."""
    occurrence = c.one("SELECT * FROM pm_weekly_occurrences WHERE order_id=:id", {"id": row["id"]})
    if occurrence is None:
        return
    from .pm_service import execution_end
    asset = c.one("SELECT code,name,hall FROM assets WHERE id=:id", {"id": row["asset_id"]})
    performer = c.one("SELECT id,name FROM users WHERE id=:id", {"id": row["assignee_id"]})
    if performer is None:
        raise ApiError(409, "مجری معتبر برای بایگانی برگه پیدا نشد.")
    execution = _execution(c, body)
    members = [dict(assetId=member["id"], code=member["code"], name=member["name"], hall=member["hall"]) for member in c.query("SELECT x.id,x.code,x.name,x.hall FROM pm_weekly_assets m JOIN assets x ON x.id=m.asset_id WHERE m.order_id=:id ORDER BY x.code,x.id", {"id": row["id"]})]
    evidence_source = "supervisor-correction-snapshot" if correction is not None else "submission-snapshot"
    snapshot = dict(execution=execution, results=deepcopy(body.get("pmResults", {})),
                    window=deepcopy(body.get("pmWindow")), equipmentMembers=members,
                    submittedByName=c.scalar("SELECT name FROM users WHERE id=:id", {"id": user.id}), identityCapturedAt=stamp,
                    provenance=evidence_source)
    if correction is not None:
        snapshot["correction"] = deepcopy(correction)
    from .pm_responsibility import effective_owner
    owner = effective_owner(c, row["id"], occurrence["supervisor_id"])
    snapshot["responsibility"] = dict(originalSupervisorId=occurrence["supervisor_id"],
        originalSupervisorName=c.scalar("SELECT name FROM users WHERE id=:id", {"id": occurrence["supervisor_id"]}),
        effectiveSupervisorId=owner, effectiveSupervisorName=c.scalar("SELECT name FROM users WHERE id=:id", {"id": owner}),
        transferIds=[entry["id"] for entry in c.query("SELECT id FROM pm_responsibility_transfers WHERE order_id=:id ORDER BY order_version", {"id": row["id"]})])
    base = None
    if correction is not None:
        base = c.one("SELECT * FROM pm_submission_archive WHERE id=:id AND order_id=:order AND workflow_revision=:revision",
                     {"id": correction["baseArchiveId"], "order": row["id"], "revision": correction["baseRevision"]})
        if base is None or hashlib.sha256(base["body"].encode("utf-8")).hexdigest() != base["body_sha256"]:
            raise ApiError(409, "نسخهٔ مبنای اصلاح PM معتبر نیست.")
        base_snapshot = loads(base["body"])
        frozen_people = {person["personId"]: person for person in base_snapshot.get("execution", {}).get("participants", [])}
        for person in execution["participants"]:
            previous = frozen_people.get(person["personId"])
            if previous is not None:
                person["personName"] = previous.get("personName")
                person["specialty"] = previous.get("specialty")
        for field in ("window", "equipmentMembers", "responsibility"):
            snapshot[field] = deepcopy(base_snapshot.get(field))
    record = dict(id=str(uuid4()), order_id=row["id"], workflow_revision=row["workflow_revision"],
        template_id=occurrence["template_id"], template_version=occurrence["template_version"],
        plan_id=occurrence["plan_id"], plan_version=occurrence["plan_version"],
        asset_id=row["asset_id"], performer_id=performer["id"], supervisor_id=occurrence["supervisor_id"],
        submitted_by=user.id, number=row["number"], title=row["title"], hall=asset["hall"],
        asset_code=asset["code"], asset_name=asset["name"], performer_name=performer["name"],
        specialty=occurrence["specialty"], work_date=row["work_date"],
        executed_at=execution_end(body), submitted_at=stamp,
        body=dumps(snapshot), evidence_source=evidence_source)
    if base is not None:
        for field in ("number", "title", "hall", "asset_code", "asset_name", "performer_name", "specialty"):
            record[field] = base[field]
    _insert(c, record)


def _insert(c, record):
    record["body_sha256"] = hashlib.sha256(record["body"].encode("utf-8")).hexdigest()
    c.execute("INSERT INTO pm_submission_archive(" + ",".join(ARCHIVE_COLUMNS) + ") VALUES(" +
              ",".join(":" + key for key in ARCHIVE_COLUMNS) + ")", record)


class PmArchiveService:
    def __init__(self, db):
        self.db = db

    def _filters(self, q=None, hall=None, asset_id=None, template_id=None, person_id=None,
                 status=None, from_date=None, to_date=None, order_id=None):
        from .work_service import WorkService
        args, clauses = {}, []
        for key, raw, maximum in (("q", q, 300), ("hall", hall, 100), ("asset", asset_id, 100),
            ("template", template_id, 100), ("person", person_id, 100), ("order", order_id, 100)):
            if raw is not None and (not isinstance(raw, str) or len(raw) > maximum or "\x00" in raw):
                raise ApiError(400, "فیلتر بایگانی نامعتبر است.")
            args[key] = raw.strip() if raw else None
        if args["q"]:
            args["q"] = "%" + args["q"].lower().replace("~", "~~").replace("%", "~%").replace("_", "~_") + "%"
            # Group forms must remain findable by every equipment member's
            # original label, even after the current master data is renamed.
            members = ("json_each(a.body,'$.equipmentMembers') m" if self.db.is_sqlite else
                       "jsonb_array_elements(a.body::jsonb->'equipmentMembers') m")
            code = "json_extract(m.value,'$.code')" if self.db.is_sqlite else "m.value->>'code'"
            name = "json_extract(m.value,'$.name')" if self.db.is_sqlite else "m.value->>'name'"
            clauses.append("(" + " OR ".join("LOWER(a." + column + ") LIKE :q ESCAPE '~'" for column in
                ("number", "title", "hall", "asset_code", "asset_name", "performer_name")) +
                " OR EXISTS(SELECT 1 FROM " + members + " WHERE LOWER(" + code + ") LIKE :q ESCAPE '~' OR LOWER(" + name + ") LIKE :q ESCAPE '~'))")
        for key, column in (("hall", "hall"), ("asset", "asset_id"), ("template", "template_id"),
                            ("person", "performer_id"), ("order", "order_id")):
            if args[key]:
                if key == "asset":
                    clauses.append("(a.asset_id=:asset OR EXISTS(SELECT 1 FROM pm_weekly_assets m WHERE m.order_id=a.order_id AND m.asset_id=:asset))")
                else:
                    clauses.append("a." + column + "=:" + key)
        for key, raw, operator in (("from_date", from_date, ">="), ("to_date", to_date, "<=")):
            args[key] = WorkService.date(raw, "بایگانی", optional=True) if raw else None
            if args[key]: clauses.append("a.work_date" + operator + ":" + key)
        if args["from_date"] and args["to_date"] and args["from_date"] > args["to_date"]:
            raise ApiError(400, "تاریخ پایان باید پس از تاریخ شروع باشد.")
        if status not in (None, "", "all", "supervisor", "head", "awaiting-approval", "completed", "returned", "cancelled", "superseded"):
            raise ApiError(400, "وضعیت بایگانی نامعتبر است.")
        if status == "awaiting-approval":
            clauses.append("a.workflow_revision=o.workflow_revision AND o.status IN ('supervisor','head')")
        elif status and status != "all":
            args["status"] = status
            clauses.append("(" + STATUS_SQL + ")=:status")
        return args, " AND ".join(clauses) or "1=1"

    @staticmethod
    def _summary(row):
        return dict(id=row["id"], orderId=row["order_id"], number=row["number"], title=row["title"],
            status=row["archive_status"], currentStatus=row["current_status"], workflowRevision=row["workflow_revision"],
            templateId=row["template_id"], templateVersion=row["template_version"], planId=row["plan_id"],
            planVersion=row["plan_version"], hall=row["hall"], assetId=row["asset_id"], assetCode=row["asset_code"],
            assetName=row["asset_name"], performerId=row["performer_id"], performerName=row["performer_name"],
            specialty=row["specialty"], workDate=row["work_date"], executedAt=row["executed_at"],
            submittedAt=row["submitted_at"], approvedAt=row["approved_at"], evidenceSource=row["evidence_source"],
            snapshotSha256=row["body_sha256"], lateExecution=row["executed_at"] >= row["end_utc"])

    @staticmethod
    def _select(body=False):
        columns = ARCHIVE_COLUMNS if body else SUMMARY_COLUMNS
        return "SELECT " + ",".join("a." + key for key in columns) + ",o.status AS current_status," + STATUS_SQL + " AS archive_status,w.end_utc," + \
            "(SELECT MAX(p.created_at) FROM approvals p WHERE p.order_id=a.order_id AND p.revision=a.workflow_revision AND p.stage='head' AND p.decision='approve') AS approved_at" + \
            " FROM pm_submission_archive a JOIN orders o ON o.id=a.order_id JOIN pm_weekly_occurrences w ON w.order_id=a.order_id"

    def list_page(self, user, page=None, page_size=None, **filters):
        require_archive(user)
        from .work_service import WorkService
        page, size = WorkService.page_bounds(page, page_size)
        args, where = self._filters(**filters)
        args.update(size=size, offset=(page - 1) * size)
        with self.db.transaction(write=False) as c:
            from .security import assert_live_actor
            assert_live_actor(c, user, lock=False)
            total = c.scalar("SELECT COUNT(*) FROM pm_submission_archive a JOIN orders o ON o.id=a.order_id WHERE " + where, args)
            rows = c.query(self._select() + " WHERE " + where + " ORDER BY a.submitted_at DESC,a.id DESC LIMIT :size OFFSET :offset", args)
        return dict(items=[self._summary(row) for row in rows], total=total, page=page,
                    pageSize=size, totalPages=(total + size - 1) // size)

    def detail(self, user, identifier):
        require_archive(user)
        from .work_service import WorkService
        with self.db.transaction(write=False) as c:
            from .security import assert_live_actor
            assert_live_actor(c, user, lock=False)
            row = c.one(self._select(True) + " WHERE a.id=:id", dict(id=identifier))
            if row is None: raise ApiError(404, "برگه بایگانی پیدا نشد.")
            if hashlib.sha256(row["body"].encode("utf-8")).hexdigest() != row["body_sha256"]:
                raise ApiError(409, "یکپارچگی نسخه بایگانی تأیید نشد.")
            snapshot = loads(row["body"])
            sheet = loads(c.scalar("SELECT body FROM pm_template_revisions WHERE template_id=:id AND revision=:revision", dict(id=row["template_id"], revision=row["template_version"])))
            args = dict(order=row["order_id"], revision=row["workflow_revision"])
            approvals = [dict(id=item["id"], stage=item["stage"], specialty=item["specialty"], decision=item["decision"], reason=item["reason"], actorId=item["actor_id"], createdAt=item["created_at"], actorName=item["name"]) for item in c.query("SELECT a.id,a.stage,a.specialty,a.decision,a.reason,a.actor_id,a.created_at,u.name FROM approvals a JOIN users u ON u.id=a.actor_id WHERE a.order_id=:order AND a.revision=:revision ORDER BY a.created_at,a.id", args)]
            # Approval names are captured in the append-only audit in the same
            # transaction; use those names instead of renamed current accounts.
            for approval in approvals:
                events = c.query("SELECT actor_name,details FROM audit WHERE entity='order' AND entity_id=:order AND actor_id=:actor AND action=:action AND created_at>=:stamp ORDER BY created_at,id LIMIT 2", dict(args, actor=approval["actorId"], action=approval["decision"], stamp=approval["createdAt"]))
                matching = next((event for event in events if loads(event["details"]).get("approvalId") == approval["id"] or loads(event["details"]).get("revision") == row["workflow_revision"]), None)
                if matching: approval["actorName"] = matching["actor_name"]
            rows = c.query(self._select() + " WHERE a.order_id=:order ORDER BY a.workflow_revision DESC LIMIT 50", args)
            revisions_total = c.scalar("SELECT COUNT(*) FROM pm_submission_archive WHERE order_id=:order", args)
            entry = self._summary(row)
            entry.update(submittedByName=snapshot.get("submittedByName"), **{key:snapshot.get("execution", {}).get(key) for key in ("startTime", "endTime", "durationMinutes")})
            result = dict(entry=entry, sheet=sheet, results=snapshot.get("results", {}),
                execution=snapshot.get("execution", {}), window=snapshot.get("window"), responsibility=snapshot.get("responsibility"),
                correction=snapshot.get("correction"), provenance=snapshot.get("provenance"),
                equipmentMembers=snapshot.get("equipmentMembers", []), approvals=approvals,
                revisions=[self._summary(item) for item in rows], revisionsTotal=revisions_total,
                revisionsHasMore=revisions_total > len(rows), identityCapturedAt=snapshot.get("identityCapturedAt"))
            # The archive is an operational audit surface. Grades never cross
            # it, including any malicious/legacy embedded template metadata.
            return WorkService._without_personnel_scores(result)

    def original(self, user, identifier, seed_directory):
        detail = self.detail(user, identifier)
        from .pm_service import PmService
        return PmService._original(seed_directory, detail["sheet"])
