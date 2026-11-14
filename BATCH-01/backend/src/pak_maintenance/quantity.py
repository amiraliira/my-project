"""Exact integer micro-units shared by both database providers."""
from decimal import Decimal
from .common import ApiError, decimal_value

SCALE = 1_000_000
MAXIMUM_INPUT = Decimal("1000000000")
INT64_MAX = (1 << 63) - 1


def units(amount):
    amount = decimal_value(amount)
    if amount.copy_abs() > MAXIMUM_INPUT:
        raise ApiError(400, "مقدار قطعه باید حداکثر یک میلیارد و دارای حداکثر شش رقم اعشار باشد.")
    sign, digits, exponent = amount.as_tuple()
    # Constructing a Decimal tuple shifts its exponent exactly. Multiplication
    # would honor the ambient context and could silently discard a tiny tail.
    scaled = Decimal((sign, digits, exponent + 6))
    if scaled != scaled.to_integral_value():
        raise ApiError(400, "مقدار قطعه باید حداکثر یک میلیارد و دارای حداکثر شش رقم اعشار باشد.")
    return int(scaled)


def value(quantity_units):
    return Decimal(quantity_units) / SCALE


def legacy_units(amount):
    amount = decimal_value(amount)
    sign, digits, exponent = amount.as_tuple()
    scaled = Decimal((sign, digits, exponent + 6))
    if scaled.copy_abs() > INT64_MAX or scaled != scaled.to_integral_value():
        raise ValueError("Existing quantity cannot be represented exactly in integer micro-units")
    return int(scaled)


class Quantity:
    units = staticmethod(units)
    value = staticmethod(value)
    legacy_units = staticmethod(legacy_units)
