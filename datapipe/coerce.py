"""Strict value coercion. No silent rounding, no guessing.

Every function raises ValueError with a message that does NOT contain the offending value.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

TYPES = ("string", "integer", "decimal", "date", "boolean")
DEFAULT_SCALE = 6
DECIMAL_PRECISION = 38
INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1
TRUE_TOKENS = {"true", "t", "yes", "y", "1"}
FALSE_TOKENS = {"false", "f", "no", "n", "0"}

# ASCII digits only: with Unicode \d, Arabic-Indic or fullwidth digits passed the pattern, int() turned them into numbers,
# and the leading-zero rule below (which looks for the ASCII "0") never fired.
_INT_RE = re.compile(r"^[+-]?[0-9]+$")
_DEC_RE = re.compile(r"^[+-]?[0-9]+(\.[0-9]+)?$")


def parse_typed(type_, raw, *, fmt=None, scale=None):
    if type_ == "string":
        return _string(raw)
    if type_ == "integer":
        return _integer(raw)
    if type_ == "decimal":
        return _decimal(raw, DEFAULT_SCALE if scale is None else scale)
    if type_ == "date":
        return _date(raw, fmt or "%Y-%m-%d")
    if type_ == "boolean":
        return _boolean(raw)
    raise ValueError(f"unknown type {type_!r}")


def _string(raw):
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, Decimal):
        return format(raw, "f")
    raise ValueError("not a scalar text value")


def _integer(raw):
    if isinstance(raw, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        if not _INT_RE.match(raw):
            raise ValueError("not a valid integer")
        digits = raw.lstrip("+-")
        if len(digits) > 1 and digits.startswith("0"):
            raise ValueError("leading zeros: identifiers such as '007' must be declared as type string")
        value = int(raw)
    else:
        raise ValueError("not a valid integer")  # floats/Decimals (e.g. 5.0) are rejected, not truncated
    if not INT64_MIN <= value <= INT64_MAX:
        raise ValueError("integer out of 64-bit range")
    return value


def _decimal(raw, scale):
    if isinstance(raw, bool):
        raise ValueError("boolean is not a decimal")
    if isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, str):
        if not _DEC_RE.match(raw):
            raise ValueError("not a valid decimal (no thousands separators, exponents or comma decimals)")
        int_part = raw.lstrip("+-").split(".")[0]
        if len(int_part) > 1 and int_part.startswith("0"):
            raise ValueError("leading zeros: identifiers such as '01234' must be declared as type string")
        try:
            value = Decimal(raw)
        except InvalidOperation:  # pragma: no cover - regex already guards this
            raise ValueError("not a valid decimal")
    else:
        raise ValueError("not a valid decimal")
    if not value.is_finite():
        raise ValueError("not a finite decimal")
    # Magnitude first, via adjusted() (the exponent of the leading digit): abs()/comparison/quantize on an extreme exponent such as
    # 1e999999999 raise decimal.Overflow / InvalidOperation, which used to escape as a raw traceback.
    if value.adjusted() >= DECIMAL_PRECISION - scale:
        raise ValueError("decimal too large")
    exponent = value.as_tuple().exponent
    if exponent < 0 and -exponent > scale:
        # trailing zeros beyond the scale are harmless; anything else would be silently rounded
        try:
            rounded_same = value == value.quantize(Decimal(1).scaleb(-scale))
        except InvalidOperation:
            raise ValueError("decimal out of the supported range")
        if not rounded_same:
            raise ValueError(f"more than {scale} decimal places (would require rounding)")
    return value


_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _date(raw, fmt):
    if not isinstance(raw, str):
        raise ValueError("not a valid date")
    if fmt == "%Y-%m-%d" and _ISO_DATE_RE.fullmatch(raw):
        try:
            return date(int(raw[:4]), int(raw[5:7]), int(raw[8:]))      # same result as strptime below, ~10x faster
        except ValueError:
            pass                                                         # an impossible date: the generic path gives the usual message
    try:
        parsed = datetime.strptime(raw, fmt).date()
    except ValueError:
        raise ValueError(f"not a valid date for format {fmt}")
    if parsed.strftime(fmt) != raw:  # rejects '2026-1-5' style near-misses
        raise ValueError(f"date not in canonical form for format {fmt}")
    return parsed


def _boolean(raw):
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        low = raw.lower()
        if low in TRUE_TOKENS:
            return True
        if low in FALSE_TOKENS:
            return False
    raise ValueError("not a valid boolean")


def quantize_scale(value: Decimal, scale: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-scale))


__all__ = ["parse_typed", "TYPES", "DEFAULT_SCALE", "DECIMAL_PRECISION", "date"]
