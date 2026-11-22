"""Read-only, bounded work activity aggregates and scoped report drilldowns."""
from datetime import date

from .common import ApiError, text
from .em_downtime import safe_reporting_body
from .work_service import WorkService


REPORT_VERSION = "completed-work-activities-v1"
TYPE_ORDER = ("PM", "EM", "CM", "GENERAL", "PROJECT")
METRICS = {
    "activityCount": "activity_count",
    "workMinutes": "work_minutes",
    "personMinutes": "person_minutes",
    "downtimeMinutes": "downtime_minutes",
}
ROLES = {"head", "manager", "planner", "supervisor", "technician"}


class ActivityReport:
    def __init__(self, database, work):
        self.db, self.work = database, work

    @staticmethod
    def parameters(viewer, start, end, kind, group_by, metric, page, size):
        WorkService._role(viewer)
        if viewer.role not in ROLES:
            raise ApiError(403, "گزارش فعالیت‌ها در حوزهٔ دسترسی شما نیست.")
        start = WorkService.date(start, "ابتدای بازه")
        end = WorkService.date(end, "انتهای بازه")
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        if not 0 <= days <= 365:
            raise ApiError(400, "بازهٔ گزارش باید مرتب و حداکثر ۳۶۶ روز باشد.")
        kind = "ALL" if kind in (None, "") else kind
        if not isinstance(kind, str) or kind not in (*TYPE_ORDER, "ALL"):
            raise ApiError(400, "دستهٔ گزارش معتبر نیست.")
        group_by = "hall" if group_by in (None, "") else group_by
        if not isinstance(group_by, str) or group_by not in ("hall", "line"):
            raise ApiError(400, "گروه‌بندی گزارش معتبر نیست.")
        metric = "activityCount" if metric in (None, "") else metric
        if not isinstance(metric, str) or metric not in METRICS:
            raise ApiError(400, "شاخص گزارش معتبر نیست.")
        page, size = WorkService.page_bounds(page, size)
        return dict(viewer=viewer.id, specialty=viewer.specialty, start=start, end=end,
                    kind=kind, group_by=group_by, metric=metric,
                    page=page, size=size, offset=(page - 1) * size)

    @staticmethod
    def cte(connection, viewer):
        if connection.is_sqlite:
            # The same registered parser as the legacy EM report rejects
            # duplicate keys and malformed/non-object legacy JSON safely.
            body = "pak_em_reporting_body(o.body)"
            stopped = "json_type(report_body,'$.productionStopped')='true'"
            minutes = """CASE WHEN json_type(report_body,'$.downtimeMinutes')='integer'
AND json_extract(report_body,'$.downtimeMinutes') BETWEEN 0 AND 1440
THEN CAST(json_extract(report_body,'$.downtimeMinutes') AS INTEGER) END"""
        else:
            body = """CASE WHEN o.body IS JSON OBJECT WITH UNIQUE KEYS
AND pg_input_is_valid(o.body,'jsonb') THEN o.body::jsonb ELSE '{}'::jsonb END"""
            stopped = "report_body->'productionStopped'='true'::jsonb"
            minutes = """CASE WHEN jsonb_typeof(report_body->'downtimeMinutes')='number'
AND report_body->>'downtimeMinutes' ~ '^[0-9]{1,4}$'
THEN CASE WHEN (report_body->>'downtimeMinutes')::integer BETWEEN 0 AND 1440
THEN (report_body->>'downtimeMinutes')::integer END END"""
        return """WITH selected AS (
SELECT o.id,o.type,o.work_date,o.duration_minutes,a.hall,a.line,""" + body + """ AS report_body,
COALESCE((SELECT SUM(CAST(p.minutes AS BIGINT)) FROM participants p WHERE p.order_id=o.id),0) AS person_minutes
FROM orders o JOIN assets a ON a.id=o.asset_id
WHERE o.status='completed' AND o.work_date>=:start AND o.work_date<=:end
AND (:kind='ALL' OR o.type=:kind) AND (""" + WorkService.read_scope(viewer) + """)), facts AS (
SELECT id,type,work_date,duration_minutes,hall,line,person_minutes,
CASE WHEN """ + stopped + """ THEN 1 ELSE 0 END AS stopped,
CASE WHEN """ + stopped + " THEN " + minutes + " END AS downtime FROM selected) "

    @staticmethod
    def aggregate_projection():
        return """COUNT(*) AS activity_count,
COALESCE(SUM(CAST(duration_minutes AS BIGINT)),0) AS work_minutes,
COALESCE(SUM(person_minutes),0) AS person_minutes,
COALESCE(SUM(CAST(downtime AS BIGINT)),0) AS downtime_minutes,
COALESCE(SUM(CASE WHEN stopped=1 AND downtime IS NULL THEN 1 ELSE 0 END),0) AS unknown_count,
COALESCE(SUM(stopped),0) AS stopped_count"""

    @staticmethod
    def totals_view(row):
        return dict(activityCount=int(row["activity_count"]), workMinutes=int(row["work_minutes"]),
                    personMinutes=int(row["person_minutes"]), downtimeMinutes=int(row["downtime_minutes"]),
                    unknownDowntimeCount=int(row["unknown_count"]), stoppedActivityCount=int(row["stopped_count"]))

    def totals(self, connection, sql, args, where=""):
        counts = ",".join("COALESCE(SUM(CASE WHEN type='" + kind + "' THEN 1 ELSE 0 END),0) AS type_" + kind.lower()
                          for kind in TYPE_ORDER)
        row = connection.one(sql + "SELECT " + self.aggregate_projection() + "," + counts + " FROM facts" + where, args)
        return self.totals_view(row), {kind: int(row["type_" + kind.lower()]) for kind in TYPE_ORDER}

    @staticmethod
    def response_context(args):
        return {"from": args["start"], "to": args["end"], "type": args["kind"],
                "groupBy": args["group_by"], "metric": REPORT_VERSION,
                "selectedMetric": args["metric"], "status": "completed",
                "page": args["page"], "pageSize": args["size"]}

    def summary(self, viewer, start, end, kind=None, group_by=None, metric=None, page=None, page_size=None):
        args = self.parameters(viewer, start, end, kind, group_by, metric, page, page_size)
        grouping = "hall,line" if args["group_by"] == "line" else "hall"
        line = "line" if args["group_by"] == "line" else "'' AS line"
        # SQL identifiers below are selected only from closed server-side lists.
        sorting = METRICS[args["metric"]]
        with self.db.transaction(write=False) as c:
            sql = self.cte(c, viewer)
            totals, type_counts = self.totals(c, sql, args)
            count = int(c.scalar(sql + "SELECT COUNT(*) FROM (SELECT " + grouping + " FROM facts GROUP BY " + grouping + ") grouped", args))
            rows = c.query(sql + "SELECT hall," + line + "," + self.aggregate_projection() +
                           " FROM facts GROUP BY " + grouping + " ORDER BY " + sorting + " DESC,hall,line LIMIT :size OFFSET :offset", args)
        return {**self.response_context(args), "totalGroups": count, "totalOrders": totals["activityCount"],
                "totals": totals, "typeCounts": type_counts,
                "items": [dict(hall=row["hall"], line=row["line"],
                               unclassified=not (row["line"] if args["group_by"] == "line" else row["hall"]).strip(),
                               **self.totals_view(row)) for row in rows],
                "total": count, "totalPages": (count + args["size"] - 1) // args["size"]}

    def orders(self, viewer, start, end, hall, line=None, kind=None, group_by=None, metric=None, page=None, page_size=None):
        args = self.parameters(viewer, start, end, kind, group_by, metric, page, page_size)
        if hall is None or args["group_by"] == "line" and line is None:
            raise ApiError(400, "گروه انتخاب‌شده را مشخص کنید.")
        if args["group_by"] == "hall" and line not in (None, ""):
            raise ApiError(400, "برای انتخاب خط، گروه‌بندی خط را انتخاب کنید.")
        args["hall"] = text(hall, maximum=100, trim=False)
        where = " WHERE hall=:hall"
        if args["group_by"] == "line":
            args["line"] = text(line, maximum=300, trim=False)
            where += " AND line=:line"
        with self.db.transaction(write=False) as c:
            sql = self.cte(c, viewer)
            totals, type_counts = self.totals(c, sql, args, where)
            selected = c.query(sql + "SELECT id,stopped,downtime FROM facts" + where +
                               " ORDER BY work_date DESC,id DESC LIMIT :size OFFSET :offset", args)
            if selected:
                parameters = {"id" + str(i): row["id"] for i, row in enumerate(selected)}
                keys = ",".join(":id" + str(i) for i in range(len(selected)))
                rows = c.query("SELECT o.* FROM orders o WHERE o.id IN (" + keys + ")", parameters)
                # A legacy row can contain a non-object or malformed body.
                # Hydrate its normalized columns and related records anyway.
                by_id = {row["id"]: {**row, "body": safe_reporting_body(row["body"])} for row in rows}
                orders = self.work.view_batch(c, [by_id[row["id"]] for row in selected], viewer)
                measured = {row["id"]: row for row in selected}
                for order in orders:
                    fact = measured[order["id"]]
                    row = by_id[order["id"]]
                    order.update(title=row["title"], description=row["description"],
                                 durationMinutes=row["duration_minutes"])
                    value = fact["downtime"]
                    order["productionStopped"] = bool(fact["stopped"])
                    order["reportedDowntimeMinutes"] = None if value is None else int(value)
            else:
                orders = []
        total = totals["activityCount"]
        return {**self.response_context(args), "hall": args["hall"], "line": args.get("line", ""),
                "totals": totals, "typeCounts": type_counts, "orders": orders,
                "total": total, "totalPages": (total + args["size"] - 1) // args["size"]}
