"""Approved maintenance consumption, never a warehouse stock balance."""
from collections import defaultdict
from uuid import uuid4
from .common import ApiError
from .quantity import INT64_MAX, value

SOURCE = """ FROM orders o JOIN order_parts line ON line.order_id=o.id JOIN parts p ON p.id=line.part_id
JOIN approvals a ON a.id=(SELECT final_a.id FROM approvals final_a WHERE final_a.order_id=o.id AND final_a.revision=o.workflow_revision AND final_a.stage='head' AND final_a.decision='approve' ORDER BY final_a.created_at DESC,final_a.id DESC LIMIT 1)
JOIN users actor ON actor.id=a.actor_id"""
COLUMNS = """SELECT o.id AS order_id,line.part_id,a.id AS approval_id,o.workflow_revision AS revision,line.quantity_units,
p.code AS part_code,p.name AS part_name,p.unit,o.number AS order_number,o.work_date AS executed_at,
a.created_at AS approved_at,a.actor_id,actor.name AS actor_name"""


def public(entry):
    return {"id": entry["id"], "partId": entry["part_id"], "partCode": entry["part_code"],
            "partName": entry["part_name"], "unit": entry["unit"], "quantity": value(entry["quantity_units"]),
            "orderId": entry["order_id"], "orderNumber": entry["order_number"], "executedAt": entry["executed_at"],
            "approvedAt": entry["approved_at"], "actorName": entry["actor_name"]}


def capture(connection, order_id):
    if connection.scalar("""SELECT COUNT(*) FROM orders o WHERE o.id=:id AND o.status='completed'
AND EXISTS(SELECT 1 FROM approvals a WHERE a.order_id=o.id AND a.revision=o.workflow_revision AND a.stage='head' AND a.decision='approve')""", {"id": order_id}) != 1:
        raise ApiError(409, "مصرف قطعه فقط پس از تأیید نهایی گزارش قابل ثبت است.")
    rows = connection.query(COLUMNS + SOURCE + " WHERE o.id=:id AND o.status='completed' ORDER BY line.part_id", {"id": order_id})
    return insert(connection, rows)


def insert(connection, rows):
    inserted = 0
    ordered = sorted(rows, key=lambda entry: (entry["part_id"], entry["order_id"]))
    fields = ("id", "order_id", "part_id", "approval_id", "revision", "quantity_units", "part_code", "part_name", "unit", "order_number", "executed_at", "approved_at", "actor_id", "actor_name")
    # Bound each INSERT to 100 rows and acquire aggregate keys in stable order.
    # A report with hundreds of parts needs a few round trips, not hundreds.
    for start in range(0, len(ordered), 100):
        args, placeholders = {}, []
        for index, row in enumerate(ordered[start:start + 100]):
            quantity_units = row["quantity_units"]
            if type(quantity_units) is not int or not 0 < quantity_units <= INT64_MAX:
                raise ApiError(409, "مقدار قطعه در گزارش معتبر نیست؛ مدیر سامانه باید داده را بررسی کند.")
            entry = dict(row, id=str(uuid4()))
            args.update({f"v{index}_{field}": entry[field] for field in fields})
            placeholders.append("(" + ",".join(f":v{index}_{field}" for field in fields) + ")")
        fresh = connection.query("INSERT INTO part_consumptions(" + ",".join(fields) + ") VALUES " + ",".join(placeholders) + " ON CONFLICT(order_id,part_id) DO NOTHING RETURNING part_id,quantity_units", args)
        if not fresh:
            continue
        totals = defaultdict(lambda: [0, 0])
        for row in fresh:
            totals[row["part_id"]][0] += row["quantity_units"]
            totals[row["part_id"]][1] += 1
        args, placeholders = {"maximum": INT64_MAX}, []
        for index, (part_id, (quantity_units, count)) in enumerate(sorted(totals.items())):
            if quantity_units > INT64_MAX:
                raise ApiError(409, "جمع مصرف این قطعه از ظرفیت ثبت عبور می‌کند.")
            args.update({f"part{index}": part_id, f"units{index}": quantity_units, f"count{index}": count})
            placeholders.append(f"(:part{index},:units{index},:count{index})")
        changed = connection.query("INSERT INTO part_consumption_totals(part_id,quantity_units,report_count) VALUES " + ",".join(placeholders) + """
ON CONFLICT(part_id) DO UPDATE SET quantity_units=part_consumption_totals.quantity_units+excluded.quantity_units,report_count=part_consumption_totals.report_count+excluded.report_count
WHERE part_consumption_totals.quantity_units<=:maximum-excluded.quantity_units AND part_consumption_totals.report_count<=:maximum-excluded.report_count RETURNING part_id""", args)
        if len(changed) != len(totals):
            raise ApiError(409, "جمع مصرف این قطعه از ظرفیت ثبت عبور می‌کند؛ مدیر سامانه باید داده را بررسی کند.")
        inserted += len(fresh)
    return inserted


