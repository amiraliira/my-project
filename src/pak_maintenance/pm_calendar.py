"""Four immutable Saturday windows per Persian month; no completion-date drift.

Conversion uses an immutable 68 KiB year-start table generated from the actual
.NET 10 PersianCalendar used by the deployed application. This avoids the
one-day leap-year drift of arithmetic approximations, including Persian 1503.
Only the incomplete final year 9378 is excluded; all full years 1..9377 work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

MAXIMUM_SUPPORTED_PERSIAN_YEAR = 9377
TEHRAN = ZoneInfo("Asia/Tehran")
_TABLE_BYTES = Path(__file__).with_name("persian_year_starts.json").read_bytes()
if hashlib.sha256(_TABLE_BYTES).hexdigest() != "5a98d07ceffc9d2d5b9e909c9d0afcc82375600e45d4d00d8e2aacdd0c142712":
    raise RuntimeError("Persian calendar reference table integrity check failed")
_YEAR_STARTS = tuple(json.loads(_TABLE_BYTES))
if len(_YEAR_STARTS) != 9378 or any(b-a not in (365,366) for a,b in zip(_YEAR_STARTS,_YEAR_STARTS[1:])):
    raise RuntimeError("Persian calendar reference table is invalid")


def _year_start(year: int) -> tuple[date, int]:
    if type(year) is not int or not 1 <= year <= MAXIMUM_SUPPORTED_PERSIAN_YEAR:
        raise ValueError("Persian year outside full calendar range (1..9377)")
    start=_YEAR_STARTS[year-1]
    return date.fromordinal(start), 0 if _YEAR_STARTS[year]-start==366 else 1


def from_persian(year: int, month: int, day: int) -> date:
    start, leap = _year_start(year)
    if type(month) is not int or not 1 <= month <= 12:
        raise ValueError("Invalid Persian month")
    maximum = 31 if month <= 6 else 30 if month <= 11 or leap == 0 else 29
    if type(day) is not int or not 1 <= day <= maximum:
        raise ValueError("Invalid Persian day")
    offset = (month - 1) * 31 if month <= 7 else 186 + (month - 7) * 30
    return start + timedelta(days=offset + day - 1)


def to_persian(value: date) -> tuple[int, int, int]:
    year = bisect_right(_YEAR_STARTS,value.toordinal())
    start, _ = _year_start(year)
    delta = (value - start).days
    if delta < 186:
        month, day = divmod(delta, 31)
        return year, month + 1, day + 1
    month, day = divmod(delta - 186, 30)
    return year, month + 7, day + 1


def format_persian(value: date) -> str:
    y, m, d = to_persian(value)
    return f"{y:04}/{m:02}/{d:02}"


def tehran_date(instant: datetime) -> date:
    if instant.tzinfo is None:
        raise ValueError("An explicit instant offset is required")
    return instant.astimezone(TEHRAN).date()


def utc_boundary(value: date) -> datetime:
    """Resolve a historical midnight gap to first valid second; earliest fold."""
    local = datetime.combine(value, time.min)
    for seconds in range(0, 4 * 3600 + 1):
        candidate = local + timedelta(seconds=seconds)
        options = []
        for fold in (0, 1):
            aware = candidate.replace(tzinfo=TEHRAN, fold=fold)
            utc = aware.astimezone(timezone.utc)
            if utc.astimezone(TEHRAN).replace(tzinfo=None) == candidate:
                options.append(utc)
        if options:
            return min(options)
    raise ValueError("Tehran midnight gap exceeds four hours")


def local_execution_utc(value: datetime) -> datetime:
    options = set()
    for fold in (0, 1):
        utc = value.replace(tzinfo=TEHRAN, fold=fold).astimezone(timezone.utc)
        if utc.astimezone(TEHRAN).replace(tzinfo=None) == value:
            options.add(utc)
    if len(options) != 1:
        raise ValueError("Execution time is ambiguous or invalid in Tehran")
    return options.pop()


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "0+00:00")


def parse_utc(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Stored instant requires UTC offset")
    return instant.astimezone(timezone.utc)


@dataclass(frozen=True)
class MonthSlot:
    month: int
    ordinal: int

    def __post_init__(self):
        if type(self.month) is not int or not 1 <= self.month <= 12 or type(self.ordinal) is not int or not 1 <= self.ordinal <= 4:
            raise ValueError("Invalid PM slot")

    @property
    def key(self):
        return f"M{self.month:02}-W{self.ordinal}"


@dataclass(frozen=True)
class WeekWindow:
    persian_year: int
    slot: MonthSlot
    start_date: date

    @property
    def key(self):
        return f"{self.persian_year:04}-{self.slot.key}"

    @property
    def end_exclusive_date(self):
        return self.start_date + timedelta(days=7)

    @property
    def end_date(self):
        return self.start_date + timedelta(days=6)

    def utc_window(self):
        return utc_boundary(self.start_date), utc_boundary(self.end_exclusive_date)


def get_week(year: int, month: int, ordinal: int) -> WeekWindow:
    slot = MonthSlot(month, ordinal)
    first = from_persian(year, month, 1)
    start = first + timedelta(days=(5 - first.weekday()) % 7 + (ordinal - 1) * 7)
    return WeekWindow(year, slot, start)


def get_year(year: int) -> tuple[WeekWindow, ...]:
    return tuple(get_week(year, month, week) for month in range(1, 13) for week in range(1, 5))


def enumerate_active(first: date, through: date):
    y, m, _ = to_persian(first)
    ey, em, _ = to_persian(through)
    count = (ey - y) * 12 + em - m + 1
    if through < first or count > 24:
        raise ValueError("PM enumeration requires ascending range of at most 24 months")
    for _ in range(count):
        for ordinal in range(1, 5):
            window = get_week(y, m, ordinal)
            if first <= window.start_date <= through:
                yield window
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def month_key(value: date) -> int:
    y, m, _ = to_persian(value)
    return y * 100 + m


def next_month(key: int) -> int:
    return (key // 100 + 1) * 100 + 1 if key % 100 == 12 else key + 1
