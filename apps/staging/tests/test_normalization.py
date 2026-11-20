from decimal import Decimal
import pytest
from apps.staging.normalization.text import normalize_text
from apps.staging.normalization.phone import normalize_phone, phone_query_forms
from apps.staging.normalization.numeric import normalize_decimal, normalize_boolean, near
from apps.staging.duplicates.snapshots import property_snapshot, customer_snapshot


@pytest.mark.parametrize("value,expected", [
    ("  Main   Street  ", "main street"), ("علي كريمي", "علی کریمی"),
    ("الف\u00a0\u2003ب\tج", "الف ب ج"), ("خانه\u200cها", "خانه\u200cها"),
    ("", None), (None, None), (12, None),
])
def test_text(value, expected):
    assert normalize_text(value) == expected


@pytest.mark.parametrize("value", ["09121234567", "+989121234567", "00989121234567", "989121234567",
                                  "(0912) 123-4567", "+98 (912) 123 4567", "۰۹۱۲۱۲۳۴۵۶۷", "٠٩١٢١٢٣٤٥٦٧"])
def test_iranian_mobile(value):
    assert normalize_phone(value) == "09121234567"


@pytest.mark.parametrize("value", ["9121234567", "+19121234567", "0912123456", "091212345678", "0912abc34567", "", None, 9121234567, "09121234567 ext 1"])
def test_ambiguous_phone_not_comparable(value):
    assert normalize_phone(value) is None
    assert phone_query_forms(value) == ()


def test_bounded_forms_are_equivalent():
    forms = phone_query_forms("09121234567")
    assert 4 <= len(forms) <= 64
    assert {normalize_phone(value) for value in forms} == {"09121234567"}
    assert {"09121234567", "+989121234567", "989121234567", "00989121234567"} <= set(forms)


@pytest.mark.parametrize("value", ["100.0100", Decimal("100.0100"), "۱۰۰.۰۱۰۰"])
def test_exact_decimal(value):
    assert normalize_decimal(value) == Decimal("100.01")


@pytest.mark.parametrize("value", [True, 0.1, "NaN", "Infinity", "1e999", "1,000", None])
def test_numeric_unknown(value):
    assert normalize_decimal(value) is None


def test_boolean_and_tolerance():
    assert normalize_boolean(True) is True
    assert normalize_boolean(False) is False
    assert normalize_boolean(None) is None
    assert normalize_boolean("false") is None
    assert normalize_boolean(0) is None
    assert near(Decimal("100"), Decimal("102"))
    assert not near(Decimal("100"), Decimal("102.01"))
    assert not near(None, None)


def test_snapshot_is_ephemeral_and_missing_values_do_not_match():
    raw = {"address": " علي  كريمي ", "area": "100.00", "parking": False}
    before = dict(raw)
    result = property_snapshot(raw)
    assert raw == before and result["address"] == "علی کریمی"
    assert result["parking"] is False and result["elevator"] is None
    assert customer_snapshot({"preferred_regions": "invalid"})["region_ids"] == ()
