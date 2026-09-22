import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.locations.models import City, Region
from apps.customers.models import Customer, CustomerRegionPreference, CustomerValuableReason
from apps.customers.services import create_customer, set_preferred_regions

pytestmark = pytest.mark.django_db


@pytest.fixture
def context():
    workspace = Workspace.objects.create(name="مجموعه", slug="customer", customer_type="consultant")
    user = User.objects.create_user(username="ali", workspace=workspace, role="consultant")
    return dict(workspace=workspace, assigned_to=user, customer_type="buyer", name="مشتری")


@pytest.fixture
def regions(context):
    city = City.objects.create(workspace=context["workspace"], name="تهران")
    return [Region.objects.create(workspace=context["workspace"], city=city, name=name) for name in ("ونک", "سعادت آباد")]


@pytest.fixture
def foreign():
    workspace = Workspace.objects.create(name="دیگر", slug="other", customer_type="consultant")
    user = User.objects.create_user(username="ali", workspace=workspace, role="consultant")
    city = City.objects.create(workspace=workspace, name="تهران")
    region = Region.objects.create(workspace=workspace, city=city, name="ونک")
    return workspace, user, region


@pytest.mark.parametrize("kind,amounts", [
    ("buyer", {"budget": Decimal("12000000000.25"), "budget_status": "cash"}),
    ("tenant", {"deposit_budget": Decimal("500000000.00"), "monthly_rent_budget": Decimal("20000000.50")}),
])
def test_valid_customer(context, kind, amounts):
    context["customer_type"] = kind
    customer = create_customer(**context, **amounts, mobile="09121111111", description="یادداشت مشتری")
    customer.refresh_from_db()
    assert customer.assigned_to == context["assigned_to"]
    assert customer.name == "مشتری"
    assert customer.mobile == "09121111111"
    assert customer.description == "یادداشت مشتری"
    assert customer.status == "active"
    assert timezone.is_aware(customer.created_at)
    assert timezone.is_aware(customer.updated_at)
    for field, value in amounts.items():
        assert getattr(customer, field) == value
    assert customer.preferred_regions.count() == 0


@pytest.mark.parametrize("status,label", [(None, None), ("", None), ("cash", "کاملاً نقد"), ("cash_plus_property", "بخشی نقد + آپارتمان")])
def test_budget_status(context, status, label):
    customer = Customer.objects.create(**context, budget_status=status)
    customer.refresh_from_db()
    assert customer.budget_status == (status or None)
    if label:
        assert customer.get_budget_status_display() == label


def test_unspecified_requirements(context):
    customer = Customer.objects.create(**context)
    for field in ("budget_status", "budget", "min_area", "max_area", "min_building_age", "max_building_age", "bedrooms", "deposit_budget", "monthly_rent_budget"):
        assert getattr(customer, field) is None


@pytest.mark.parametrize("role", ["agency_manager", "range_manager", "secretary", "admin"])
def test_wrong_assignee_role(context, role):
    context["assigned_to"].role = role
    context["assigned_to"].save()
    with pytest.raises(ValidationError):
        Customer.objects.create(**context)


def test_foreign_missing_workspace_less_assignee(context, foreign):
    context["assigned_to"] = foreign[1]
    with pytest.raises(ValidationError):
        Customer.objects.create(**context)
    foreign[1].workspace = None
    foreign[1].save()
    with pytest.raises(ValidationError):
        Customer.objects.create(**context)
    context.pop("assigned_to")
    with pytest.raises(ValidationError):
        Customer.objects.create(**context, assigned_to_id=uuid.uuid4())


@pytest.mark.parametrize("kind,fields", [
    ("buyer", {"deposit_budget": 0}), ("buyer", {"monthly_rent_budget": 0}),
    ("tenant", {"budget": 0}), ("tenant", {"budget_status": "cash"}),
    ("tenant", {"budget_status": "cash_plus_property"}),
])
def test_incompatible_fields_model_and_database(context, kind, fields):
    context["customer_type"] = kind
    with pytest.raises(ValidationError):
        Customer.objects.create(**context, **fields)
    customer = Customer.objects.create(**context)
    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.filter(pk=customer.pk).update(**fields)


