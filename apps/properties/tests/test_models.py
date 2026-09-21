from decimal import Decimal
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.accounts.models import User
from apps.locations.models import City, Region
from apps.organizations.models import Workspace
from apps.properties.models import PropertyFile, PropertyFileImage, PropertyFileValuableReason
from apps.properties.services import create_property_file


pytestmark = pytest.mark.django_db


@pytest.fixture
def context():
    workspace = Workspace.objects.create(name="تهران", slug="property-test", customer_type="consultant")
    user = User.objects.create_user(username="ali", workspace=workspace, role="consultant")
    city = City.objects.create(workspace=workspace, name="تهران")
    region = Region.objects.create(workspace=workspace, city=city, name="ونک")
    return dict(workspace=workspace, assigned_to=user, city=city, region=region, transaction_type="sale")


@pytest.fixture
def foreign():
    workspace = Workspace.objects.create(name="دیگر", slug="property-other", customer_type="consultant")
    user = User.objects.create_user(username="ali", workspace=workspace, role="consultant")
    city = City.objects.create(workspace=workspace, name="تهران")
    region = Region.objects.create(workspace=workspace, city=city, name="ونک")
    return dict(workspace=workspace, assigned_to=user, city=city, region=region)


@pytest.mark.parametrize("kind,amounts", [
    ("sale", {"price_per_square_meter": Decimal("125000000.25"), "total_price": Decimal("12500000025.00")}),
    ("rent", {"deposit_amount": Decimal("500000000.00"), "monthly_rent": Decimal("15000000.50")}),
])
def test_valid_file_round_trip(context, kind, amounts):
    context["transaction_type"] = kind
    item = create_property_file(**context, **amounts, area=Decimal("100.00"),
        bedrooms=2, total_floors=5, units_per_floor=2, unit_floor=0, building_age=0,
        owner_name="مالک", owner_phone="09121111111", visit_contact_phone="09122222222",
        address="نشانی ملک", description="توضیحات مستقل فایل")
    item.refresh_from_db()
    assert item.assigned_to_id == context["assigned_to"].pk
    assert item.city_id == context["city"].pk
    assert item.region_id == context["region"].pk
    assert item.owner_phone != item.visit_contact_phone
    assert item.owner_phone == "09121111111"
    assert item.visit_contact_phone == "09122222222"
    assert item.description == "توضیحات مستقل فایل"
    assert item.address == "نشانی ملک"
    assert item.source == "manual"
    assert item.area == Decimal("100.00")
    for field, value in amounts.items():
        assert getattr(item, field) == value
        assert isinstance(getattr(item, field), Decimal)
    assert timezone.is_aware(item.created_at)
    assert timezone.is_aware(item.updated_at)


@pytest.mark.parametrize("mode", ["none", "custom", "divar"])
def test_region_optional_in_every_mode(context, mode):
    workspace = context["workspace"]
    workspace.region_mode = mode
    workspace.save()
    context["region"] = None
    item = PropertyFile.objects.create(**context)
    assert item.region is None
    assert item.area is None
    assert item.total_price is None


@pytest.mark.parametrize("field", ["assigned_to", "city", "region"])
def test_foreign_relationship_rejected(context, foreign, field):
    context[field] = foreign[field]
    with pytest.raises(ValidationError) as error:
        PropertyFile.objects.create(**context)
    assert field in error.value.message_dict
    assert not PropertyFile.objects.exists()


def test_city_region_mismatch(context):
    context["city"] = City.objects.create(workspace=context["workspace"], name="ری")
    with pytest.raises(ValidationError, match="منطقه"):
        PropertyFile.objects.create(**context)


@pytest.mark.parametrize("role", ["agency_manager", "range_manager", "secretary", "admin"])
def test_consultant_required(context, role):
    user = context["assigned_to"]
    user.role = role
    user.save()
    with pytest.raises(ValidationError, match="مشاور"):
        PropertyFile.objects.create(**context)


def test_workspace_less_assignee_rejected(context):
    user = context["assigned_to"]
    user.workspace = None
    user.save()
    with pytest.raises(ValidationError):
        PropertyFile.objects.create(**context)


@pytest.mark.parametrize("field", ["assigned_to", "city", "region"])
def test_missing_relationship_raises_validation_not_does_not_exist(context, field):
    context.pop(field)
    context[f"{field}_id"] = uuid.uuid4()
    with pytest.raises(ValidationError):
        PropertyFile.objects.create(**context)


@pytest.mark.parametrize("kind,field", [("sale", "deposit_amount"), ("sale", "monthly_rent"), ("rent", "total_price"), ("rent", "price_per_square_meter")])
@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("1.50")])
def test_incompatible_financial_fields(context, kind, field, amount):
    context["transaction_type"] = kind
    with pytest.raises(ValidationError) as error:
        PropertyFile.objects.create(**context, **{field: amount})
    assert field in error.value.message_dict


NUMERIC_FIELDS = ["area", "bedrooms", "total_floors", "units_per_floor", "unit_floor", "building_age", "price_per_square_meter", "total_price", "deposit_amount", "monthly_rent"]


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_negative_values_rejected(context, field):
    if field in ("deposit_amount", "monthly_rent"):
        context["transaction_type"] = "rent"
    with pytest.raises(ValidationError) as error:
        PropertyFile.objects.create(**context, **{field: -1})
    assert field in error.value.message_dict


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_database_rejects_negative_even_when_validation_bypassed(context, field):
    if field in ("deposit_amount", "monthly_rent"):
        context["transaction_type"] = "rent"
    item = PropertyFile.objects.create(**context)
    # MySQL unsigned integer columns reject negatives before CHECK evaluation.
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        PropertyFile.objects.filter(pk=item.pk).update(**{field: -1})


