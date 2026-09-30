from datetime import date
from decimal import Decimal

import pytest

from datapipe.coerce import parse_typed


def bad(type_, raw, **kw):
    with pytest.raises(ValueError):
        parse_typed(type_, raw, **kw)


def test_integer_basic_and_sign():
    assert parse_typed("integer", "42") == 42
    assert parse_typed("integer", "-7") == -7
    assert parse_typed("integer", "0") == 0


@pytest.mark.parametrize("raw", ["007", "1.0", "1e3", " 1", "1,000", "abc", "", "0x10"])
def test_integer_rejects_lookalikes(raw):
    bad("integer", raw)


def test_integer_boundaries_int64():
    assert parse_typed("integer", str(2 ** 63 - 1)) == 2 ** 63 - 1
    bad("integer", str(2 ** 63))
    bad("integer", str(-(2 ** 63) - 1))


def test_integer_rejects_bool_float_and_decimal_from_json():
    bad("integer", True)
    bad("integer", 5.0)
    bad("integer", Decimal("5.0"))


def test_decimal_exact_no_float_noise():
    assert parse_typed("decimal", "0.1", scale=2) + parse_typed("decimal", "0.2", scale=2) == Decimal("0.3")


def test_decimal_scale_boundary_and_no_silent_rounding():
    assert parse_typed("decimal", "1.23", scale=2) == Decimal("1.23")
    assert parse_typed("decimal", "1.230", scale=2) == Decimal("1.230")   # trailing zero is not rounding
    bad("decimal", "1.234", scale=2)


@pytest.mark.parametrize("raw", ["01234", "007.5", "12,50", "1,000.00", "1e5", "NaN", "Infinity", ".5", "5.", "", "--1"])
def test_decimal_rejects_ambiguous_formats(raw):
    bad("decimal", raw)


def test_decimal_too_large_for_column():
    bad("decimal", "1" + "0" * 33, scale=6)


def test_date_strict_iso_and_ambiguous():
    assert parse_typed("date", "2026-01-05") == date(2026, 1, 5)
    bad("date", "03/04/2026")                                    # ambiguous, not the declared format
    bad("date", "2026-1-5")                                      # non-canonical
    bad("date", "2026-02-30")                                    # impossible date
    assert parse_typed("date", "03/04/2026", fmt="%d/%m/%Y") == date(2026, 4, 3)


def test_boolean_tokens():
    assert parse_typed("boolean", "TRUE") is True
    assert parse_typed("boolean", "0") is False
    assert parse_typed("boolean", 1) is True
    bad("boolean", "maybe")
    bad("boolean", 2)


def test_string_from_json_numbers():
    assert parse_typed("string", 12345) == "12345"
    assert parse_typed("string", True) == "true"
    bad("string", None)


def test_decimal_zero_forms_still_valid():
    assert parse_typed("decimal", "0") == 0
    assert parse_typed("decimal", "0.50", scale=2) == Decimal("0.50")
    assert parse_typed("decimal", "-0.5", scale=2) == Decimal("-0.5")