NUMBERS = ["min_area", "max_area", "min_building_age", "max_building_age", "bedrooms", "budget", "deposit_budget", "monthly_rent_budget"]


@pytest.mark.parametrize("field", NUMBERS)
def test_negative_model_and_database(context, field):
    if field in ("deposit_budget", "monthly_rent_budget"):
        context["customer_type"] = "tenant"
    with pytest.raises(ValidationError):
        Customer.objects.create(**context, **{field: -1})
    customer = Customer.objects.create(**context)
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        Customer.objects.filter(pk=customer.pk).update(**{field: -1})


@pytest.mark.parametrize("minimum,maximum", [("min_area", "max_area"), ("min_building_age", "max_building_age")])
def test_invalid_bounds_model_and_database(context, minimum, maximum):
    with pytest.raises(ValidationError):
        Customer.objects.create(**context, **{minimum: 10, maximum: 5})
    customer = Customer.objects.create(**context, **{minimum: 0, maximum: 0})
    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.filter(pk=customer.pk).update(**{minimum: 10, maximum: 5})


@pytest.mark.parametrize("fields", [{"min_area": 10}, {"max_area": 10}, {"min_building_age": 0}, {"max_building_age": 0}, {"bedrooms": 0}])
def test_one_sided_requirements(context, fields):
    Customer.objects.create(**context, **fields)


@pytest.mark.parametrize("fields", [{"customer_type": "bad"}, {"status": "bad"}, {"budget_status": "bad"}])
def test_choice_validation(context, fields):
    data = {**context, **fields}
    with pytest.raises(ValidationError):
        Customer.objects.create(**data)
    customer = Customer.objects.create(**context)
    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.filter(pk=customer.pk).update(**fields)


def test_multiple_regions_and_inactive_retention(context, regions):
    customer = create_customer(**context, preferred_regions=regions)
    assert set(customer.preferred_regions.all()) == set(regions)
    regions[0].is_active = False
    regions[0].save()
    set_preferred_regions(customer=customer, regions=regions)
    customer.preferred_regions.add(regions[0])
    assert customer.preferred_regions.count() == 2
    customer.region_preferences.get(region=regions[0]).save()
    set_preferred_regions(customer=customer, regions=[regions[1]])
    with pytest.raises(ValidationError), transaction.atomic():
        customer.preferred_regions.add(regions[0])
    customer.preferred_regions.clear()
    assert not customer.preferred_regions.exists()


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("invalid", ["foreign", "inactive", "missing"])
def test_invalid_m2m(context, regions, foreign, reverse, invalid):
    customer = Customer.objects.create(**context)
    region = foreign[2] if invalid == "foreign" else regions[0]
    if invalid == "inactive":
        region.is_active = False
        region.save()
    with pytest.raises(ValidationError), transaction.atomic():
        if reverse:
            region.interested_customers.add(uuid.uuid4() if invalid == "missing" else customer)
        else:
            customer.preferred_regions.add(uuid.uuid4() if invalid == "missing" else region)
    assert not CustomerRegionPreference.objects.exists()


def test_through_model_and_uniqueness(context, regions, foreign):
    customer = Customer.objects.create(**context)
    with pytest.raises(ValidationError):
        CustomerRegionPreference.objects.create(customer=customer, region=foreign[2])
    regions[0].is_active = False
    regions[0].save()
    with pytest.raises(ValidationError):
        CustomerRegionPreference.objects.create(customer=customer, region=regions[0])
    link = CustomerRegionPreference.objects.create(customer=customer, region=regions[1])
    with pytest.raises(ValidationError):
        CustomerRegionPreference.objects.create(customer=customer, region=regions[1])
    with pytest.raises(IntegrityError), transaction.atomic():
        CustomerRegionPreference.objects.bulk_create([CustomerRegionPreference(customer=customer, region=regions[1])])
    link.region = foreign[2]
    with pytest.raises(ValidationError):
        link.save()


