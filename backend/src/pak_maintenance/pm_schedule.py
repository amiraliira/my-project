"""Original-period evidence and explicitly approved 48-slot recurrence."""
from dataclasses import dataclass, asdict
import re
from .common import ApiError
from .pm_calendar import MonthSlot, get_week

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


@dataclass(frozen=True)
class PeriodInfo:
    period_class: str
    trigger_kind: str
    interval_months: int | None
    interval_days: int | None
    meter_hours: int | None
    source_text: str

    def json(self):
        return dict(zip(("periodClass", "triggerKind", "intervalMonths", "intervalDays", "meterHours", "sourceText"), asdict(self).values()))


def read_period(original: str) -> PeriodInfo:
    text = original.translate(_DIGITS).replace("\u200c", " ").replace("سالیانه", "سالانه")
    def number(pattern):
        match = re.search(pattern, text)
        return int(match.group(1)) if match else None
    hours, months, days = number(r"(\d+)\s*ساعت"), number(r"(\d+)\s*ما[هه]"), number(r"(\d+)\s*روز")
    if months is None:
        for literal, count in (("یک",1),("دو",2),("سه",3),("چهار",4),("شش",6),("هفت",7),("ده",10),("دوازده",12)):
            if re.search(r"(?<!\w)" + literal + r"\s*ما[هه]", text):
                months = count
                break
    if "ماهانه" in text: months = 1
    if "سالانه" in text: months = 12
    years = number(r"(\d+)\s*سال")
    if years is not None: months = years * 12
    if "یک سال و نیم" in text: months = 18
    if days is None and "پانزده" in text: days = 15
    weekly = "هفتگی" in text
    category = "weekly" if weekly else {1:"monthly",2:"bimonthly",3:"quarterly",6:"six-monthly",10:"ten-monthly",12:"annual"}.get(months)
    if category is None: category = "multi-year" if months and months > 12 else "other-monthly" if months and months > 0 else "day-interval" if days is not None else "unknown"
    calendar = weekly or months is not None or days is not None
    return PeriodInfo(category, "calendar-and-meter" if hours is not None and calendar else "meter" if hours is not None else "calendar" if calendar else "unknown", months, days, hours, original)


def normalize(input, template):
    if input is None: return {"mode":"annual-slots"}
    if not isinstance(input, dict): raise ApiError(400,"مبنای تکرار برنامه نامعتبر است.")
    if input.get("mode") == "annual-slots": return {"mode":"annual-slots"}
    if input.get("mode") != "source-period": raise ApiError(400,"مبنای تکرار برنامه نامعتبر است.")
    unit = input.get("unit")
    values = [input.get(key,0) for key in ("interval","anchorYear","anchorMonth","week")]
    if any(type(value) is not int for value in values): raise ApiError(400,"پارامتر دوره باید عدد صحیح باشد.")
    interval, year, month, week = values
    if unit not in ("week","month") or not 1 <= interval <= (48 if unit=="week" else 120) or not 1<=month<=12 or not 1<=week<=4: raise ApiError(400,"فاصله، سال، ماه و هفتهٔ مبنای تکرار را معتبر تعیین کنید.")
    try: get_week(year,month,week)
    except ValueError: raise ApiError(400,"سال مبنای برنامه خارج از تقویم پشتیبانی‌شده است.") from None
    from .pm_form_info import period
    policy = input.get("meterPolicy", "")
    if not policy and period(template).meter_hours is None: policy="calendar-only"
    if policy not in ("calendar-only","meter-only","whichever-first"): raise ApiError(400,"برای برگه دارای ساعت دستگاه، مبنای صدور را صریح تعیین کنید.")
    return dict(mode="source-period", unit=unit, interval=interval, anchorYear=year, anchorMonth=month, week=week, meterPolicy=policy)


def source_period(plan): return plan.get("recurrence",{}).get("mode")=="source-period"


def slots_for_month(plan, year, month):
    if not source_period(plan):
        return sorted((MonthSlot(month,s["week"]) for s in plan.get("slots",[]) if s.get("month")==month), key=lambda s:s.ordinal)
    r=plan["recurrence"]
    interval, ay, am, aw = (r[key] for key in ("interval","anchorYear","anchorMonth","week"))
    if type(interval) is not int or interval <= 0: raise ValueError("Invalid persisted PM recurrence")
    delta=(year-ay)*12+month-am
    if r["unit"]=="month": return [MonthSlot(month,aw)] if delta>=0 and delta%interval==0 else []
    if r["unit"]!="week": raise ValueError("Invalid persisted PM unit")
    return [MonthSlot(month,w) for w in range(1,5) if delta*4+w-aw>=0 and (delta*4+w-aw)%interval==0]


def year_slots(plan,year): return [{"month":s.month,"week":s.ordinal} for month in range(1,13) for s in slots_for_month(plan,year,month)]


def non_calendar_blocker_codes(template):
    from .pm_form_info import has_activities, empty_pump_roster
    return (["control-form"] if template.get("formKind")=="control" else []) + ([] if has_activities(template) else ["no-activities"]) + (["missing-group-members"] if empty_pump_roster(template) else [])


def non_calendar_blockers(template):
    labels={"control-form":"برگهٔ کنترل تحویل اسناد، فعالیت اجرایی یک دستگاه نیست.","no-activities":"برگه فعالیت اجرایی مشخصی ندارد.","missing-group-members":"فعالیت‌ها آماده؛ فهرست پمپ‌های سالن "+template.get("hall","")+" تکمیل شود."}
    return [labels[code] for code in non_calendar_blocker_codes(template)]
