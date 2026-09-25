import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.customers.models import Customer, CustomerRegionPreference
from apps.customers.services import create_customer, set_preferred_regions
from .test_models import context, regions, foreign, make_customer
from .test_api import world, body, client_for, url, BASE

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("field", ["min_area", "max_area", "bedrooms", "budget"])
def test_required_buyer_fields_model_database_api(context, world, field):
    with pytest.raises(ValidationError):
        make_customer(**context, **{field: None})
    customer = make_customer(**context)
    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.filter(pk=customer.pk).update(**{field: None})
    data = body()
    data.pop(field)
    client = client_for(world.consultant)
    assert client.post(BASE, data, format="json").status_code == 400
    assert client.patch(url(world.own), {field: None}, format="json").status_code == 400


@pytest.mark.parametrize("field", ["deposit_budget", "monthly_rent_budget"])
def test_required_tenant_fields(context, world, field):
    context["customer_type"] = "tenant"
    with pytest.raises(ValidationError):
        make_customer(**context, **{field: None})
    customer = make_customer(**context)
    assert customer.deposit_budget == customer.monthly_rent_budget == 0
    with pytest.raises(IntegrityError), transaction.atomic():
        Customer.objects.filter(pk=customer.pk).update(**{field: None})
    data = body(customer_type="tenant")
    data.pop(field)
    assert client_for(world.consultant).post(BASE, data, format="json").status_code == 400


def test_geography_required_without_silent_default(context, regions, world):
    assert Customer._meta.get_field("all_regions").default is False
    with pytest.raises(ValidationError):
        make_customer(**context, all_regions=False)
    with pytest.raises(ValidationError):
        make_customer(aggregate=True, **context, all_regions=True, preferred_regions=regions)
    data = body()
    data.pop("all_regions")
    client = client_for(world.consultant)
    assert client.post(BASE, data, format="json").status_code == 400
    assert client.post(BASE, body(all_regions=True, preferred_regions=[str(world.region.pk)]), format="json").status_code == 400
    response = client.post(BASE, body(), format="json")
    assert response.status_code == 201
    assert response.data["all_regions"] is True and response.data["preferred_regions"] == []
    assert all("all_regions" in row for row in client.get(BASE).data["results"])


def test_geography_transitions_and_atomic_failure(world):
    client = client_for(world.consultant)
    assert client.patch(url(world.own), {"all_regions": True, "preferred_regions": [str(world.region.pk)]}, format="json").status_code == 400
    response = client.patch(url(world.own), {"all_regions": True}, format="json")
    assert response.status_code == 200 and response.data["preferred_regions"] == []
    assert client.patch(url(world.own), {"all_regions": False, "description": "bad"}, format="json").status_code == 400
    world.own.refresh_from_db()
    assert world.own.all_regions and world.own.description != "bad"
    response = client.patch(url(world.own), {"all_regions": False, "preferred_regions": [str(world.other_region.pk)]}, format="json")
    assert response.status_code == 200 and not response.data["all_regions"]
    assert client.patch(url(world.own), {"all_regions": True, "valuable_reasons": ["duplicate", "duplicate"]}, format="json").status_code == 400
    world.own.refresh_from_db()
    assert not world.own.all_regions
    assert list(world.own.preferred_regions.all()) == [world.other_region]


@pytest.mark.parametrize("operation", ["remove", "clear", "queryset", "instance", "reverse_remove", "reverse_clear"])
def test_last_preference_cannot_be_deleted(context, regions, operation):
    customer = make_customer(aggregate=True, **context, preferred_regions=[regions[0]])
    with pytest.raises(ValidationError), transaction.atomic():
        if operation == "remove":
            customer.preferred_regions.remove(regions[0])
        elif operation == "clear":
            customer.preferred_regions.clear()
        elif operation == "queryset":
            CustomerRegionPreference.objects.filter(customer=customer).delete()
        elif operation == "instance":
            customer.region_preferences.get().delete()
        elif operation == "reverse_remove":
            regions[0].interested_customers.remove(customer)
        else:
            regions[0].interested_customers.clear()
    assert customer.preferred_regions.count() == 1
    set_preferred_regions(customer=customer, regions=[regions[1]])
    assert list(customer.preferred_regions.all()) == [regions[1]]


