import uuid
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.locations.models import City, Region
from apps.locations.services import save_city, save_region, set_region_mode
from apps.organizations.models import Workspace

pytestmark = pytest.mark.django_db
BASE = "/api/v1/"
MODE = BASE + "location-settings/"
ROLES = User.Role.values


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def endpoint(kind, obj=None):
    return BASE + kind + "/" + (str(obj.pk) + "/" if obj else "")


@pytest.fixture
def world():
    workspace = Workspace.objects.create(name="اول", slug="first", customer_type="consultant")
    foreign_workspace = Workspace.objects.create(name="دوم", slug="second", customer_type="agency_manager")
    actor = User.objects.create_user(username="manager", workspace=workspace, role="agency_manager")
    city = City.objects.create(workspace=workspace, name="تهران")
    other_city = City.objects.create(workspace=workspace, name="شیراز")
    foreign_city = City.objects.create(workspace=foreign_workspace, name="تهران")
    region = Region.objects.create(workspace=workspace, city=city, name="مرکز")
    foreign_region = Region.objects.create(workspace=foreign_workspace, city=foreign_city, name="مرکز")
    return SimpleNamespace(**locals())


def body(world, kind, **overrides):
    data = {"name": "جدید"}
    if kind == "regions":
        data["city"] = str(world.city.pk)
    return {**data, **overrides}


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("owner", [False, True])
def test_location_management_role_and_owner_matrix(world, role, owner):
    User.objects.filter(pk=world.actor.pk).update(role=role, is_workspace_owner=owner)
    client = client_for(world.actor)
    allowed = role == "agency_manager" or owner
    for kind, obj in [("cities", world.city), ("regions", world.region)]:
        assert client.get(endpoint(kind)).status_code == 200
        assert str(obj.pk) in {row["id"] for row in client.get(endpoint(kind)).data["results"]}
        assert client.get(endpoint(kind, obj)).status_code == 200
        response = client.post(endpoint(kind), body(world, kind), format="json")
        assert response.status_code == (201 if allowed else 403), response.data
        response = client.patch(endpoint(kind, obj), {"is_active": False}, format="json")
        assert response.status_code == (200 if allowed else 403)
    assert client.get(MODE).status_code == 200
    assert client.patch(MODE, {"region_mode": "custom"}, format="json").status_code == (200 if allowed else 403)


@pytest.mark.parametrize("mode", Workspace.RegionMode.values)
def test_mode_switch_preserves_data_and_manual_management(world, mode):
    client = client_for(world.actor)
    response = client.patch(MODE, {"region_mode": mode}, format="json")
    assert response.status_code == 200
    assert response.data == {"region_mode": mode, "region_mode_display": dict(Workspace.RegionMode.choices)[mode]}
    assert City.objects.filter(pk=world.city.pk).exists()
    assert Region.objects.filter(pk=world.region.pk).exists()
    assert client.post(endpoint("cities"), {"name": "کرمان"}, format="json").status_code == 201
    assert client.post(endpoint("regions"), body(world, "regions"), format="json").status_code == 201
    assert client.get(endpoint("regions", world.region)).status_code == 200


@pytest.mark.parametrize("data", [{}, {"region_mode": "none"}, {"region_mode": ""}, {"region_mode": "invalid"}, {"region_mode": None}, {"workspace_id": "x", "region_mode": "custom"}])
def test_mode_invalid_payload_rejected(world, data):
    assert client_for(world.actor).patch(MODE, data, format="json").status_code == 400
    world.workspace.refresh_from_db()
    assert world.workspace.region_mode is None


def test_mode_server_workspace_ignores_query_and_header(world):
    client = client_for(world.actor)
    response = client.patch(MODE + "?workspace=" + str(world.foreign_workspace.pk), {"region_mode": "custom"},
                            format="json", HTTP_X_WORKSPACE_SLUG=world.foreign_workspace.slug)
    assert response.status_code == 200
    world.foreign_workspace.refresh_from_db()
    assert world.foreign_workspace.region_mode is None


