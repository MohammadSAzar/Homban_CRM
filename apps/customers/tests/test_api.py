import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.locations.models import City, Region
from apps.organizations.models import Workspace
from apps.ranges.models import Range, RangeMembership
from apps.customers.api_services import save_customer
from apps.customers.models import Customer, CustomerRegionPreference
from apps.customers.services import create_customer

pytestmark = pytest.mark.django_db
BASE = "/api/v1/customers/"


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def url(item):
    return BASE + str(item.pk) + "/"


@pytest.fixture
def world():
    workspace = Workspace.objects.create(name="اول", slug="customers-first", customer_type="agency_manager")
    foreign_workspace = Workspace.objects.create(name="دوم", slug="customers-other", customer_type="consultant")
    def user(name, role="consultant", ws=workspace):
        return User.objects.create_user(username=name, workspace=ws, role=role)
    agency = user("agency", "agency_manager")
    manager = user("manager", "range_manager")
    consultant = user("consultant")
    second = user("second")
    unrelated = user("unrelated")
    foreign_user = user("foreign", ws=foreign_workspace)
    team = Range.objects.create(workspace=workspace, name="اول", manager=manager)
    other_team = Range.objects.create(workspace=workspace, name="دوم", manager=manager)
    RangeMembership.objects.create(workspace=workspace, range=team, user=consultant)
    RangeMembership.objects.create(workspace=workspace, range=other_team, user=second)
    city = City.objects.create(workspace=workspace, name="تهران")
    region = Region.objects.create(workspace=workspace, city=city, name="ونک")
    other_region = Region.objects.create(workspace=workspace, city=city, name="ولیعصر")
    foreign_city = City.objects.create(workspace=foreign_workspace, name="تهران")
    foreign_region = Region.objects.create(workspace=foreign_workspace, city=foreign_city, name="ونک")
    def customer(assignee, ws=workspace, location=region):
        return create_customer(workspace=ws, assigned_to=assignee, customer_type="buyer",
            name="نام محرمانه", mobile="09121111111", description="یادداشت محرمانه",
            preferred_regions=[location], valuable_reasons=["آماده خرید"])
    own = customer(consultant)
    managed = customer(second)
    outside = customer(unrelated)
    foreign = customer(foreign_user, foreign_workspace, foreign_region)
    return SimpleNamespace(**locals())


def body(**changes):
    return {"customer_type": "buyer", "name": "مشتری", **changes}


@pytest.mark.parametrize("actor", ["agency", "manager", "consultant"])
def test_create_full_details(world, actor):
    data = body(mobile="09123333333", description="توضیح", budget="1000.50",
        budget_status="cash_plus_property", min_area="50.25", max_area="120.50",
        preferred_regions=[str(world.region.pk), str(world.other_region.pk)],
        valuable_reasons=["آماده خرید", "بودجه مناسب"])
    if actor != "consultant":
        data["assigned_to"] = str(world.consultant.pk)
    response = client_for(getattr(world, actor)).post(BASE, data, format="json")
    assert response.status_code == 201, response.data
    row = response.data
    assert row["name"] == "مشتری" and row["mobile"] == data["mobile"] and row["description"] == "توضیح"
    assert row["budget"] == "1000.50" and row["budget_status_display"] == "بخشی نقد + آپارتمان"
    assert {r["id"] for r in row["preferred_regions"]} == {str(world.region.pk), str(world.other_region.pk)}
    assert set(row["valuable_reasons"]) == set(data["valuable_reasons"])
    assert set(row["assigned_to"]) == {"id", "username", "first_name", "last_name"}
    assert row["assigned_to"]["id"] == str(world.consultant.pk)
    assert not {"password", "groups", "user_permissions", "is_superuser", "workspace"} & set(row)
    item = Customer.objects.get(pk=row["id"])
    assert item.workspace == world.workspace and item.code.startswith("CU-")


