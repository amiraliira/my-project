"""Role-scoped personnel performance from immutable final scoring facts."""
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from .common import ApiError, text, today
from .permissions import can_view_personnel_reports
from .quantity import value as quantity_value
from .reporting_service import FACT_JOINS, score_aggregate, parse_score
from .score_facts import round_score, stored_decimal

MONTH_NAMES = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
BASIC_JOINS = "FROM orders o JOIN participants p ON p.order_id=o.id JOIN users u ON u.id=p.person_id"
HALL_JOINS = "FROM participants p JOIN orders o ON o.id=p.order_id JOIN assets a ON a.id=o.asset_id"
HALL_TOTALS = "COUNT(*) AS activity_count,COALESCE(SUM(CAST(p.minutes AS BIGINT)),0) AS minutes"


def hours(minutes):
    return (Decimal(minutes) / 60).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def available_years(connection, work_scope, args):
    """Two bounded projections, rather than a database round trip per year."""
    from .pm_calendar import from_persian, to_persian
    current = to_persian(date.fromisoformat(today()))[0]
    gregorian = connection.query("SELECT DISTINCT SUBSTR(o.work_date,1,4) AS year " + BASIC_JOINS +
        " WHERE o.status='completed' AND (" + work_scope + ")", args)
    candidates = sorted({int(row['year']) - offset for row in gregorian for offset in (621, 622)} - {current})
    values, bindings = [], dict(args)
    for candidate in candidates:
        if not 1300 <= candidate < 1634:
            continue
        index = len(values)
        values.append(f'(:year_{index},:lower_{index},:upper_{index})')
        bindings.update({f'year_{index}': candidate,
            f'lower_{index}': from_persian(candidate, 1, 1).isoformat(),
            f'upper_{index}': from_persian(candidate + 1, 1, 1).isoformat()})
    years = {current}
    if values:
        rows = connection.query('WITH candidate_years(year,lower_date,upper_date) AS (VALUES ' +
            ','.join(values) + ') SELECT year FROM candidate_years y WHERE (SELECT 1 ' +
            BASIC_JOINS + " WHERE o.status='completed' AND (" + work_scope +
            ') AND o.work_date>=y.lower_date AND o.work_date<y.upper_date LIMIT 1) IS NOT NULL', bindings)
        years.update(row['year'] for row in rows)
    return sorted(years, reverse=True)


def period(kind=None, year=None, month=None):
    from .pm_calendar import from_persian, to_persian
    kind = kind or "monthly"
    if kind == "all":
        return {"kind": kind, "year": None, "month": None, "startDate": None, "endDate": None, "label": "کل دوره"}
    if kind != "monthly":
        raise ApiError(400, "بازه عملکرد باید ماهانه یا کل دوره باشد.")
    current_year, current_month, _ = to_persian(date.fromisoformat(today()))
    y, m = current_year if year is None else year, current_month if month is None else month
    if type(y) is not int or type(m) is not int or not 1300 <= y <= 1500 or not 1 <= m <= 12:
        raise ApiError(400, "سال و ماه شمسی معتبر انتخاب کنید.")
    start = from_persian(y, m, 1)
    end = from_persian(y + 1, 1, 1) if m == 12 else from_persian(y, m + 1, 1)
    return {"kind": kind, "year": y, "month": m, "startDate": start.isoformat(), "endDate": end.isoformat(), "label": f"{MONTH_NAMES[m - 1]} {y}"}