def test_unconfigured_mode_is_readable_and_configured_mode_cannot_be_cleared(world):
    client = client_for(world.actor)
    assert client.get(MODE).data == {"region_mode": None, "region_mode_display": None}
    assert client.patch(MODE, {"region_mode": "custom"}, format="json").status_code == 200
    assert client.patch(MODE, {"region_mode": None}, format="json").status_code == 400
    world.workspace.refresh_from_db()
    assert world.workspace.region_mode == "custom"


def test_mode_switch_preserves_exact_location_rows(world):
    client = client_for(world.actor)
    cities = list(City.objects.order_by("pk").values())
    regions = list(Region.objects.order_by("pk").values())
    for mode in ("custom", "divar", "custom"):
        assert client.patch(MODE, {"region_mode": mode}, format="json").status_code == 200
        assert list(City.objects.order_by("pk").values()) == cities
        assert list(Region.objects.order_by("pk").values()) == regions


@pytest.mark.parametrize("kind", ["cities", "regions"])
@pytest.mark.parametrize("field,value", [
    ("workspace", "foreign"), ("workspace_id", "foreign"), ("source", "divar"),
    ("source", "manual"), ("external_id", "123"), ("external_slug", "tehran"), ("id", "foreign"),
])
def test_location_payload_allowlist(world, kind, field, value):
    client = client_for(world.actor)
    obj = world.city if kind == "cities" else world.region
    if value == "foreign":
        value = str(world.foreign_workspace.pk)
    assert client.post(endpoint(kind), body(world, kind, **{field: value}), format="json").status_code == 400
    assert client.patch(endpoint(kind, obj), {field: value}, format="json").status_code == 400
    obj.refresh_from_db()
    assert obj.workspace_id == world.workspace.pk and obj.source == "manual"
    assert obj.external_id == obj.external_slug == ""


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_manual_creation_and_response_allowlist(world, kind):
    response = client_for(world.actor).post(endpoint(kind), body(world, kind), format="json")
    assert response.status_code == 201
    fields = {"id", "name", "source", "source_display", "is_active"}
    if kind == "regions":
        fields |= {"city", "city_name"}
    assert set(response.data) == fields
    assert response.data["source"] == "manual" and response.data["source_display"] == "دستی"


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_duplicate_names_and_cross_workspace_names(world, kind):
    client = client_for(world.actor)
    obj = world.city if kind == "cities" else world.region
    assert client.post(endpoint(kind), body(world, kind, name=obj.name), format="json").status_code == 400
    foreign = world.foreign_city if kind == "cities" else world.foreign_region
    foreign.name = "نام خارجی"
    foreign.save()
    assert client.post(endpoint(kind), body(world, kind, name=foreign.name), format="json").status_code == 201
    created = client.post(endpoint(kind), body(world, kind, name="نام دوم"), format="json")
    response = client.patch(endpoint(kind) + created.data["id"] + "/", {"name": obj.name}, format="json")
    assert response.status_code == 400 and "name" in response.data


def test_region_names_can_repeat_in_another_city(world):
    response = client_for(world.actor).post(endpoint("regions"), {
        "city": str(world.other_city.pk), "name": world.region.name,
    }, format="json")
    assert response.status_code == 201


@pytest.mark.parametrize("city_id", ["foreign", "missing", "invalid"])
def test_region_city_scope_create_and_update(world, city_id):
    value = str(world.foreign_city.pk) if city_id == "foreign" else str(uuid.uuid4()) if city_id == "missing" else "bad"
    client = client_for(world.actor)
    assert client.post(endpoint("regions"), body(world, "regions", city=value), format="json").status_code == 400
    assert client.patch(endpoint("regions", world.region), {"city": value, "name": "تغییر"}, format="json").status_code == 400
    world.region.refresh_from_db()
    assert world.region.city_id == world.city.pk and world.region.name == "مرکز"


