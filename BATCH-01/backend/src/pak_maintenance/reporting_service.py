"""Bounded SQL projections and exact decimal aggregation for both providers."""
from decimal import Decimal
from .common import ApiError, today
from .score_facts import participant_score, round_score, decimal_add

FACT_JOINS = """FROM orders o JOIN participants p ON p.order_id=o.id JOIN users u ON u.id=p.person_id
LEFT JOIN completed_scores f ON f.order_id=o.id AND f.revision=o.workflow_revision AND f.person_id=p.person_id"""

POSTGRESQL_FUNCTIONS = """
CREATE OR REPLACE FUNCTION pak_round_even(p_value numeric,p_digits integer)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE factor numeric; scaled numeric; whole numeric; fraction numeric;
BEGIN
 IF p_digits<0 OR p_digits>28 THEN RAISE EXCEPTION 'Unsupported decimal scale'; END IF;
 factor:=power(10::numeric,p_digits); scaled:=abs(p_value)*factor;
 whole:=trunc(scaled); fraction:=scaled-whole;
 IF fraction>0.5 OR (fraction=0.5 AND mod(whole,2)=1) THEN whole:=whole+1; END IF;
 RETURN sign(p_value)*whole::numeric(100,40)/factor;
END; $$;
CREATE OR REPLACE FUNCTION pak_decimal(p_value numeric)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE places integer; rounded numeric;
BEGIN
 FOR places IN REVERSE 28..0 LOOP
  rounded:=pak_round_even(p_value,places);
  IF abs(rounded)*power(10::numeric,places)<=79228162514264337593543950335 THEN RETURN rounded; END IF;
 END LOOP;
 RAISE EXCEPTION 'Decimal score overflow';
END; $$;
CREATE OR REPLACE FUNCTION pak_participant_score(p_minutes integer,p_supervisor numeric,p_head numeric)
RETURNS numeric LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
 SELECT pak_decimal(pak_decimal(pak_decimal(p_minutes::numeric(80,50)/60)*p_supervisor)*p_head);
$$;
CREATE OR REPLACE FUNCTION pak_decimal_add(p_total numeric,p_value numeric)
RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
 SELECT pak_decimal(COALESCE(p_total,0)+COALESCE(p_value,0));
$$;
DROP AGGREGATE IF EXISTS pak_decimal_sum(numeric);
CREATE AGGREGATE pak_decimal_sum(numeric)(SFUNC=pak_decimal_add,STYPE=numeric,INITCOND='0',PARALLEL=SAFE);
"""


def parse_score(raw):
    return Decimal(str(raw)) if raw not in (None, "") else Decimal(0)


def cents_score(cents):
    """Convert integer evidence without ambient Decimal context rounding."""
    value = Decimal(cents or 0)
    sign, digits, exponent = value.as_tuple()
    return Decimal((sign, digits, exponent - 2))


def rounded_cents(cents, legacy):
    raw = decimal_add(cents_score(cents), parse_score(legacy))
    rounded = round_score(raw)
    sign, digits, exponent = rounded.as_tuple()
    # round_score always produces exactly two decimal places.
    return int(Decimal((sign, digits, exponent + 2)))