@pytest.mark.parametrize("actor", ["agency", "manager"])
def test_managers_require_explicit_active_eligible_assignee(world, actor):
    client = client_for(getattr(world, actor))
    assert client.post(BASE, body(), format="json").status_code == 400
    for target in (world.agency, world.manager, world.foreign_user):
        assert client.post(BASE, body(assigned_to=str(target.pk)), format="json").status_code == 400
        assert client.patch(url(world.own), {"assigned_to": str(target.pk)}, format="json").status_code == 400
    world.consultant.is_active = False
    world.consultant.save()
    assert client.post(BASE, body(assigned_to=str(world.consultant.pk)), format="json").status_code == 400
    assert client.patch(url(world.own), {"assigned_to": str(world.consultant.pk)}, format="json").status_code == 400
    assert client.patch(url(world.own), {"description": "تاریخی"}, format="json").status_code == 200


def test_consultant_self_assignment_only(world):
    client = client_for(world.consultant)
    for target in (world.second, world.foreign_user):
        assert client.post(BASE, body(assigned_to=str(target.pk)), format="json").status_code == 400
        assert client.patch(url(world.own), {"assigned_to": str(target.pk)}, format="json").status_code == 400
    assert client.post(BASE, body(assigned_to=str(world.consultant.pk)), format="json").status_code == 201


def test_range_union_zero_scope_and_reassignment(world):
    client = client_for(world.manager)
    assert client.get(BASE).data["count"] == 2
    for target in (world.consultant, world.second):
        assert client.post(BASE, body(assigned_to=str(target.pk)), format="json").status_code == 201
    assert client.post(BASE, body(assigned_to=str(world.unrelated.pk)), format="json").status_code == 400
    assert client.patch(url(world.own), {"assigned_to": str(world.second.pk)}, format="json").status_code == 200
    assert client_for(world.consultant).get(url(world.own)).status_code == 404
    assert client.patch(url(world.own), {"assigned_to": str(world.unrelated.pk)}, format="json").status_code == 400
    world.team.is_active = False
    world.team.save()
    assert client.post(BASE, body(assigned_to=str(world.consultant.pk)), format="json").status_code == 400
    assert client.get(url(world.managed)).status_code == 200
    world.other_team.is_active = False
    world.other_team.save()
    assert client.get(BASE).data["count"] == 0
    assert client.get(url(world.managed)).status_code == 404
    assert client.post(BASE, body(assigned_to=str(world.second.pk)), format="json").status_code == 400


def test_agency_reassignment_revokes_range_scope(world):
    response = client_for(world.agency).patch(url(world.own), {"assigned_to": str(world.unrelated.pk)}, format="json")
    assert response.status_code == 200
    assert client_for(world.manager).get(url(world.own)).status_code == 404
    assert client_for(world.unrelated).get(url(world.own)).status_code == 200


@pytest.mark.parametrize("actor,count", [("agency", 3), ("manager", 2), ("consultant", 1)])
def test_list_and_detail_scope(world, actor, count):
    client = client_for(getattr(world, actor))
    response = client.get(BASE, {"workspace": str(world.foreign_workspace.pk)})
    assert response.status_code == 200 and response.data["count"] == count
    for row in response.data["results"]:
        assert not {"mobile", "description", "preferred_regions", "valuable_reasons", "password", "groups"} & set(row)
    detail = client.get(url(world.own)).data
    assert detail["name"] == world.own.name and detail["mobile"] == world.own.mobile
    assert detail["description"] == world.own.description
    assert client.get(url(world.foreign)).status_code == 404
    assert client.patch(url(world.foreign), {"status": "inactive"}, format="json").status_code == 404


@pytest.mark.parametrize("actor", ["manager", "consultant"])
def test_no_cross_owner_contact_or_mutation(world, actor):
    client = client_for(getattr(world, actor))
    for item in (world.outside, world.foreign):
        for response in (
            client.get(url(item)),
            client.patch(url(item), {"name": "تغییر", "preferred_regions": [], "valuable_reasons": []}, format="json"),
        ):
            assert response.status_code == 404
            assert not any(secret in str(response.data) for secret in (item.name, item.mobile, item.description))
    assert client.get(url(world.foreign)).data == client.get(BASE + str(uuid.uuid4()) + "/").data


