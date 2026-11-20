from decimal import Decimal, InvalidOperation
from .text import DIGITS

AREA_ABSOLUTE = Decimal("1")
AREA_RELATIVE = Decimal("0.02")
FINANCIAL_RELATIVE = Decimal("0.05")


def normalize_decimal(value):
    # Floats/bools must not silently introduce approximate business comparisons.
    if isinstance(value, bool) or isinstance(value, float) or value is None:
        return None
    if not isinstance(value, (str, int, Decimal)):
        return None
    text = str(value).translate(DIGITS).strip()
    if len(text) > 80:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() and abs(number) <= Decimal("1e24") else None


def normalize_boolean(value):
    return value if isinstance(value, bool) else None


def tolerance(value, *, financial=False):
    return abs(value) * FINANCIAL_RELATIVE if financial else max(AREA_ABSOLUTE, abs(value) * AREA_RELATIVE)


def near(left, right, *, financial=False):
    return (left is not None and right is not None and left >= 0 and right >= 0
            and abs(left - right) <= tolerance(left, financial=financial))


def bounds(value, *, financial=False):
    delta = tolerance(value, financial=financial)
    return max(Decimal(0), value - delta), value + delta
