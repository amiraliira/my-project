"""Exact default System.Text.Json bytes for persisted idempotency hashes.

The hash is persisted across language migration. Changing Unicode escaping
would falsely reject an unchanged retry of a C#-created request.
"""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN


def _decimal(value):
    if not value.is_finite():
        raise ValueError("Non-finite Decimal")
    # System.Decimal retains trailing zero scale, up to 28 places, and writes
    # fixed notation. Negative zero is serialized without a minus sign.
    desired = min(28, max(0, -value.as_tuple().exponent))
    with localcontext() as context:
        context.prec = 160
        for places in range(desired, -1, -1):
            rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
            if abs(rounded) * (Decimal(10) ** places) <= Decimal("79228162514264337593543950335"):
                return format(rounded.copy_abs() if rounded.is_zero() else rounded, "f")
    raise OverflowError("System.Decimal range exceeded")
def _string(value):
    pieces=['"'];short={8:r"\b",9:r"\t",10:r"\n",12:r"\f",13:r"\r",92:r"\\"}
    for ch in value:
        cp=ord(ch)
        if cp in short:pieces.append(short[cp])
        elif cp<32 or cp>126 or ch in "<>&'+`\"":
            if 0xD800<=cp<=0xDFFF:cp=0xFFFD
            if cp<=0xFFFF:pieces.append(f"\\u{cp:04X}")
            else:
                cp-=0x10000;pieces.append(f"\\u{0xD800+(cp>>10):04X}\\u{0xDC00+(cp&1023):04X}")
        else:pieces.append(ch)
    pieces.append('"');return "".join(pieces)


def canonical_json(value):
    if value is None:return "null"
    if value is True:return "true"
    if value is False:return "false"
    if isinstance(value,str):return _string(value)
    if type(value) is int:return str(value)
    if isinstance(value,Decimal):return _decimal(value)
    if isinstance(value,list):return "["+",".join(canonical_json(x) for x in value)+"]"
    if isinstance(value,dict):return "{"+",".join(_string(k)+":"+canonical_json(v) for k,v in value.items())+"}"
    raise TypeError("Idempotency payload contains an unsupported value")