@pytest.mark.parametrize("role", ["secretary", "admin"])
@pytest.mark.parametrize("owner", [False, True])
def test_denied_roles_and_internal_privileges(world, role, owner):
    actor = User.objects.create_user(username=role, workspace=world.workspace, role=role,
        is_workspace_owner=owner, is_staff=True, is_superuser=True)
    client = client_for(actor)
    assert client.get(BASE).status_code == 403
    assert client.get(url(world.own)).status_code == 403
    assert client.post(BASE, body(), format="json").status_code == 403
    assert client.patch(url(world.own), {"status": "inactive"}, format="json").status_code == 403


@pytest.mark.parametrize("actor", ["consultant", "manager"])
def test_ownership_does_not_expand_scope(world, actor):
    user = getattr(world, actor)
    user.is_workspace_owner = True
    user.save()
    assert client_for(user).get(url(world.outside)).status_code == 404


@pytest.mark.parametrize("field,value", [
    ("workspace", "fake"), ("workspace_id", "fake"), ("code", "fake"), ("id", "fake"),
    ("created_at", "2020-01-01"), ("updated_at", "2020-01-01"), ("assigned_to_id", "fake"),
    ("role", "agency_manager"), ("is_workspace_owner", True), ("is_superuser", True),
])
def test_immutable_and_privilege_fields(world, field, value):
    client = client_for(world.consultant)
    assert client.post(BASE, body(**{field: value}), format="json").status_code == 400
    assert client.patch(url(world.own), {field: value}, format="json").status_code == 400


@pytest.mark.parametrize("changes", [
    {"min_area": -1}, {"max_area": -1}, {"min_area": 100, "max_area": 50},
    {"min_building_age": -1}, {"max_building_age": -1}, {"min_building_age": 10, "max_building_age": 5},
    {"bedrooms": -1}, {"budget": -1}, {"deposit_budget": 0}, {"monthly_rent_budget": 0},
    {"budget_status": "bad"}, {"customer_type": "bad"}, {"status": "bad"}, {"name": ""},
    {"customer_type": "tenant", "budget": 0}, {"customer_type": "tenant", "budget_status": "cash"},
    {"customer_type": "tenant", "deposit_budget": -1}, {"customer_type": "tenant", "monthly_rent_budget": -1},
])
def test_domain_validation(world, changes):
    response = client_for(world.consultant).post(BASE, body(**changes), format="json")
    assert response.status_code == 400, response.data


@pytest.mark.parametrize("value,label", [(None, None), ("", None), ("cash", "کاملاً نقد"), ("cash_plus_property", "بخشی نقد + آپارتمان")])
def test_budget_status_choices_and_nullable_default(world, value, label):
    client = client_for(world.consultant)
    response = client.post(BASE, body(budget_status=value), format="json")
    assert response.status_code == 201, response.data
    assert response.data["budget_status_display"] == label
    assert client.post(BASE, body(), format="json").data["budget_status"] is None