@pytest.mark.parametrize("changes", [
    {"deposit_amount": 0}, {"monthly_rent": 0}, {"transaction_type": "invalid"},
    {"transaction_type": "rent", "total_price": 1},
    {"transaction_type": "rent", "price_per_square_meter": 1},
    {"status": "invalid"}, {"code": ""},
])
def test_database_choices_and_financial_constraints(context, changes):
    item = PropertyFile.objects.create(**context)
    with pytest.raises(IntegrityError), transaction.atomic():
        PropertyFile.objects.filter(pk=item.pk).update(**changes)


@pytest.mark.parametrize("value", [True, False, None])
def test_facilities_preserve_unknown_and_false(context, value):
    fields = dict.fromkeys(["parking", "storage", "elevator", "balcony"], value)
    item = PropertyFile.objects.create(**context, **fields)
    item.refresh_from_db()
    assert all(getattr(item, field) is value for field in fields)


@pytest.mark.parametrize("status", PropertyFile.Status.values)
def test_status_persists_without_deletion(context, status):
    item = create_property_file(**context, image_references=["media/reference"], valuable_reasons=["قیمت مناسب"])
    item.status = status
    item.save()
    item.refresh_from_db()
    assert item.status == status
    assert item.images.count() == 1
    assert item.valuable_reasons.count() == 1
    assert context["assigned_to"].is_active


def test_multiple_reasons_and_ordered_images(context):
    item = create_property_file(**context, is_valuable=True,
        valuable_reasons=["قیمت مناسب", "موقعیت مناسب"],
        image_references=["https://example.com/front.jpg", "media/interior.jpg"])
    assert set(item.valuable_reasons.values_list("reason", flat=True)) == {"قیمت مناسب", "موقعیت مناسب"}
    assert list(item.images.values_list("reference", flat=True)) == ["https://example.com/front.jpg", "media/interior.jpg"]
    assert list(item.images.values_list("sort_order", flat=True)) == [0, 1]
    image = item.images.first()
    image.sort_order = 3
    image.save()
    assert item.images.last().pk == image.pk
    assert timezone.is_aware(image.created_at)
    with pytest.raises(ValidationError):
        PropertyFileValuableReason.objects.create(property_file=item, reason="قیمت مناسب")
    with pytest.raises(IntegrityError), transaction.atomic():
        PropertyFileValuableReason.objects.bulk_create([PropertyFileValuableReason(property_file=item, reason="قیمت مناسب")])


def test_valuable_without_reasons_is_supported(context):
    item = PropertyFile.objects.create(**context, is_valuable=True)
    assert item.valuable_reasons.count() == 0


@pytest.mark.parametrize("kwargs", [
    {"valuable_reasons": ["قیمت مناسب", "قیمت مناسب"]},
    {"image_references": ["valid-reference", ""]},
])
def test_aggregate_creation_rollback(context, kwargs):
    with pytest.raises(ValidationError):
        create_property_file(**context, **kwargs)
    assert not PropertyFile.objects.exists()
    assert not PropertyFileImage.objects.exists()
    assert not PropertyFileValuableReason.objects.exists()


def test_image_order_validation_and_database(context):
    item = PropertyFile.objects.create(**context)
    with pytest.raises(ValidationError):
        PropertyFileImage.objects.create(property_file=item, reference="media/image", sort_order=-1)
    image = PropertyFileImage.objects.create(property_file=item, reference="media/image")
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        PropertyFileImage.objects.filter(pk=image.pk).update(sort_order=-1)


def test_referenced_region_cannot_move_to_another_city(context):
    item = PropertyFile.objects.create(**context)
    region = context["region"]
    region.city = City.objects.create(workspace=context["workspace"], name="ری")
    with pytest.raises(ValidationError):
        region.save()
    region.refresh_from_db()
    assert region.city_id == item.city_id


def test_codes_are_unique_stable_server_derived(context):
    first = PropertyFile.objects.create(**context)
    second = PropertyFile.objects.create(**context)
    assert first.code != second.code
    assert first.code == f"PF-{first.pk.hex.upper()}"
    code = first.code
    first.description = "ویرایش"
    first.save()
    first.refresh_from_db()
    assert first.code == code == str(first)
    assert PropertyFile.objects.get(code=code).pk == first.pk
    first.code = second.code
    with pytest.raises(ValidationError):
        first.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        PropertyFile.objects.filter(pk=first.pk).update(code=second.code)
    with pytest.raises(ValidationError):
        PropertyFile.objects.create(**context, code="client-code")


def test_workspace_cannot_be_changed(context, foreign):
    item = PropertyFile.objects.create(**context)
    for name, value in foreign.items():
        setattr(item, name, value)
    with pytest.raises(ValidationError) as error:
        item.save()
    assert "workspace" in error.value.message_dict
    item.refresh_from_db()
    assert item.workspace == context["workspace"]


@pytest.mark.parametrize("field", ["workspace", "assigned_to", "city", "region"])
def test_related_business_records_are_protected(context, field):
    item = create_property_file(**context, image_references=["image"], valuable_reasons=["موقعیت"])
    with pytest.raises(ProtectedError):
        context[field].delete()
    assert PropertyFile.objects.filter(pk=item.pk).exists()
    assert item.images.exists()
    assert item.valuable_reasons.exists()


def test_transaction_change_requires_clearing_previous_amounts(context):
    item = PropertyFile.objects.create(**context, total_price=100)
    item.transaction_type = "rent"
    with pytest.raises(ValidationError):
        item.save()
    item.total_price = None
    item.deposit_amount = 80
    item.monthly_rent = 0
    item.save()
    item.refresh_from_db()
    assert item.deposit_amount == 80
    assert item.monthly_rent == 0
    assert item.total_price is None
