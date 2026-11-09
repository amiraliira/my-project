"""Shared domain types; no HTTP, telemetry, or network side effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True, slots=True)
class User:
    id: str
    code: str = ""
    name: str = ""
    role: str = ""
    specialty: str = ""
    active: bool = True
    password_hash: str = ""
    session_epoch: int = 0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "User":
        return cls(**{key: row[key] for key in cls.__dataclass_fields__ if key in row})

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "code": self.code, "name": self.name,
                "role": self.role, "specialty": self.specialty,
                "active": bool(self.active), "version": self.session_epoch}


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("An aware UTC timestamp is required")
    # Match DateTimeOffset.ToString("O"): fixed width preserves existing SQL ordering.
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond:06d}0+00:00"


def now() -> str:
    return iso_utc(datetime.now(timezone.utc))


def today() -> str:
    return datetime.now(ZoneInfo("Asia/Tehran")).date().isoformat()


def decimal_value(value: Any, field: str = "عدد") -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise ApiError(400, f"{field} معتبر نیست.")
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise InvalidOperation
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (ValueError, InvalidOperation, OverflowError):
        raise ApiError(400, f"{field} معتبر نیست.") from None


def strict_int(value: Any, *, minimum: int | None = None,
               maximum: int | None = None, field: str = "عدد") -> int:
    if type(value) is not int:
        raise ApiError(400, f"{field} باید عدد صحیح باشد.")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ApiError(400, f"{field} خارج از محدوده است.")
    return value


def text(value: Any, *, maximum: int = 1000, field: str = "متن", trim: bool = True) -> str:
    if not isinstance(value, str):
        raise ApiError(400, f"{field} معتبر نیست.")
    result = value.strip() if trim else value
    if len(result) > maximum or "\x00" in result:
        raise ApiError(400, f"{field} خارج از محدوده است.")
    return result


def page_bounds(page: int | None = None, page_size: int | None = None,
                maximum: int = 100) -> tuple[int, int]:
    return (strict_int(1 if page is None else page, minimum=1, maximum=1_000_000),
            strict_int(25 if page_size is None else page_size, minimum=1, maximum=maximum))


def parse_date(value: Any) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ApiError(400, "تاریخ معتبر نیست.")
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError
        return result
    except ValueError:
        raise ApiError(400, "تاریخ معتبر نیست.") from None


def json_default(value: Any) -> Any:
    # Persist Decimal as an exact JSON number, via dumps below, never a quoted string.
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON type {type(value).__name__}")


def dumps(value: Any) -> str:
    """Serialize finite Decimal values exactly without allowing JSON token injection."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite JSON number")
        return str(value)
    if type(value) is int:
        return str(value)
    if isinstance(value, float):
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(dumps(item) for item in value) + "]"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return "{" + ",".join(dumps(key) + ":" + dumps(item) for key, item in value.items()) + "}"
    return dumps(json_default(value))


def loads(value: str) -> Any:
    def invalid_constant(_: str):
        raise ValueError("Non-finite JSON number")
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = item
        return result
    def number(raw):
        # Bound lexical/scale work before downstream fixed-point arithmetic.
        if len(raw) > 128:
            raise ValueError("JSON number exceeds its precision limit")
        try:
            result = Decimal(raw)
        except ArithmeticError:
            raise ValueError("Invalid JSON number") from None
        if not result.is_finite() or not -100 <= result.as_tuple().exponent <= 100:
            raise ValueError("JSON number exceeds its scale limit")
        return result
    def integer(raw):
        if len(raw) > 100:
            raise ValueError("JSON integer exceeds its precision limit")
        return int(raw)
    result = json.loads(value, parse_float=number, parse_int=integer,
                        parse_constant=invalid_constant, object_pairs_hook=unique_object)
    pending = [result]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise ValueError("Invalid Unicode JSON text") from None
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return result


def audit(connection, user: User | None, action: str, entity: str,
          entity_id: str, details: Any, *, created_at: str | None = None) -> None:
    connection.execute(
        "INSERT INTO audit(id,actor_id,actor_name,action,entity,entity_id,details,created_at) "
        "VALUES(:id,:actor,:name,:action,:entity,:entity_id,:details,:date)",
        {"id": str(uuid4()), "actor": user.id if user else None,
         "name": user.name if user else "سیستم", "action": action, "entity": entity,
         "entity_id": entity_id, "details": dumps(details), "date": created_at or now()})