def test_partial_validation_and_type_transition(world):
    client = client_for(world.consultant)
    assert client.patch(url(world.own), {"budget": 100, "budget_status": "cash", "min_area": 50, "max_area": 100}, format="json").status_code == 200
    assert client.patch(url(world.own), {"min_area": 101}, format="json").status_code == 400
    assert client.patch(url(world.own), {"customer_type": "tenant"}, format="json").status_code == 400
    response = client.patch(url(world.own), {"customer_type": "tenant", "budget": None, "budget_status": None,
        "deposit_budget": "100.50", "monthly_rent_budget": "20.25"}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["deposit_budget"] == "100.50" and response.data["monthly_rent_budget"] == "20.25"


@pytest.mark.parametrize("status", Customer.Status.values)
def test_status_and_no_hard_delete(world, status):
    client = client_for(world.consultant)
    assert client.patch(url(world.own), {"status": status}, format="json").status_code == 200
    assert client.get(url(world.own)).status_code == 200
    assert client.delete(url(world.own)).status_code == 405
    assert client.put(url(world.own), {}, format="json").status_code == 405
    assert world.own.region_preferences.count() == 1


def test_region_replacement_inactive_retention_and_removal(world):
    client = client_for(world.consultant)
    world.region.is_active = False
    world.region.save()
    response = client.patch(url(world.own), {"preferred_regions": [str(world.region.pk), str(world.other_region.pk)]}, format="json")
    assert response.status_code == 200, response.data
    assert any(not r["is_active"] for r in response.data["preferred_regions"])
    assert client.patch(url(world.own), {"description": "توضیح"}, format="json").status_code == 200
    assert world.own.preferred_regions.count() == 2
    assert client.patch(url(world.own), {"preferred_regions": [str(world.other_region.pk)]}, format="json").status_code == 200
    assert client.patch(url(world.own), {"preferred_regions": [str(world.region.pk)]}, format="json").status_code == 400
    assert client.patch(url(world.own), {"preferred_regions": []}, format="json").status_code == 200
    assert world.own.preferred_regions.count() == 0


@pytest.mark.parametrize("invalid", ["foreign", "inactive", "missing"])
def test_invalid_regions_and_aggregate_rollback(world, invalid):
    client = client_for(world.consultant)
    world.other_region.is_active = False
    world.other_region.save()
    target = {"foreign": world.foreign_region.pk, "inactive": world.other_region.pk, "missing": uuid.uuid4()}[invalid]
    count = Customer.objects.count()
    assert client.post(BASE, body(preferred_regions=[str(target)]), format="json").status_code == 400
    assert Customer.objects.count() == count
    response = client.patch(url(world.own), {"name": "rollback", "preferred_regions": [str(target)], "valuable_reasons": []}, format="json")
    assert response.status_code == 400
    world.own.refresh_from_db()
    assert world.own.name != "rollback"
    assert list(world.own.preferred_regions.all()) == [world.region]
    assert world.own.valuable_reasons.count() == 1


def test_reasons_rollback_includes_preference_changes(world):
    client = client_for(world.consultant)
    response = client.patch(url(world.own), {"valuable_reasons": ["یک", "دو"], "is_valuable": True}, format="json")
    assert response.status_code == 200 and set(response.data["valuable_reasons"]) == {"یک", "دو"}
    response = client.patch(url(world.own), {"description": "rollback", "preferred_regions": [str(world.other_region.pk)],
        "valuable_reasons": ["تکراری", "تکراری"]}, format="json")
    assert response.status_code == 400
    world.own.refresh_from_db()
    assert world.own.description != "rollback"
    assert list(world.own.preferred_regions.all()) == [world.region]
    assert set(world.own.valuable_reasons.values_list("reason", flat=True)) == {"یک", "دو"}
    count = Customer.objects.count()
    assert client.post(BASE, body(valuable_reasons=["تکراری", "تکراری"]), format="json").status_code == 400
    assert Customer.objects.count() == count
    assert client.patch(url(world.own), {"valuable_reasons": []}, format="json").status_code == 200
    assert world.own.valuable_reasons.count() == 0 and world.own.is_valuable


@pytest.mark.parametrize("params", [
    {"customer_type": "buyer"}, {"status": "archived"}, {"min_area": 99}, {"max_area": 121},
    {"bedrooms": 3}, {"min_budget": 999}, {"max_budget": 1001},
    {"budget_status": "cash"}, {"is_valuable": "true"},
])
def test_buyer_filters(world, params):
    for field, value in dict(status="archived", min_area=100, max_area=120, bedrooms=3, budget=1000, budget_status="cash", is_valuable=True).items():
        setattr(world.own, field, value)
    world.own.save()
    world.managed.customer_type = "tenant"
    world.managed.min_area = 50
    world.managed.max_area = 200
    world.managed.save()
    response = client_for(world.manager).get(BASE, params)
    assert response.status_code == 200, response.data
    assert [r["id"] for r in response.data["results"]] == [str(world.own.pk)]


@pytest.mark.parametrize("params", [
    {"min_deposit_budget": 99}, {"max_deposit_budget": 101},
    {"min_monthly_rent_budget": 19}, {"max_monthly_rent_budget": 21},
])
def test_tenant_financial_filters(world, params):
    world.own.customer_type = "tenant"
    world.own.deposit_budget = 100
    world.own.monthly_rent_budget = 20
    world.own.save()
    response = client_for(world.manager).get(BASE, params)
    assert response.status_code == 200
    assert [r["id"] for r in response.data["results"]] == [str(world.own.pk)]


def test_uuid_filters_and_validation(world):
    world.own.preferred_regions.add(world.other_region)
    client = client_for(world.agency)
    for key, target in (("assigned_to", world.consultant), ("preferred_region", world.other_region)):
        assert client.get(BASE, {key: str(target.pk)}).data["count"] == 1
    for key, target in (("assigned_to", world.foreign_user), ("preferred_region", world.foreign_region)):
        assert client.get(BASE, {key: str(target.pk)}).data["count"] == 0
    for params in ({"assigned_to": "bad"}, {"preferred_region": "bad"}, {"min_budget": "bad"}, {"status": "bad"}):
        assert client.get(BASE, params).status_code == 400


@pytest.mark.parametrize("actor", ["agency", "manager", "consultant"])
def test_queries_do_not_grow(world, actor):
    client = client_for(getattr(world, actor))
    for _ in range(2):
        with CaptureQueriesContext(connection) as queries:
            response = client.get(BASE)
        assert response.status_code == 200 and len(queries) == 3
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url(world.own))
        assert response.status_code == 200 and len(queries) == 4
        create_customer(workspace=world.workspace, assigned_to=world.consultant, customer_type="buyer", name="جدید",
            preferred_regions=[world.region, world.other_region], valuable_reasons=["یک", "دو"])
        world.own.preferred_regions.add(world.other_region)