class PerformanceService:
    def __init__(self, db):
        self.db = db

    def hall_report(self, viewer, *, kind=None, year=None, month=None, person_id=None,
                    page=None, page_size=None, hall=None, activities=False):
        """Page one person's final work, grouped by the equipment's recorded hall."""
        from .work_service import WorkService
        WorkService._role(viewer)
        person = viewer.id if person_id is None else text(person_id, maximum=100, field="شناسه فرد")
        if not person:
            raise ApiError(400, "فرد موردنظر را انتخاب کنید.")
        if person != viewer.id and not can_view_personnel_reports(viewer):
            raise ApiError(403, "گزارش عملکرد سایر نفرات فقط در دسترس رئیس است.")
        selected = period(kind, year, month)
        page, size = WorkService.page_bounds(page, 12 if page_size is None else page_size, maximum=25)
        args = {"person": person, "size": size, "offset": (page - 1) * size}
        where = " WHERE p.person_id=:person AND o.status='completed'"
        if selected["kind"] == "monthly":
            where += " AND o.work_date>=:start AND o.work_date<:end"
            args.update(start=selected["startDate"], end=selected["endDate"])
        if activities:
            args["hall"] = text(hall, maximum=100, field="سالن", trim=False)
            where += " AND a.hall=:hall"
        with self.db.transaction(write=False) as c:
            from .security import assert_live_actor
            assert_live_actor(c, viewer, lock=False)
            person_row = c.one("SELECT name,specialty FROM users WHERE id=:person", args)
            if person_row is None:
                raise ApiError(404, "فرد موردنظر یافت نشد.")
            totals = c.one("SELECT " + HALL_TOTALS + " " + HALL_JOINS + where, args)
            response = {"personId": person, "personName": person_row["name"], "period": selected,
                        "activityCount": int(totals["activity_count"]), "minutes": int(totals["minutes"]),
                        "page": page, "pageSize": size}
            if activities:
                rows = c.query("SELECT o.id,o.work_date,o.type,o.title,o.description,a.name AS asset_name,"
                               "a.line,p.minutes " + HALL_JOINS + where +
                               " ORDER BY o.work_date DESC,o.id DESC LIMIT :size OFFSET :offset", args)
                items = self._hall_activity_details(c, rows)
                return {**response, "hall": args["hall"], "items": items, "total": response["activityCount"]}
            total = c.scalar("SELECT COUNT(DISTINCT a.hall) " + HALL_JOINS + where, args)
            rows = c.query("SELECT a.hall," + HALL_TOTALS + " " + HALL_JOINS + where +
                           " GROUP BY a.hall ORDER BY minutes DESC,a.hall LIMIT :size OFFSET :offset", args)
        return {**response, "total": int(total), "items": [
            {"hall": row["hall"], "activityCount": int(row["activity_count"]), "minutes": int(row["minutes"])}
            for row in rows
        ]}

    @staticmethod
    def _hall_activity_details(connection, rows):
        # Fetch just the displayed affiliations and consumed parts as two
        # bounded reads. Personnel scoring tables aren't part of this view.
        if not rows:
            return []
        from .work_service import _in_list
        keys, args = _in_list([row["id"] for row in rows])
        peers, parts = {}, {}
        for row in connection.query("SELECT p.order_id,p.person_id,u.name,p.minutes "
                                    "FROM participants p JOIN users u ON u.id=p.person_id "
                                    "WHERE p.order_id IN " + keys + " ORDER BY u.name,p.person_id", args):
            peers.setdefault(row["order_id"], []).append({
                "personId": row["person_id"], "personName": row["name"], "minutes": row["minutes"],
            })
        for row in connection.query("SELECT op.order_id,p.id,p.code,p.name,p.unit,op.quantity_units "
                                    "FROM order_parts op JOIN parts p ON p.id=op.part_id "
                                    "WHERE op.order_id IN " + keys + " ORDER BY p.code,p.id", args):
            parts.setdefault(row["order_id"], []).append({
                "partId": row["id"], "code": row["code"], "name": row["name"], "unit": row["unit"],
                "quantity": quantity_value(row["quantity_units"]),
            })
        return [{"orderId": row["id"], "workDate": row["work_date"], "type": row["type"],
                 "title": row["title"], "description": row["description"], "assetName": row["asset_name"],
                 "line": row["line"], "minutes": row["minutes"],
                 "participants": peers.get(row["id"], []), "parts": parts.get(row["id"], [])}
                for row in rows]

    def report(self, viewer, period=None, year=None, month=None, person_id=None, detail_page=None, detail_page_size=None, scope=None):
        from .work_service import WorkService
        from .pm_calendar import format_persian
        WorkService._role(viewer)
        if scope not in (None, "own", "all"):
            raise ApiError(400, "حوزهٔ عملکرد باید شخصی یا همهٔ نفرات باشد.")
        scope = scope or ("all" if can_view_personnel_reports(viewer) else "own")
        if scope == "all" and not can_view_personnel_reports(viewer):
            raise ApiError(403, "گزارش عملکرد سایر نفرات فقط در دسترس رئیس است.")
        own = scope == "own"
        selected = globals()["period"](period, year, month)
        page, size = WorkService.page_bounds(detail_page, 20 if detail_page_size is None else detail_page_size)
        person_id = (person_id or "").strip() or None
        if person_id and len(person_id) > 100:
            raise ApiError(400, "شناسه فرد نامعتبر است.")
        if own and person_id not in (None, viewer.id):
            raise ApiError(403, "فقط عملکرد شخصی خودتان در دسترس است.")
        args = {"viewer": viewer.id, "specialty": viewer.specialty, "size": size, "offset": (page - 1) * size,
                "start": selected["startDate"], "end": selected["endDate"], "person": person_id}
        # Scope every projection independently. A shared order must not expose
        # another participant's score, even when a forged personId is supplied.
        # Historical participation belongs to the person, regardless of today's
        # specialty or role; those current fields cannot widen personal access.
        staff_scope = "u.id=:viewer" if own else "u.specialty<>''"
        work_scope = "p.person_id=:viewer" if own else "1=1"
        bounds = " AND o.work_date>=:start AND o.work_date<:end" if selected["kind"] == "monthly" else ""
        where = " WHERE o.status='completed' AND (" + work_scope + ")" + bounds
        with self.db.transaction(write=False) as c:
            if person_id and not c.scalar("SELECT COUNT(*) FROM users u WHERE u.id=:person AND (" + staff_scope + ")", args):
                raise ApiError(403, "عملکرد این فرد در حوزه دسترسی شما نیست.")
            people = {entry["person_id"]: dict(entry, minutes=0, activity_count=0, pm=0, em=0, project=0, cm=0, general=0, unscored_count=0, raw_score_text="0") for entry in c.query("SELECT u.id AS person_id,u.name,u.code,u.specialty,u.active FROM users u WHERE u.active=1 AND (" + staff_scope + ")", args)}
            aggregates = """SELECT u.id AS person_id,u.name,u.code,u.specialty,u.active,
COALESCE(SUM(CAST(p.minutes AS BIGINT)),0) AS minutes,COUNT(*) AS activity_count,
COALESCE(SUM(CASE WHEN o.type='PM' THEN 1 ELSE 0 END),0) AS pm,
COALESCE(SUM(CASE WHEN o.type='EM' THEN 1 ELSE 0 END),0) AS em,
COALESCE(SUM(CASE WHEN o.type='PROJECT' THEN 1 ELSE 0 END),0) AS project,
COALESCE(SUM(CASE WHEN o.type='CM' THEN 1 ELSE 0 END),0) AS cm,
COALESCE(SUM(CASE WHEN o.type='GENERAL' THEN 1 ELSE 0 END),0) AS general,
COALESCE(SUM(CASE WHEN (f.supervisor_coefficient IS NULL AND COALESCE(f.source,'')<>'head-only') OR f.head_coefficient IS NULL THEN 1 ELSE 0 END),0) AS unscored_count,""" + score_aggregate(c.is_sqlite) + " AS raw_score_text " + FACT_JOINS + where + " GROUP BY u.id,u.name,u.code,u.specialty,u.active"
            for entry in c.query(aggregates, args):
                people[entry["person_id"]] = entry
            ranked = sorted(people.values(), key=lambda p: (-round_score(parse_score(p["raw_score_text"])), -p["minutes"], p["name"]))
            detail_filter = " AND p.person_id=:person" if person_id else ""
            detail_total = c.scalar("SELECT COUNT(*) " + BASIC_JOINS + where + detail_filter, args)
            # Select the authorized participant page before joining potentially
            # years of scoring facts. The outer joins hydrate only that page,
            # with the same stable order and one repeatable-read snapshot.
            detail_keys = "SELECT o.id AS selected_order,p.person_id AS selected_person " + BASIC_JOINS + where + detail_filter + " ORDER BY o.work_date DESC,o.number DESC,p.person_id LIMIT :size OFFSET :offset"
            detail_rows = c.query("SELECT o.id AS order_id,o.number,o.work_date,o.type,o.title,u.id AS person_id,u.name AS person_name,p.specialty,p.minutes,f.supervisor_coefficient,COALESCE(f.head_coefficient_exact,CAST(f.head_coefficient AS TEXT)) AS head_coefficient,f.score_raw,f.source FROM (" + detail_keys + ") selected JOIN orders o ON o.id=selected.selected_order JOIN participants p ON p.order_id=o.id AND p.person_id=selected.selected_person JOIN users u ON u.id=p.person_id LEFT JOIN completed_scores f ON f.order_id=o.id AND f.revision=o.workflow_revision AND f.person_id=p.person_id ORDER BY o.work_date DESC,o.number DESC,p.person_id", args)
            details = [{"orderId": row["order_id"], "number": row["number"], "workDate": row["work_date"], "jalaliDate": format_persian(date.fromisoformat(row["work_date"])), "type": row["type"], "title": row["title"], "personId": row["person_id"], "personName": row["person_name"], "specialty": row["specialty"], "minutes": row["minutes"], "hours": hours(row["minutes"]), "supervisorCoefficient": stored_decimal(row["supervisor_coefficient"]) if row["supervisor_coefficient"] is not None else None, "headCoefficient": stored_decimal(row["head_coefficient"]) if row["head_coefficient"] is not None else None, "score": round_score(stored_decimal(row["score_raw"])) if row["score_raw"] is not None else None, "source": row["source"]} for row in detail_rows]
            years = available_years(c, work_scope, args)
            activity_count = c.scalar("SELECT COUNT(DISTINCT o.id) " + BASIC_JOINS + where, args)
        result_people = [{"personId": p["person_id"], "name": p["name"], "code": p["code"], "specialty": p["specialty"], "active": bool(p["active"]), "minutes": p["minutes"], "hours": hours(p["minutes"]), "activityCount": p["activity_count"], "score": round_score(parse_score(p["raw_score_text"])), "pm": p["pm"], "em": p["em"], "project": p["project"], "cm": p["cm"], "general": p["general"], "unscoredCount": p["unscored_count"]} for p in ranked]
        minutes = sum(p["minutes"] for p in result_people)
        return {"period": selected, "scope": scope,
                "formula": "برای تکنسین: نمره سرپرست × نمره رئیس × ساعت فرد؛ برای کار سرپرست: نمره رئیس (۰ تا ۹) × ساعت فرد", "years": years, "people": result_people,
                "totals": {"peopleCount": len(result_people), "minutes": minutes, "hours": hours(minutes), "activityCount": activity_count,
                           "score": sum((p["score"] for p in result_people), Decimal(0)), "unscoredCount": sum(p["unscoredCount"] for p in result_people)},
                "details": details, "detailPage": page, "detailPageSize": size, "detailTotal": detail_total}