@pytest.mark.parametrize("operation", ["add", "reverse", "through", "service"])
def test_all_regions_rejects_explicit_links(context, regions, operation):
    customer = make_customer(**context)
    with pytest.raises(ValidationError), transaction.atomic():
        if operation == "add":
            customer.preferred_regions.add(regions[0])
        elif operation == "reverse":
            regions[0].interested_customers.add(customer)
        elif operation == "through":
            CustomerRegionPreference.objects.create(customer=customer, region=regions[0])
        else:
            set_preferred_regions(customer=customer, regions=regions)
    assert not customer.preferred_regions.exists()


def test_partial_save_validates_effective_geography_and_financials(context, regions):
    customer = make_customer(aggregate=True, **context, preferred_regions=[regions[0]])
    customer.all_regions = True
    customer.description = "kept"
    customer.save(update_fields=["description"])
    customer.refresh_from_db()
    assert not customer.all_regions and customer.preferred_regions.count() == 1
    customer.all_regions = True
    customer.save(update_fields=["all_regions"])
    assert not customer.preferred_regions.exists()
    customer.all_regions = False
    with pytest.raises(ValidationError):
        customer.save(update_fields=["all_regions"])
    customer.save(update_fields=["all_regions"], preferred_regions=[regions[1]])
    customer.customer_type = "tenant"
    customer.budget = None
    customer.deposit_budget = customer.monthly_rent_budget = 0
    with pytest.raises(ValidationError):
        customer.save(update_fields=["customer_type"])
    customer.refresh_from_db()
    assert customer.customer_type == "buyer"


def test_type_transitions_clear_incompatible_fields_and_rollback(world):
    client = client_for(world.consultant)
    client.patch(url(world.own), {"budget_status": "cash"}, format="json")
    for data in ({"customer_type": "tenant"}, {"customer_type": "tenant", "deposit_budget": 0}):
        assert client.patch(url(world.own), data, format="json").status_code == 400
    response = client.patch(url(world.own), {"customer_type": "tenant", "deposit_budget": 0, "monthly_rent_budget": 0}, format="json")
    assert response.status_code == 200 and response.data["budget"] is None and response.data["budget_status"] is None
    assert client.patch(url(world.own), {"customer_type": "buyer"}, format="json").status_code == 400
    response = client.patch(url(world.own), {"customer_type": "buyer", "budget": 0}, format="json")
    assert response.status_code == 200 and response.data["deposit_budget"] is None and response.data["monthly_rent_budget"] is None
    assert response.data["budget_status"] is None


def test_last_link_cannot_move_to_another_customer(context, regions):
    first = make_customer(aggregate=True, **context, preferred_regions=[regions[0]])
    second = make_customer(aggregate=True, **context, preferred_regions=[regions[1]])
    link = first.region_preferences.get()
    link.customer = second
    with pytest.raises(ValidationError):
        link.save()
    assert first.region_preferences.count() == 1


def test_all_regions_has_no_physical_backfill_or_foreign_links(context, regions, foreign):
    customer = make_customer(**context)
    regions[0].is_active = False
    regions[0].save()
    customer.description = "geography follows workspace active taxonomy"
    customer.save()
    assert customer.all_regions and customer.workspace != foreign[0]
    assert not customer.preferred_regions.exists()


def test_multirow_deletion_and_direct_move_preserve_minimum(context, regions):
    first = make_customer(aggregate=True, **context, preferred_regions=regions)
    second = make_customer(aggregate=True, **context, preferred_regions=[regions[1]])
    with pytest.raises(ValidationError):
        first.region_preferences.all().delete()
    assert first.region_preferences.count() == 2
    link = first.region_preferences.get(region=regions[0])
    link.customer = second
    link.save(update_fields=["customer"])
    assert first.region_preferences.count() == 1 and second.region_preferences.count() == 2


def test_aggregate_failure_rolls_back_financial_assignment_and_geography(world):
    response = client_for(world.agency).patch(url(world.own), {
        "assigned_to": str(world.second.pk), "customer_type": "tenant",
        "deposit_budget": 0, "monthly_rent_budget": 0, "all_regions": True,
        "valuable_reasons": ["duplicate", "duplicate"], "name": "changed",
    }, format="json")
    assert response.status_code == 400
    world.own.refresh_from_db()
    assert world.own.assigned_to_id == world.consultant.pk
    assert world.own.customer_type == "buyer" and world.own.budget == 0
    assert world.own.deposit_budget is None and world.own.monthly_rent_budget is None
    assert not world.own.all_regions and world.own.name != "changed"
    assert list(world.own.preferred_regions.all()) == [world.region]
    assert world.own.valuable_reasons.count() == 1