def register_functions(connection):
    from .em_downtime import safe_reporting_body
    connection.create_function("pak_em_reporting_body", 1, safe_reporting_body, deterministic=True)
    class CentSum:
        def __init__(self):
            self.total = 0
        def step(self, raw):
            if raw is not None:
                self.total += int(raw)
        def finalize(self):
            # SQLite UDF return integers have a signed-int64 limit; text keeps
            # the final aggregate exact before one System.Decimal range check.
            return str(self.total)
    class DecimalSum:
        def __init__(self):
            self.total = Decimal(0)
        def step(self, raw):
            self.total = decimal_add(self.total, parse_score(raw))
        def finalize(self):
            return str(self.total)
    class ScoreSum:
        def __init__(self):
            self.total = Decimal(0)
        def step(self, minutes, supervisor, head, individual):
            raw = participant_score(minutes, supervisor, head)
            if raw is not None:
                self.total = decimal_add(self.total, round_score(raw) if individual else raw)
        def finalize(self):
            return str(self.total)
    connection.create_aggregate("pak_decimal_sum", 1, DecimalSum)
    connection.create_aggregate("pak_cent_sum", 1, CentSum)
    connection.create_aggregate("pak_score_sum", 4, ScoreSum)
    connection.create_function("pak_round_score", 1, lambda raw: str(round_score(parse_score(raw))) if raw is not None else None, deterministic=True)
    connection.create_function("pak_participant_score", 3, lambda minutes, supervisor, head: str(participant_score(minutes, supervisor, head)) if supervisor is not None and head is not None else None, deterministic=True)
    connection.create_function("pak_fact_total", 2, lambda cents, legacy: str(decimal_add(cents_score(cents), parse_score(legacy))), deterministic=True)
    connection.create_function("pak_rounded_cents", 2, rounded_cents, deterministic=True)


def score_aggregate(sqlite):
    return "pak_fact_total(COALESCE(SUM(f.score_cents),0),pak_decimal_sum(f.score_raw) FILTER(WHERE f.source='legacy-specialty'))" if sqlite else "(COALESCE(SUM(f.score_cents),0)::numeric/100+COALESCE(pak_decimal_sum(f.score_raw::numeric) FILTER(WHERE f.source='legacy-specialty'),0))::text"


