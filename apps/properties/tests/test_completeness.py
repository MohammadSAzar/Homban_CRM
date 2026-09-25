from decimal import Decimal, localcontext

import pytest
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction

from apps.locations.models import Region
from apps.properties.models import PropertyFile, derive_price
from .test_models import context, make_file
from .test_api import world, body, client_for, url, BASE

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("field,value", [("region", None), ("area", None), ("area", 0), ("area", -1), ("bedrooms", None), ("bedrooms", -1), ("total_price", None)])
def test_required_model_and_sql(context, field, value):
    with pytest.raises(ValidationError):
        make_file(**{**context, field: value})
    item = make_file(**context)
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        PropertyFile.objects.filter(pk=item.pk).update(**{field: value})


@pytest.mark.parametrize("total,area,expected", [
    ("15277862300", "100", "152000000"), ("15200000000", "100", "152000000"),
    ("0", "0.01", "0"), ("999999.99", "1", "0"),
    ("999999999999999999.99", "1000000000", "999000000"),
])
def test_exact_flooring(context, total, area, expected):
    item = make_file(**context, total_price=Decimal(total), area=Decimal(area), bedrooms=0)
    item.refresh_from_db()
    assert item.price_per_square_meter == Decimal(expected)
    with localcontext() as ctx:
        ctx.prec = 4
        assert derive_price(Decimal(total), Decimal(area)) == Decimal(expected)


def test_normal_and_partial_recompute(context):
    item = make_file(**context, total_price=20000000000, area=100)
    item.area = 50
    item.save()
    assert item.price_per_square_meter == 400000000
    item.total_price = 10000000000
    item.save(update_fields=["total_price"])
    item.refresh_from_db()
    assert item.price_per_square_meter == 200000000
    item.total_price = 999  # Excluded in-memory change must not affect stored price.
    item.area = 100
    item.save(update_fields=["area"])
    item.refresh_from_db()
    assert item.total_price == 10000000000 and item.price_per_square_meter == 100000000
    item.price_per_square_meter = 123
    item.save(update_fields=["price_per_square_meter"])
    item.refresh_from_db()
    assert item.price_per_square_meter == 100000000


def test_inactive_region_retention_and_reselection(context):
    item = make_file(**context)
    region = context["region"]
    region.is_active = False
    region.save()
    item.description = "تاریخی"
    item.save(update_fields=["description"])
    with pytest.raises(ValidationError):
        make_file(**context)
    new = Region.objects.create(workspace=context["workspace"], city=context["city"], name="جدید")
    item.region = new
    item.save(update_fields=["region"])
    item.region = region
    with pytest.raises(ValidationError):
        item.save(update_fields=["region"])
    item.refresh_from_db()
    assert item.region == new


@pytest.mark.parametrize("missing", ["deposit_amount", "monthly_rent"])
def test_rent_required_and_sql(context, missing):
    fields = {**context, "transaction_type": "rent", missing: None}
    with pytest.raises(ValidationError):
        make_file(**fields)
    item = make_file(**{**context, "transaction_type": "rent"})
    with pytest.raises(IntegrityError), transaction.atomic():
        PropertyFile.objects.filter(pk=item.pk).update(**{missing: None})


@pytest.mark.parametrize("deposit,rent", [(100, 0), (0, 100)])
def test_zero_rent_financials(context, deposit, rent):
    item = make_file(**{**context, "transaction_type": "rent"}, deposit_amount=deposit, monthly_rent=rent)
    assert item.total_price is None and item.price_per_square_meter is None


def test_partial_type_changes_validate_persisted_finances(context):
    item = make_file(**context)
    item.transaction_type = "rent"
    item.total_price = None
    item.deposit_amount = 100
    item.monthly_rent = 0
    with pytest.raises(ValidationError):
        item.save(update_fields=["transaction_type"])
    item.save(update_fields=["transaction_type", "total_price", "deposit_amount", "monthly_rent"])
    item.refresh_from_db()
    assert item.price_per_square_meter is None
    item.transaction_type = "sale"
    item.deposit_amount = item.monthly_rent = None
    with pytest.raises(ValidationError):
        item.save()
    item.total_price = 15277862300
    item.save()
    item.refresh_from_db()
    assert item.price_per_square_meter == 152000000


@pytest.mark.parametrize("field", ["region", "area", "bedrooms", "total_price"])
def test_api_missing_required_fields(world, field):
    payload = body(world)
    payload.pop(field)
    response = client_for(world.consultant).post(BASE, payload, format="json")
    assert response.status_code == 400 and field in response.data


def test_api_transitions_recompute_and_rollback(world):
    client = client_for(world.consultant)
    endpoint = url(world.own)
    assert client.patch(endpoint, {"total_price": 15277862300, "area": 100}, format="json").data["price_per_square_meter"] == "152000000.00"
    assert client.patch(endpoint, {"area": 50}, format="json").data["price_per_square_meter"] == "305000000.00"
    for values in ({"area": 0}, {"region": None}, {"transaction_type": "rent", "total_price": None}, {"price_per_square_meter": 1}):
        assert client.patch(endpoint, {**values, "valuable_reasons": [], "description": "rollback"}, format="json").status_code == 400
        world.own.refresh_from_db()
        assert world.own.description != "rollback" and world.own.valuable_reasons.exists()
    response = client.patch(endpoint, {"transaction_type": "rent", "total_price": None, "deposit_amount": 100, "monthly_rent": 0}, format="json")
    assert response.status_code == 200 and response.data["price_per_square_meter"] is None
    assert client.patch(endpoint, {"transaction_type": "sale", "deposit_amount": None, "monthly_rent": None}, format="json").status_code == 400
    response = client.patch(endpoint, {"transaction_type": "sale", "deposit_amount": None, "monthly_rent": None, "total_price": 10000000000}, format="json")
    assert response.status_code == 200 and response.data["price_per_square_meter"] == "200000000.00"


@pytest.mark.parametrize("missing", ["deposit_amount", "monthly_rent"])
def test_api_rent_amount_required(world, missing):
    data = body(world, transaction_type="rent", total_price=None, deposit_amount=0, monthly_rent=100)
    data.pop(missing)
    response = client_for(world.consultant).post(BASE, data, format="json")
    assert response.status_code == 400 and missing in response.data