def test_region_city_update_same_workspace(world):
    response = client_for(world.actor).patch(endpoint("regions", world.region), {"city": str(world.other_city.pk)}, format="json")
    assert response.status_code == 200 and response.data["city_name"] == world.other_city.name


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_scoped_lists_and_guessed_ids(world, kind):
    client = client_for(world.actor)
    foreign = world.foreign_city if kind == "cities" else world.foreign_region
    listed = client.get(endpoint(kind), {"workspace": str(world.foreign_workspace.pk)})
    assert str(foreign.pk) not in {row["id"] for row in listed.data["results"]}
    for method in [client.get, client.patch]:
        kwargs = {"data": {"name": "تغییر"}, "format": "json"} if method == client.patch else {}
        response = method(endpoint(kind, foreign), **kwargs)
        missing = method(endpoint(kind) + str(uuid.uuid4()) + "/", **kwargs)
        assert response.status_code == missing.status_code == 404
        assert response.data == missing.data


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_update_deactivation_reactivation_no_delete(world, kind):
    obj = world.city if kind == "cities" else world.region
    client = client_for(world.actor)
    for active in [False, True]:
        response = client.patch(endpoint(kind, obj), {"name": "ویرایش", "is_active": active}, format="json")
        assert response.status_code == 200 and response.data["is_active"] is active
    assert client.delete(endpoint(kind, obj)).status_code == 405
    assert client.put(endpoint(kind, obj), body(world, kind), format="json").status_code == 405


@pytest.mark.parametrize("kind", ["cities", "regions"])
@pytest.mark.parametrize("data", [{"name": "تغییر"}, {"is_active": False}, {"external_id": "new"}, {"source": "manual"}])
def test_external_records_readable_but_not_mutable(world, kind, data):
    obj = world.city if kind == "cities" else world.region
    obj.source = "divar"
    obj.external_id = "external"
    obj.save()
    client = client_for(world.actor)
    assert client.get(endpoint(kind, obj)).status_code == 200
    assert client.patch(endpoint(kind, obj), data, format="json").status_code in (400, 403)
    obj.refresh_from_db()
    assert obj.source == "divar" and obj.external_id == "external" and obj.is_active


@pytest.mark.parametrize("path", ["cities/", "regions/", "location-settings/"])
def test_anonymous_denied(path):
    assert APIClient().get(BASE + path).status_code == 401


@pytest.mark.parametrize("state", ["inactive_user", "inactive_workspace", "no_workspace"])
def test_customer_eligibility_remains_required(world, state):
    client = client_for(world.actor)
    if state == "inactive_user":
        User.objects.filter(pk=world.actor.pk).update(is_active=False)
    elif state == "inactive_workspace":
        Workspace.objects.filter(pk=world.workspace.pk).update(is_active=False)
    else:
        User.objects.filter(pk=world.actor.pk).update(workspace=None)
    for path in ["cities/", "regions/", "location-settings/"]:
        assert client.get(BASE + path).status_code == 401


def test_services_recheck_current_actor_and_reject_extra_fields(world):
    with pytest.raises(ValidationError):
        save_city(actor=world.actor, data={"name": "جدید", "workspace_id": world.foreign_workspace.pk})
    with pytest.raises(ValidationError):
        save_region(actor=world.actor, data={"name": "جدید", "city": world.foreign_city.pk})
    with pytest.raises(ValidationError):
        set_region_mode(actor=world.actor, data={"region_mode": "wrong"})
    User.objects.filter(pk=world.actor.pk).update(role="consultant")
    with pytest.raises(PermissionDenied):
        save_city(actor=world.actor, data={"name": "جدید"})


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_list_query_count_is_constant(world, kind):
    client = client_for(world.actor)
    with CaptureQueriesContext(connection) as initial:
        assert client.get(endpoint(kind)).status_code == 200
    for i in range(6):
        city = City.objects.create(workspace=world.workspace, name=f"شهر {i}")
        Region.objects.create(workspace=world.workspace, city=city, name=f"منطقه {i}")
    with CaptureQueriesContext(connection) as expanded:
        assert client.get(endpoint(kind)).status_code == 200
    assert len(initial) == len(expanded) == 3


