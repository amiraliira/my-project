"""Read-only, scoped sums of recorded EM downtime, not interval-union OEE."""
from datetime import date

from .common import ApiError, dumps, loads, text
from .work_service import WorkService


METRIC = "reported-em-downtime-v1"
ROLES = {"head", "manager", "planner", "supervisor", "technician"}


def safe_reporting_body(raw):
    """SQLite equivalent of the guarded PostgreSQL JSON-object projection."""
    try:
        value = loads(raw)
        return dumps(value) if isinstance(value, dict) else "{}"
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        return "{}"


class EmDowntimeReport:
    def __init__(self, database, work):
        self.db, self.work = database, work

    def parameters(self, viewer, start, end, page, size):
        WorkService._role(viewer)
        if viewer.role not in ROLES:
            raise ApiError(403, "گزارش توقفات در حوزهٔ دسترسی شما نیست.")
        start = WorkService.date(start, "ابتدای بازه")
        end = WorkService.date(end, "انتهای بازه")
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        if not 0 <= days <= 365:
            raise ApiError(400, "بازهٔ گزارش باید مرتب و حداکثر ۳۶۶ روز باشد.")
        page, size = WorkService.page_bounds(page, size)
        return dict(viewer=viewer.id, specialty=viewer.specialty, start=start, end=end,
                    page=page, size=size, offset=(page - 1) * size)

    def cte(self, connection, viewer):
        if connection.is_sqlite:
            # Common.loads rejects duplicate keys, nonfinite and oversized
            # numbers just like the application boundary. No schema is changed.
            body = "pak_em_reporting_body(o.body)"
            stopped = "json_type(report_body,'$.productionStopped')='true'"
            minutes = """CASE WHEN json_type(report_body,'$.downtimeMinutes')='integer'
AND json_extract(report_body,'$.downtimeMinutes') BETWEEN 0 AND 1440
THEN CAST(json_extract(report_body,'$.downtimeMinutes') AS INTEGER) END"""
        else:
            # Guards stay inside CASE: malformed legacy TEXT or out-of-range
            # JSON numbers must not reach a jsonb/numeric cast via reordering.
            body = """CASE WHEN o.body IS JSON OBJECT WITH UNIQUE KEYS
AND pg_input_is_valid(o.body,'jsonb') THEN o.body::jsonb ELSE '{}'::jsonb END"""
            stopped = "report_body->'productionStopped'='true'::jsonb"
            minutes = """CASE WHEN jsonb_typeof(report_body->'downtimeMinutes')='number'
AND report_body->>'downtimeMinutes' ~ '^[0-9]{1,4}$'
THEN CASE WHEN (report_body->>'downtimeMinutes')::integer BETWEEN 0 AND 1440
THEN (report_body->>'downtimeMinutes')::integer END END"""
        return """WITH selected AS (
SELECT o.id,o.work_date,o.duration_minutes,a.hall,a.line,""" + body + """ AS report_body,
COALESCE((SELECT SUM(CAST(p.minutes AS BIGINT)) FROM participants p WHERE p.order_id=o.id),0) AS person_minutes
FROM orders o JOIN assets a ON a.id=o.asset_id
WHERE o.type='EM' AND o.status='completed' AND o.work_date>=:start AND o.work_date<=:end
AND (""" + WorkService.read_scope(viewer) + """)), facts AS (
SELECT id,work_date,duration_minutes,hall,line,person_minutes,""" + minutes + """ AS downtime
FROM selected WHERE """ + stopped + ") "

    @staticmethod
    def totals(connection, cte, parameters, where=""):
        row = connection.one(cte + """SELECT COUNT(*) AS activity_count,
COALESCE(SUM(CAST(downtime AS BIGINT)),0) AS downtime_minutes,
COALESCE(SUM(CAST(duration_minutes AS BIGINT)),0) AS repair_minutes,
COALESCE(SUM(person_minutes),0) AS person_minutes,
COALESCE(SUM(CASE WHEN downtime IS NULL THEN 1 ELSE 0 END),0) AS unknown_count
FROM facts""" + where, parameters)
        return dict(activityCount=row["activity_count"], downtimeMinutes=row["downtime_minutes"],
                    repairMinutes=row["repair_minutes"], personMinutes=row["person_minutes"],
                    unknownDowntimeCount=row["unknown_count"])

    def summary(self, viewer, start, end, page=None, page_size=None):
        args = self.parameters(viewer, start, end, page, page_size)
        with self.db.transaction(write=False) as c:
            sql = self.cte(c, viewer)
            totals = self.totals(c, sql, args)
            total = c.scalar(sql + "SELECT COUNT(*) FROM (SELECT hall,line FROM facts GROUP BY hall,line) grouped", args)
            rows = c.query(sql + """SELECT hall,line,COUNT(*) AS activity_count,
COALESCE(SUM(CAST(downtime AS BIGINT)),0) AS downtime_minutes,
COALESCE(SUM(CAST(duration_minutes AS BIGINT)),0) AS repair_minutes,
COALESCE(SUM(person_minutes),0) AS person_minutes,
SUM(CASE WHEN downtime IS NULL THEN 1 ELSE 0 END) AS unknown_count
FROM facts GROUP BY hall,line ORDER BY downtime_minutes DESC,hall,line
LIMIT :size OFFSET :offset""", args)
        return {"from": start, "to": end, "metric": METRIC, "status": "completed",
                "totalLines": total, "totalOrders": totals["activityCount"], "totals": totals,
                "items": [dict(hall=row["hall"], line=row["line"], unclassified=not row["line"].strip(),
                               downtimeMinutes=row["downtime_minutes"], repairMinutes=row["repair_minutes"],
                               personMinutes=row["person_minutes"], activityCount=row["activity_count"],
                               unknownDowntimeCount=row["unknown_count"]) for row in rows],
                "page": args["page"], "pageSize": args["size"], "total": total,
                "totalPages": (total + args["size"] - 1) // args["size"]}

    def orders(self, viewer, start, end, hall, line, page=None, page_size=None):
        args = self.parameters(viewer, start, end, page, page_size)
        # Empty is a real unclassified key, None means the key was omitted.
        if hall is None or line is None:
            raise ApiError(400, "سالن و خطِ انتخاب‌شده را مشخص کنید.")
        args.update(hall=text(hall, maximum=100, trim=False), line=text(line, maximum=300, trim=False))
        where = " WHERE hall=:hall AND line=:line"
        with self.db.transaction(write=False) as c:
            sql = self.cte(c, viewer)
            totals = self.totals(c, sql, args, where)
            total = totals["activityCount"]
            selected = c.query(sql + "SELECT id,downtime FROM facts" + where +
                               " ORDER BY work_date DESC,id DESC LIMIT :size OFFSET :offset", args)
            if selected:
                parameters = dict(args)
                parameters.update({"id" + str(i): row["id"] for i, row in enumerate(selected)})
                keys = ",".join(":id" + str(i) for i in range(len(selected)))
                rows = c.query("SELECT o.* FROM orders o WHERE o.id IN (" + keys + ")", parameters)
                by_id = {row["id"]: row for row in rows}
                orders = self.work.view_batch(c, [by_id[row["id"]] for row in selected], viewer)
                measured = {row["id"]: row["downtime"] for row in selected}
                for order in orders:
                    order["reportedDowntimeMinutes"] = measured[order["id"]]
            else:
                orders = []
        return {"from": start, "to": end, "hall": hall, "line": line, "metric": METRIC,
                "status": "completed", "orders": orders, "totals": totals, "total": total,
                "page": args["page"], "pageSize": args["size"],
                "totalPages": (total + args["size"] - 1) // args["size"]}