def test_anonymous_and_database_auth_state(world):
    assert APIClient().get(BASE).status_code == 401
    client = client_for(world.consultant)
    world.consultant.is_active = False
    world.consultant.save()
    assert client.get(BASE).status_code == 401
    world.consultant.is_active = True
    world.consultant.role = "admin"
    world.consultant.save()
    assert client.get(BASE).status_code == 403


@pytest.mark.parametrize("state", ["inactive_workspace", "workspace_less"])
def test_workspace_auth_state(world, state):
    client = client_for(world.consultant)
    if state == "inactive_workspace":
        world.workspace.is_active = False
        world.workspace.save()
    else:
        world.consultant.workspace = None
        world.consultant.save()
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, body(), format="json").status_code == 401


def test_services_recheck_actor_and_payload(world):
    User.objects.filter(pk=world.agency.pk).update(role="secretary")
    with pytest.raises(PermissionDenied):
        save_customer(actor=world.agency, data={"name": "x"}, customer_id=world.own.pk)
    with pytest.raises(ValidationError):
        save_customer(actor=world.consultant, data={"workspace": world.foreign_workspace.pk}, customer_id=world.own.pk)


def test_membership_change_revokes_scope(world):
    client = client_for(world.manager)
    team = Range.objects.create(workspace=world.workspace, name="بدون مدیر")
    membership = world.consultant.range_membership
    membership.range = team
    membership.save()
    assert client.get(url(world.own)).status_code == 404
    assert client.patch(url(world.own), {"status": "inactive"}, format="json").status_code == 404
    assert client.post(BASE, body(assigned_to=str(world.consultant.pk)), format="json").status_code == 400


def test_invalid_legacy_relationships_do_not_leak(world):
    # Simulate unsupported raw/bulk corruption; read boundaries still fail closed.
    Customer.objects.filter(pk=world.own.pk).update(assigned_to=world.foreign_user)
    assert client_for(world.agency).get(url(world.own)).status_code == 404
    CustomerRegionPreference.objects.bulk_create([CustomerRegionPreference(customer=world.managed, region=world.foreign_region)])
    data = client_for(world.agency).get(url(world.managed)).data
    assert str(world.foreign_region.pk) not in str(data)


def test_pagination(world):
    for _ in range(50):
        Customer.objects.create(workspace=world.workspace, assigned_to=world.consultant, customer_type="buyer", name="مشتری")
    client = client_for(world.consultant)
    first = client.get(BASE).data
    second = client.get(BASE, {"page": 2}).data
    assert first["count"] == 51 and len(first["results"]) == 50 and len(second["results"]) == 1
    assert not {r["id"] for r in first["results"]} & {r["id"] for r in second["results"]}