class ReportingService:
    register_functions = staticmethod(register_functions)
    score_aggregate = staticmethod(score_aggregate)
    parse_score = staticmethod(parse_score)
    POSTGRESQL_FUNCTIONS = POSTGRESQL_FUNCTIONS
    FACT_JOINS = FACT_JOINS

    def __init__(self, db, work_service=None):
        from .work_service import WorkService
        self.db = db
        self.work = work_service or WorkService(db)

    def em_downtime(self, viewer, start, end, page=None, page_size=None):
        from .em_downtime import EmDowntimeReport
        return EmDowntimeReport(self.db, self.work).summary(viewer, start, end, page, page_size)

    def em_downtime_orders(self, viewer, start, end, hall, line, page=None, page_size=None):
        from .em_downtime import EmDowntimeReport
        return EmDowntimeReport(self.db, self.work).orders(viewer, start, end, hall, line, page, page_size)

    def dashboard(self, viewer):
        from .work_service import WorkService
        WorkService._role(viewer)
        args = {"viewer": viewer.id, "specialty": viewer.specialty, "today": today()}
        scope = WorkService.read_scope(viewer)
        with self.db.transaction(write=False) as c:
            # A bounded set of type/status groups supplies the overview and
            # both charts together, rather than scanning the visible history
            # independently for each. Keep the same authorization predicate.
            groups = c.query("""SELECT o.type,o.status,COUNT(*) AS total,
COALESCE(SUM(CASE WHEN o.status NOT IN ('completed','cancelled') AND o.due_date IS NOT NULL AND o.due_date<:today THEN 1 ELSE 0 END),0) AS overdue,
COALESCE(SUM(CASE WHEN o.status='completed' THEN CAST(o.duration_minutes AS BIGINT) ELSE 0 END),0) AS repair_minutes
FROM orders o WHERE """ + scope + " GROUP BY o.type,o.status", args)
            counts = dict(total=0, open=0, completed=0, overdue=0, repair_minutes=0)
            types, statuses = {}, {}
            for group in groups:
                count = group["total"]
                counts["total"] += count
                counts["open"] += count if group["status"] not in {"completed", "cancelled"} else 0
                counts["completed"] += count if group["status"] == "completed" else 0
                counts["overdue"] += group["overdue"]
                counts["repair_minutes"] += group["repair_minutes"]
                types[group["type"]] = types.get(group["type"], 0) + count
                statuses[group["status"]] = statuses.get(group["status"], 0) + count
            by_type = [dict(name=name, count=types[name]) for name in sorted(types)]
            by_status = [dict(name=name, count=statuses[name]) for name in sorted(statuses)]
            person_minutes = c.scalar("SELECT COALESCE(SUM(CAST(p.minutes AS BIGINT)),0) FROM orders o JOIN participants p ON p.order_id=o.id WHERE o.status='completed' AND " + scope, args)
            grade_scope = "p.person_id=:viewer" if viewer.role == "technician" else "p.specialty=:specialty" if viewer.role == "supervisor" else "1=1"
            fact_scope = " WHERE o.status='completed' AND (" + scope + ") AND (" + grade_scope + ")"
            fact_filter = fact_scope + " GROUP BY o.id"
            if c.is_sqlite:
                # Modern evidence is already rounded per person. Aggregate its
                # integer cents directly, and invoke Decimal only for orders
                # containing legacy specialty evidence. This preserves mixed
                # half-even ties while avoiding a Decimal callback per order.
                order_scores = "SELECT o.id,COALESCE(SUM(f.score_cents),0) AS cents,pak_decimal_sum(f.score_raw) FILTER(WHERE f.source='legacy-specialty') AS legacy_raw,MAX(CASE WHEN f.source='legacy-specialty' THEN 1 ELSE 0 END) AS has_legacy " + FACT_JOINS + fact_filter
                total_sql = "SELECT pak_fact_total(pak_cent_sum(CASE WHEN has_legacy=1 THEN pak_rounded_cents(cents,legacy_raw) ELSE cents END),NULL) FROM (" + order_scores + ") scored"
            elif viewer.role == 'head' and not c.scalar("SELECT EXISTS(SELECT 1 " + FACT_JOINS + fact_scope + " AND f.source='legacy-specialty')", args):
                # Current individual facts are already rounded to exact cents.
                # Sum them directly instead of grouping every historical order.
                # The existence check and sum share this repeatable snapshot;
                # mixed legacy evidence keeps its original per-order rounding.
                total_sql = "SELECT (COALESCE(SUM(f.score_cents),0)::numeric/100)::text " + FACT_JOINS + fact_scope
            else:
                order_scores = "SELECT o.id," + score_aggregate(False) + " AS raw_score,MAX(CASE WHEN f.source='legacy-specialty' THEN 1 ELSE 0 END) AS has_legacy " + FACT_JOINS + fact_filter
                total_sql = "SELECT COALESCE(SUM(CASE WHEN has_legacy=0 THEN raw_score::numeric ELSE pak_round_even(raw_score::numeric,2) END),0)::text FROM (" + order_scores + ") scored"
            score = parse_score(c.scalar(total_sql, args)) if viewer.role == "head" else None
            base = "SELECT a.id,a.code,a.name,COALESCE(SUM(CAST(o.duration_minutes AS BIGINT)),0) AS minutes,COUNT(*) AS count FROM orders o JOIN assets a ON a.id=o.asset_id WHERE o.type='EM' AND (" + scope + ")"
            by_asset = c.query(base + " AND o.status='completed' GROUP BY a.id,a.code,a.name ORDER BY minutes DESC,a.code,a.id LIMIT 10", args)
            repair = c.query(base + " AND o.status<>'cancelled' GROUP BY a.id,a.code,a.name ORDER BY minutes DESC,a.code,a.id LIMIT 12", args)
            recent = self.work.view_batch(c, c.query("SELECT o.* FROM orders o WHERE " + scope + " ORDER BY o.created_at DESC,o.id DESC LIMIT 20", args), viewer)
        return {"total": counts["total"], "open": counts["open"], "completed": counts["completed"], "overdue": counts["overdue"], "repairMinutes": counts["repair_minutes"], "personMinutes": person_minutes, "score": score, "byType": by_type, "byStatus": by_status, "byAsset": [{k: v for k, v in entry.items() if k != "id"} for entry in by_asset], "repair": repair, "recent": recent}