class ConsumptionService:
    capture = staticmethod(capture)

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _filter(viewer, q=None, part_id=None, from_date=None, to_date=None):
        from .work_service import WorkService
        if viewer.role not in {"supervisor", "planner", "head", "manager", "admin"}:
            raise ApiError(403, "گزارش مصرف قطعات در سطح دسترسی شما نیست.")
        q, part_id = (q or "").strip(), (part_id or "").strip()
        if len(q) > 300 or len(part_id) > 100:
            raise ApiError(400, "فیلتر گزارش مصرف معتبر نیست.")
        start = WorkService.date(from_date, "ابتدای بازه") if from_date else None
        end = WorkService.date(to_date, "انتهای بازه") if to_date else None
        if start and end and start > end:
            raise ApiError(400, "ابتدای بازه نباید پس از انتهای بازه باشد.")
        args = {"part": part_id, "start": start, "end": end, "search": "%" + q.lower().replace("~", "~~").replace("%", "~%").replace("_", "~_") + "%"}
        filters = ["1=1"]
        if part_id:
            filters.append("m.part_id=:part")
        if start:
            filters.append("m.executed_at>=:start")
        if end:
            filters.append("m.executed_at<=:end")
        if q:
            filters.append("(LOWER(m.part_code) LIKE :search ESCAPE '~' OR LOWER(m.part_name) LIKE :search ESCAPE '~' OR LOWER(m.order_number) LIKE :search ESCAPE '~')")
        return " WHERE " + " AND ".join(filters), args

    def history(self, viewer, page=1, page_size=25, q=None, part_id=None, from_date=None, to_date=None):
        from .work_service import WorkService
        page, page_size = WorkService.page_bounds(page, page_size)
        filters, args = self._filter(viewer, q, part_id, from_date, to_date)
        args.update(size=page_size, offset=(page - 1) * page_size)
        with self.db.transaction(write=False) as c:
            total = c.scalar("SELECT COUNT(*) FROM part_consumptions m" + filters, args)
            items = [public(row) for row in c.query("SELECT m.* FROM part_consumptions m" + filters + " ORDER BY m.executed_at DESC,m.id DESC LIMIT :size OFFSET :offset", args)]
        return {"items": items, "total": total, "page": page, "pageSize": page_size,
                "totalPages": (total + page_size - 1) // page_size, "hasMore": args["offset"] + len(items) < total}

    def export(self, viewer, q=None, part_id=None, from_date=None, to_date=None):
        filters, args = self._filter(viewer, q, part_id, from_date, to_date)
        def rows():
            with self.db.transaction(write=False) as c:
                previous_date = previous_id = None
                while True:
                    cursor = "" if previous_date is None else " AND (m.executed_at<:cursor_date OR (m.executed_at=:cursor_date AND m.id<:cursor_id))"
                    parameters = dict(args, cursor_date=previous_date, cursor_id=previous_id)
                    batch = c.query("SELECT m.* FROM part_consumptions m" + filters + cursor + " ORDER BY m.executed_at DESC,m.id DESC LIMIT 100", parameters)
                    if not batch:
                        return
                    yield from (public(entry) for entry in batch)
                    previous_date, previous_id = batch[-1]["executed_at"], batch[-1]["id"]
        return rows()