def test_region_city_filter_is_scoped(world):
    client = client_for(world.actor)
    assert client.get(endpoint("regions"), {"city": str(world.city.pk)}).data["count"] == 1
    for city_id in [world.foreign_city.pk, uuid.uuid4()]:
        response = client.get(endpoint("regions"), {"city": str(city_id)})
        assert response.status_code == 200 and response.data["results"] == []
    assert client.get(endpoint("regions"), {"city": "bad"}).status_code == 400


@pytest.mark.parametrize("kind", ["cities", "regions"])
@pytest.mark.parametrize("data", [{"name": ""}, {"name": " "}, {"name": "x" * 101}, {"is_active": "bad"}])
def test_invalid_fields_do_not_mutate_records(world, kind, data):
    obj = world.city if kind == "cities" else world.region
    original = obj.name
    response = client_for(world.actor).patch(endpoint(kind, obj), data, format="json")
    assert response.status_code == 400
    obj.refresh_from_db()
    assert obj.name == original and obj.is_active


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_pagination_is_bounded_and_stable(world, kind):
    for i in range(51):
        if kind == "cities":
            City.objects.create(workspace=world.workspace, name=f"شهر {i}")
        else:
            Region.objects.create(workspace=world.workspace, city=world.city, name=f"منطقه {i}")
    client = client_for(world.actor)
    first = client.get(endpoint(kind)).data
    second = client.get(endpoint(kind), {"page": 2}).data
    assert len(first["results"]) == 50
    ids = [row["id"] for row in first["results"] + second["results"]]
    assert len(set(ids)) == len(ids) == first["count"]


def test_django_privileges_do_not_grant_location_management(world):
    User.objects.filter(pk=world.actor.pk).update(role="admin", is_staff=True, is_superuser=True)
    client = client_for(world.actor)
    assert client.post(endpoint("cities"), {"name": "جدید"}, format="json").status_code == 403
    assert client.patch(MODE, {"region_mode": "custom"}, format="json").status_code == 403


def test_revoked_ownership_is_checked_using_current_database(world):
    User.objects.filter(pk=world.actor.pk).update(role="consultant", is_workspace_owner=True)
    world.actor.refresh_from_db()
    client = client_for(world.actor)
    User.objects.filter(pk=world.actor.pk).update(is_workspace_owner=False)
    assert client.patch(MODE, {"region_mode": "custom"}, format="json").status_code == 403
    with pytest.raises(PermissionDenied):
        set_region_mode(actor=world.actor, data={"region_mode": "custom"})


@pytest.mark.parametrize("manager", [False, True])
def test_inactive_visibility_filters_and_parent_deactivation(world, manager):
    # City deactivation hides its regions from readers without rewriting child flags.
    city_response = client_for(world.actor).patch(endpoint("cities", world.city), {"is_active": False}, format="json")
    assert city_response.status_code == 200
    world.region.refresh_from_db()
    assert world.region.is_active
    if not manager:
        User.objects.filter(pk=world.actor.pk).update(role="consultant")
    client = client_for(world.actor)
    for kind, obj in [("cities", world.city), ("regions", world.region)]:
        assert client.get(endpoint(kind, obj)).status_code == (200 if manager else 404)
        ids = {row["id"] for row in client.get(endpoint(kind)).data["results"]}
        assert (str(obj.pk) in ids) is manager
        assert client.get(endpoint(kind), {"is_active": "bad"}).status_code == 400
    cities = client.get(endpoint("cities"), {"is_active": "false"}).data["results"]
    assert len(cities) == (1 if manager else 0)
    Region.objects.filter(pk=world.region.pk).update(is_active=False)
    regions = client.get(endpoint("regions"), {"is_active": "false"}).data["results"]
    assert len(regions) == (1 if manager else 0)


