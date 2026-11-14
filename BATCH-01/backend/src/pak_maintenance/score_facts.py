"""Immutable completion evidence; capture only inside the approval transaction."""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from .quantity import decimal_value

DECIMAL_MAX = Decimal("79228162514264337593543950335")


def stored_decimal(number):
    # Existing SQLite schema 6 has NUMERIC affinity: fractional coefficients
    # arrive as binary64. Match C# Convert.ToDecimal(double)'s 15 significant
    # digits for compatibility. PostgreSQL NUMERIC remains native Decimal.
    return Decimal(format(number, ".15g")) if isinstance(number, float) else decimal_value(number)


def dotnet_decimal(number):
    """Match System.Decimal's 96-bit coefficient and maximum 28-digit scale."""
    with localcontext() as context:
        context.prec = 90
        for places in range(28, -1, -1):
            result = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
            if abs(result) * (Decimal(10) ** places) <= DECIMAL_MAX:
                return result
    raise OverflowError("Decimal scoring overflow")


def decimal_add(left, right):
    with localcontext() as context:
        context.prec = 90
        return dotnet_decimal(left + right)


def participant_score(minutes, supervisor, head, policy="two-stage-v1"):
    if head is None or supervisor is None and policy != "head-only-v1":
        return None
    with localcontext() as context:
        context.prec = 90
        hours = dotnet_decimal(Decimal(minutes) / 60)
        if policy == "head-only-v1":
            return dotnet_decimal(hours * stored_decimal(head))
        intermediate = dotnet_decimal(hours * stored_decimal(supervisor))
        return dotnet_decimal(intermediate * stored_decimal(head))


def round_score(raw):
    if raw is None:
        return None
    with localcontext() as context:
        context.prec = 90
        return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def capture(connection, order_id):
    rows = connection.query("""
SELECT o.id AS order_id,o.workflow_revision AS revision,p.person_id,p.minutes,
       s.coefficient AS individual_coefficient,legacy.coefficient AS legacy_coefficient,
       h.coefficient AS head_coefficient_exact,head.coefficient AS legacy_head_coefficient,
       s.person_id AS scored_person,legacy.id AS legacy_id,COALESCE(gp.policy,'two-stage-v1') AS grading_policy
FROM orders o JOIN participants p ON p.order_id=o.id
LEFT JOIN participant_grading_policies gp ON gp.order_id=o.id AND gp.revision=o.workflow_revision AND gp.person_id=p.person_id
LEFT JOIN participant_scores s ON s.order_id=o.id AND s.revision=o.workflow_revision AND s.person_id=p.person_id AND s.specialty=p.specialty
LEFT JOIN participant_head_scores h ON h.order_id=o.id AND h.revision=o.workflow_revision AND h.person_id=p.person_id AND h.specialty=p.specialty
LEFT JOIN approvals head ON head.id=(SELECT a.id FROM approvals a WHERE a.order_id=o.id AND a.revision=o.workflow_revision AND a.stage='head' AND a.decision='approve' ORDER BY a.created_at DESC,a.id DESC LIMIT 1)
LEFT JOIN approvals legacy ON s.person_id IS NULL AND legacy.id=(SELECT a.id FROM approvals a WHERE a.order_id=o.id AND a.revision=o.workflow_revision AND a.stage='supervisor' AND a.specialty=p.specialty AND a.decision='approve' AND a.coefficient IS NOT NULL ORDER BY a.created_at DESC,a.id DESC LIMIT 1)
WHERE o.status='completed' AND o.id=:id AND NOT EXISTS(SELECT 1 FROM completed_scores f WHERE f.order_id=o.id AND f.revision=o.workflow_revision AND f.person_id=p.person_id)
""", {"id": order_id})
    for row in rows:
        coefficient = row["individual_coefficient"] if row["scored_person"] is not None else row["legacy_coefficient"]
        coefficient = stored_decimal(coefficient) if coefficient is not None else None
        head_raw = row["head_coefficient_exact"] if row["head_coefficient_exact"] is not None else row["legacy_head_coefficient"]
        head_coefficient = stored_decimal(head_raw) if head_raw is not None else None
        source = "individual" if row["scored_person"] is not None else "legacy-specialty" if row["legacy_id"] is not None else None
        if row["grading_policy"] == "head-only-v1":
            coefficient, source = None, "head-only"
        raw = participant_score(row["minutes"], coefficient, head_coefficient, row["grading_policy"])
        connection.execute("""INSERT INTO completed_scores(order_id,revision,person_id,supervisor_coefficient,head_coefficient,head_coefficient_exact,score_raw,score_cents,source)
VALUES(:order_id,:revision,:person_id,:supervisor,:head,:head_exact,:raw,:cents,:source)""", {
            "order_id": row["order_id"], "revision": row["revision"], "person_id": row["person_id"],
            "supervisor": coefficient, "head": head_coefficient, "head_exact": row["head_coefficient_exact"], "raw": str(raw) if raw is not None else None,
            "cents": int(round_score(raw) * 100) if raw is not None and source in ("individual", "head-only") else None, "source": source})


class ScoreFacts:
    capture = staticmethod(capture)