def test_set_failure_preserves_previous_links(context, regions, foreign):
    customer = create_customer(**context, preferred_regions=regions)
    with pytest.raises(ValidationError):
        set_preferred_regions(customer=customer, regions=[foreign[2]])
    assert set(customer.preferred_regions.all()) == set(regions)


@pytest.mark.parametrize("failure", ["region", "reason"])
def test_create_atomic(context, foreign, failure):
    kwargs = {"preferred_regions": [foreign[2]]} if failure == "region" else {"valuable_reasons": ["جدی", "جدی"]}
    with pytest.raises(ValidationError):
        create_customer(**context, **kwargs)
    assert not Customer.objects.exists()
    assert not CustomerValuableReason.objects.exists()
    assert not CustomerRegionPreference.objects.exists()


@pytest.mark.parametrize("status", Customer.Status.values)
def test_status_preserves_relations(context, regions, status):
    customer = create_customer(**context, preferred_regions=regions, is_valuable=True, valuable_reasons=["آماده معامله", "پیگیری منظم"])
    customer.status = status
    customer.save()
    customer.refresh_from_db()
    assert customer.status == status
    assert customer.is_valuable
    assert customer.valuable_reasons.count() == 2
    assert customer.preferred_regions.count() == 2


def test_reason_unique_and_optional(context):
    customer = Customer.objects.create(**context, is_valuable=True)
    assert not customer.valuable_reasons.exists()
    CustomerValuableReason.objects.create(customer=customer, reason="آماده معامله")
    with pytest.raises(ValidationError):
        CustomerValuableReason.objects.create(customer=customer, reason="آماده معامله")
    with pytest.raises(IntegrityError), transaction.atomic():
        CustomerValuableReason.objects.bulk_create([CustomerValuableReason(customer=customer, reason="آماده معامله")])


def test_code_unique_stable(context):
    first, second = [Customer.objects.create(**context) for _ in range(2)]
    code = first.code
    assert code == f"CU-{first.pk.hex.upper()}" != second.code
    first.description = "ویرایش"
    first.save()
    assert Customer.objects.get(code=code).pk == first.pk
    assert str(first) == code
    first.code = second.code
    with pytest.raises(ValidationError):
        first.save()
    with pytest.raises(ValidationError):
        Customer.objects.create(**context, code="supplied")
    for bad_code in (second.code, ""):
        with pytest.raises(IntegrityError), transaction.atomic():
            Customer.objects.filter(pk=first.pk).update(code=bad_code)


def test_workspace_immutable(context, foreign):
    customer = Customer.objects.create(**context)
    customer.workspace, customer.assigned_to = foreign[:2]
    with pytest.raises(ValidationError):
        customer.save()


@pytest.mark.parametrize("target", ["workspace", "assigned_to", "region", "city"])
def test_history_protected(context, regions, target):
    customer = create_customer(**context, preferred_regions=regions)
    obj = context[target] if target in context else (regions[0] if target == "region" else regions[0].city)
    with pytest.raises(ProtectedError):
        obj.delete()
    assert Customer.objects.filter(pk=customer.pk).exists()
    assert customer.preferred_regions.count() == 2


@pytest.mark.parametrize("field", ["region", "region_id", "customer", "customer_id"])
def test_partial_preference_save_validates_the_stored_customer(context, regions, foreign, field):
    customer = Customer.objects.create(**context)
    other = Customer.objects.create(workspace=foreign[0], assigned_to=foreign[1], customer_type="buyer", name="دیگر")
    link = CustomerRegionPreference.objects.create(customer=customer, region=regions[0])
    link.customer, link.region = other, foreign[2]
    with pytest.raises(ValidationError):
        link.save(update_fields=[field])
    link.refresh_from_db()
    assert link.customer_id == customer.pk
    assert link.region_id == regions[0].pk
    link.region = regions[1]
    link.save(update_fields=["region"])
    link.refresh_from_db()
    assert link.region_id == regions[1].pk