def test_city_reactivation_restores_read_visibility_without_changing_regions(world):
    manager = client_for(world.actor)
    reader = User.objects.create_user(username="reader", workspace=world.workspace, role="consultant")
    client = client_for(reader)
    manager.patch(endpoint("cities", world.city), {"is_active": False}, format="json")
    assert client.get(endpoint("regions", world.region)).status_code == 404
    manager.patch(endpoint("cities", world.city), {"is_active": True}, format="json")
    assert client.get(endpoint("regions", world.region)).status_code == 200


def test_legacy_cross_workspace_region_link_is_not_exposed(world):
    Region.objects.filter(pk=world.region.pk).update(city=world.foreign_city)
    client = client_for(world.actor)
    assert client.get(endpoint("regions", world.region)).status_code == 404
    assert client.get(endpoint("regions")).data["results"] == []


def test_inactive_region_in_active_city_is_hidden_from_reader(world):
    Region.objects.filter(pk=world.region.pk).update(is_active=False)
    User.objects.filter(pk=world.actor.pk).update(role="consultant")
    client = client_for(world.actor)
    assert client.get(endpoint("cities", world.city)).status_code == 200
    assert client.get(endpoint("regions", world.region)).status_code == 404
    assert client.get(endpoint("regions"), {"is_active": "false"}).data["results"] == []


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_creation_ignores_foreign_tenant_selector(world, kind):
    response = client_for(world.actor).post(
        endpoint(kind) + "?workspace_id=" + str(world.foreign_workspace.pk), body(world, kind),
        format="json", HTTP_X_WORKSPACE_SLUG=world.foreign_workspace.slug,
    )
    assert response.status_code == 201
    model = City if kind == "cities" else Region
    assert model.objects.get(pk=response.data["id"]).workspace_id == world.workspace.pk


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_services_enforce_target_scope_and_source(world, kind):
    service = save_city if kind == "cities" else save_region
    foreign = world.foreign_city if kind == "cities" else world.foreign_region
    with pytest.raises(NotFound):
        service(actor=world.actor, data={"name": "تغییر"}, object_id=foreign.pk)
    obj = world.city if kind == "cities" else world.region
    obj.source = "divar"
    obj.save()
    with pytest.raises(PermissionDenied):
        service(actor=world.actor, data={"is_active": False}, object_id=obj.pk)


def test_service_validation_rolls_back_city_and_region_changes(world):
    with pytest.raises(ValidationError):
        save_city(actor=world.actor, data={"name": ""}, object_id=world.city.pk)
    with pytest.raises(ValidationError):
        save_region(actor=world.actor, data={"city": world.other_city.pk, "name": ""}, object_id=world.region.pk)
    world.city.refresh_from_db()
    world.region.refresh_from_db()
    assert world.city.name == "تهران" and world.region.city_id == world.city.pk


def test_managers_can_configure_inactive_city_without_activating_it(world):
    City.objects.filter(pk=world.city.pk).update(is_active=False)
    response = client_for(world.actor).post(endpoint("regions"), body(world, "regions"), format="json")
    assert response.status_code == 201
    world.city.refresh_from_db()
    assert not world.city.is_active


@pytest.mark.parametrize("field", ["workspace", "workspace_id", "id"])
def test_mode_rejects_workspace_override(world, field):
    response = client_for(world.actor).patch(MODE, {"region_mode": "divar", field: str(world.foreign_workspace.pk)}, format="json")
    assert response.status_code == 400 and field in response.data
    world.workspace.refresh_from_db()
    world.foreign_workspace.refresh_from_db()
    assert world.workspace.region_mode is None and world.foreign_workspace.region_mode is None


@pytest.mark.parametrize("kind", ["cities", "regions"])
def test_form_creation_has_same_active_default_as_json(world, kind):
    response = client_for(world.actor).post(endpoint(kind), body(world, kind))
    assert response.status_code == 201 and response.data["is_active"] is True
