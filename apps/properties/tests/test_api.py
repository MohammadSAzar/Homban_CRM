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
from apps.properties.models import PropertyFile, PropertyFileImage
from apps.properties.services import create_property_file
from apps.properties.api_services import save_file

pytestmark = pytest.mark.django_db
BASE = "/api/v1/property-files/"


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def url(item):
    return BASE + str(item.pk) + "/"


@pytest.fixture
def world():
    workspace = Workspace.objects.create(name="اول", slug="files-first", customer_type="agency_manager")
    foreign_workspace = Workspace.objects.create(name="دوم", slug="files-other", customer_type="consultant")
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
    foreign_city = City.objects.create(workspace=foreign_workspace, name="تهران")
    foreign_region = Region.objects.create(workspace=foreign_workspace, city=foreign_city, name="ونک")
    def file(assignee, ws=workspace, location=city):
        return create_property_file(workspace=ws, assigned_to=assignee, city=location,
            region=region if ws == workspace else foreign_region, area=100, bedrooms=0, total_price=1000,
            transaction_type="sale", owner_name="نام محرمانه", owner_phone="09121111111",
            visit_contact_phone="09122222222", description="یادداشت محرمانه", address="نشانی محرمانه",
            valuable_reasons=["قیمت مناسب"], image_references=["private/image"])
    own = file(consultant)
    managed = file(second)
    outside = file(unrelated)
    foreign_file = file(foreign_user, foreign_workspace, foreign_city)
    return SimpleNamespace(**locals())


def body(world, **changes):
    return {"transaction_type": "sale", "city": str(world.city.pk), "region": str(world.region.pk), "area": "100", "bedrooms": 0, "total_price": 1000, **changes}


@pytest.mark.parametrize("actor", ["agency", "manager", "consultant"])
def test_create_and_contact_detail(world, actor):
    user = getattr(world, actor)
    client = client_for(user)
    data = body(world, region=str(world.region.pk), owner_name="مالک", owner_phone="09120000000",
        visit_contact_phone="09123333333", description="توضیح", address="نشانی", area="90.25",
        parking=False, storage=None, elevator=True, balcony=False, valuable_reasons=["موقعیت", "قیمت"])
    if actor != "consultant":
        data["assigned_to"] = str(world.consultant.pk)
    response = client.post(BASE, data, format="json")
    assert response.status_code == 201, response.data
    assert response.data["owner_name"] == "مالک"
    assert response.data["owner_phone"] == "09120000000"
    assert response.data["visit_contact_phone"] == "09123333333"
    assert set(response.data["valuable_reasons"]) == {"موقعیت", "قیمت"}
    assert response.data["parking"] is False and response.data["storage"] is None
    assert set(response.data["assigned_to"]) == {"id", "username", "first_name", "last_name"}
    assert response.data["assigned_to"]["id"] == str(world.consultant.pk)
    assert PropertyFile.objects.get(pk=response.data["id"]).workspace == world.workspace
    assert client.get(BASE + response.data["id"] + "/").data["description"] == "توضیح"


@pytest.mark.parametrize("actor", ["agency", "manager", "consultant"])
@pytest.mark.parametrize("target", ["foreign_user", "agency", "manager"])
def test_ineligible_assignment(world, actor, target):
    client = client_for(getattr(world, actor))
    assert client.post(BASE, body(world, assigned_to=str(getattr(world, target).pk)), format="json").status_code == 400


def test_consultant_cannot_assign_another(world):
    client = client_for(world.consultant)
    assert client.post(BASE, body(world, assigned_to=str(world.second.pk)), format="json").status_code == 400
    assert client.patch(url(world.own), {"assigned_to": str(world.second.pk)}, format="json").status_code == 400
    assert client.patch(url(world.own), {"assigned_to": str(world.consultant.pk)}, format="json").status_code == 200


@pytest.mark.parametrize("actor", ["agency", "manager"])
def test_manager_requires_explicit_active_target(world, actor):
    client = client_for(getattr(world, actor))
    assert client.post(BASE, body(world), format="json").status_code == 400
    world.consultant.is_active = False
    world.consultant.save()
    assert client.post(BASE, body(world, assigned_to=str(world.consultant.pk)), format="json").status_code == 400
    # Historical records stay manageable; user activation is never implicit.
    assert client.patch(url(world.own), {"description": "تاریخی"}, format="json").status_code == 200


def test_range_manager_union_and_revocation(world):
    client = client_for(world.manager)
    assert {row["id"] for row in client.get(BASE).data["results"]} == {str(world.own.pk), str(world.managed.pk)}
    for user in (world.consultant, world.second):
        assert client.post(BASE, body(world, assigned_to=str(user.pk)), format="json").status_code == 201
    assert client.post(BASE, body(world, assigned_to=str(world.unrelated.pk)), format="json").status_code == 400
    world.team.is_active = False
    world.team.save()
    assert client.get(url(world.own)).status_code == 404
    assert client.get(url(world.managed)).status_code == 200
    world.other_team.is_active = False
    world.other_team.save()
    assert client.get(BASE).data["count"] == 0
    assert client.post(BASE, body(world, assigned_to=str(world.second.pk)), format="json").status_code == 400


@pytest.mark.parametrize("actor,expected", [("agency", 3), ("manager", 2), ("consultant", 1)])
def test_list_scope_and_no_sensitive_list_fields(world, actor, expected):
    client = client_for(getattr(world, actor))
    response = client.get(BASE, {"workspace": str(world.foreign_workspace.pk)})
    assert response.status_code == 200
    assert response.data["count"] == expected
    for row in response.data["results"]:
        assert not {"owner_name", "owner_phone", "visit_contact_phone", "description", "address", "images", "password", "groups", "user_permissions"} & set(row)
    assert client.get(url(world.own)).data["owner_phone"] == world.own.owner_phone


@pytest.mark.parametrize("actor", ["manager", "consultant"])
@pytest.mark.parametrize("operation", ["get", "patch", "image_add", "image_delete", "image_order"])
def test_other_owner_or_workspace_is_not_found(world, actor, operation):
    client = client_for(getattr(world, actor))
    for item in (world.outside, world.foreign_file):
        if operation == "get":
            response = client.get(url(item))
        elif operation == "patch":
            response = client.patch(url(item), {"description": "تغییر", "valuable_reasons": []}, format="json")
        elif operation == "image_add":
            response = client.post(url(item) + "images/", {"reference": "x"}, format="json")
        elif operation == "image_delete":
            response = client.delete(url(item) + "images/" + str(item.images.first().pk) + "/")
        else:
            response = client.patch(url(item) + "images/", {"order": []}, format="json")
        assert response.status_code == 404, response.data
        assert not any(value in str(response.data) for value in (item.owner_phone, item.visit_contact_phone, item.owner_name, item.description, item.address))
    assert client.get(url(world.foreign_file)).data == client.get(BASE + str(uuid.uuid4()) + "/").data


@pytest.mark.parametrize("role", ["secretary", "admin"])
@pytest.mark.parametrize("owner", [False, True])
def test_denied_roles_even_owner_or_staff(world, role, owner):
    actor = User.objects.create_user(username=role, workspace=world.workspace, role=role, is_workspace_owner=owner, is_staff=True, is_superuser=True)
    client = client_for(actor)
    assert client.get(BASE).status_code == 403
    assert client.get(url(world.own)).status_code == 403
    assert client.post(BASE, body(world), format="json").status_code == 403
    assert client.patch(url(world.own), {"status": "inactive"}, format="json").status_code == 403


@pytest.mark.parametrize("actor", ["manager", "consultant"])
def test_owner_does_not_expand_scope(world, actor):
    user = getattr(world, actor)
    user.is_workspace_owner = True
    user.save()
    assert client_for(user).get(url(world.outside)).status_code == 404


@pytest.mark.parametrize("field,value", [
    ("workspace", "fake"), ("workspace_id", "fake"), ("code", "fake"), ("id", "fake"),
    ("created_at", "2020-01-01"), ("updated_at", "2020-01-01"), ("source", "divar"), ("price_per_square_meter", 0),
    ("is_superuser", True), ("images", []), ("assigned_to_id", "fake"),
])
def test_immutable_and_extra_payload_rejected(world, field, value):
    client = client_for(world.consultant)
    assert client.post(BASE, body(world, **{field: value}), format="json").status_code == 400
    assert client.patch(url(world.own), {field: value}, format="json").status_code == 400


@pytest.mark.parametrize("changes", [
    {"area": -1}, {"bedrooms": -1}, {"total_price": -1}, {"building_age": -1},
    {"unit_floor": -1}, {"total_floors": -1}, {"units_per_floor": -1},
    {"deposit_amount": 0}, {"monthly_rent": 0}, {"transaction_type": "bad"},
    {"status": "bad"}, {"parking": "bad"},
])
def test_domain_validation(world, changes):
    response = client_for(world.consultant).post(BASE, body(world, **changes), format="json")
    assert response.status_code == 400, response.data


def test_rent_and_transaction_change(world):
    client = client_for(world.consultant)
    response = client.post(BASE, body(world, transaction_type="rent", total_price=None, deposit_amount="100.50", monthly_rent="25.25"), format="json")
    assert response.status_code == 201, response.data
    endpoint = BASE + response.data["id"] + "/"
    assert client.patch(endpoint, {"total_price": 5}, format="json").status_code == 400
    assert client.patch(endpoint, {"transaction_type": "sale", "deposit_amount": None, "monthly_rent": None, "total_price": 10}, format="json").status_code == 200


def test_locations_and_cross_workspace_assignment(world):
    client = client_for(world.agency)
    for fields in ({"city": str(world.foreign_city.pk)}, {"region": str(world.foreign_region.pk)}, {"assigned_to": str(world.foreign_user.pk)}):
        response = client.patch(url(world.own), fields, format="json")
        assert response.status_code == 400
    city = City.objects.create(workspace=world.workspace, name="ری")
    assert client.patch(url(world.own), {"city": str(city.pk), "region": str(world.region.pk)}, format="json").status_code == 400
    assert client.get(url(world.foreign_file)).status_code == 404
    assert client.patch(url(world.foreign_file), {"status": "inactive"}, format="json").status_code == 404


@pytest.mark.parametrize("status", PropertyFile.Status.values)
def test_status_and_no_hard_delete(world, status):
    client = client_for(world.consultant)
    assert client.patch(url(world.own), {"status": status}, format="json").status_code == 200
    assert client.get(url(world.own)).status_code == 200
    assert client.delete(url(world.own)).status_code == 405
    assert client.put(url(world.own), {}, format="json").status_code == 405
    assert world.own.images.count() == 1


def test_reassignment_scope(world):
    agency, manager = client_for(world.agency), client_for(world.manager)
    assert manager.patch(url(world.own), {"assigned_to": str(world.second.pk)}, format="json").status_code == 200
    assert client_for(world.consultant).get(url(world.own)).status_code == 404
    assert manager.patch(url(world.own), {"assigned_to": str(world.unrelated.pk)}, format="json").status_code == 400
    assert agency.patch(url(world.own), {"assigned_to": str(world.unrelated.pk)}, format="json").status_code == 200
    assert manager.get(url(world.own)).status_code == 404


def test_reasons_replace_and_rollback(world):
    client = client_for(world.consultant)
    assert client.patch(url(world.own), {"valuable_reasons": ["یک", "دو"], "is_valuable": True}, format="json").status_code == 200
    response = client.patch(url(world.own), {"description": "rollback", "valuable_reasons": ["تکراری", "تکراری"]}, format="json")
    assert response.status_code == 400
    world.own.refresh_from_db()
    assert world.own.description != "rollback"
    assert set(world.own.valuable_reasons.values_list("reason", flat=True)) == {"یک", "دو"}
    assert client.patch(url(world.own), {"valuable_reasons": []}, format="json").status_code == 200
    assert world.own.valuable_reasons.count() == 0
    assert world.own.is_valuable


def test_images_add_reorder_remove(world):
    client = client_for(world.consultant)
    images_url = url(world.own) + "images/"
    first = world.own.images.first()
    response = client.post(images_url, {"reference": "https://example.com/image.jpg"}, format="json")
    assert response.status_code == 201, response.data
    second = response.data["id"]
    assert response.data["sort_order"] == 1
    assert client.patch(images_url, {"order": [second, str(first.pk)]}, format="json").status_code == 204
    detail = client.get(url(world.own)).data
    assert [image["id"] for image in detail["images"]] == [second, str(first.pk)]
    for order in ([second], [second, second], [second, str(world.foreign_file.images.first().pk)]):
        assert client.patch(images_url, {"order": order}, format="json").status_code == 400
    assert client.delete(images_url + str(first.pk) + "/").status_code == 204
    assert client.delete(images_url + str(world.managed.images.first().pk) + "/").status_code == 404
    assert client.post(images_url, {"reference": "x", "sort_order": 20}, format="json").status_code == 400
    assert client.post(images_url, {"reference": ""}, format="json").status_code == 400


@pytest.mark.parametrize("filters", [
    {"transaction_type": "sale"}, {"status": "archived"}, {"min_area": 99}, {"max_area": 101},
    {"bedrooms": 3}, {"min_total_price": 999}, {"max_total_price": 1001}, {"is_valuable": "true"},
    {"source": "manual"},
])
def test_filters(world, filters):
    world.own.status = "archived"
    world.own.area = 100
    world.own.bedrooms = 3
    world.own.total_price = 1000
    world.own.is_valuable = True
    world.own.save()
    world.managed.transaction_type = "rent"
    world.managed.total_price = None
    world.managed.deposit_amount = 0
    world.managed.monthly_rent = 0
    world.managed.area = 200 if "max_area" in filters else 50
    world.managed.save()
    response = client_for(world.manager).get(BASE, filters)
    assert response.status_code == 200, response.data
    ids = {row["id"] for row in response.data["results"]}
    assert str(world.own.pk) in ids
    if "source" not in filters:
        assert str(world.managed.pk) not in ids


def test_uuid_filters_and_invalid_filters(world):
    world.own.region = Region.objects.create(workspace=world.workspace, city=world.city, name="ویژه")
    world.own.save()
    client = client_for(world.agency)
    for field, target in (("assigned_to", world.consultant), ("region", world.own.region)):
        assert client.get(BASE, {field: str(target.pk)}).data["count"] == 1
    assert client.get(BASE, {"city": str(world.city.pk)}).data["count"] == 3
    assert client.get(BASE, {"assigned_to": str(world.foreign_user.pk)}).data["count"] == 0
    for params in ({"city": "bad"}, {"min_area": "bad"}, {"status": "bad"}):
        assert client.get(BASE, params).status_code == 400


@pytest.mark.parametrize("actor", ["agency", "manager", "consultant"])
def test_list_detail_queries_do_not_grow(world, actor):
    client = client_for(getattr(world, actor))
    for _ in range(2):
        with CaptureQueriesContext(connection) as queries:
            response = client.get(BASE)
        assert response.status_code == 200
        assert len(queries) == 3
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url(world.own))
        assert response.status_code == 200
        assert len(queries) == 4
        create_property_file(workspace=world.workspace, assigned_to=world.consultant, city=world.city,
            transaction_type="sale", region=world.region, area=100, bedrooms=0, total_price=1000, image_references=["a", "b"], valuable_reasons=["الف", "ب"])
        PropertyFileImage.objects.create(property_file=world.own, reference="extra")


def test_anonymous_inactive_and_current_role(world):
    assert APIClient().get(BASE).status_code == 401
    client = client_for(world.consultant)
    world.consultant.is_active = False
    world.consultant.save()
    assert client.get(BASE).status_code == 401
    world.consultant.is_active = True
    world.consultant.role = "admin"
    world.consultant.save()
    assert client.get(BASE).status_code == 403


def test_services_recheck_actor_and_reject_privileges(world):
    User.objects.filter(pk=world.agency.pk).update(role="secretary")
    with pytest.raises(PermissionDenied):
        save_file(actor=world.agency, data={"description": "x"}, file_id=world.own.pk)
    with pytest.raises(ValidationError):
        save_file(actor=world.consultant, data={"workspace": world.foreign_workspace.pk}, file_id=world.own.pk)


@pytest.mark.parametrize("state", ["inactive_workspace", "workspace_less"])
def test_customer_auth_state(world, state):
    client = client_for(world.consultant)
    if state == "inactive_workspace":
        world.workspace.is_active = False
        world.workspace.save()
    else:
        world.consultant.workspace = None
        world.consultant.save()
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, body(world), format="json").status_code == 401


def test_membership_move_revokes_management_immediately(world):
    client = client_for(world.manager)
    team = Range.objects.create(workspace=world.workspace, name="بدون مدیر")
    membership = world.consultant.range_membership
    membership.range = team
    membership.save()
    assert client.get(url(world.own)).status_code == 404
    assert client.patch(url(world.own), {"status": "inactive"}, format="json").status_code == 404
    assert client.post(BASE, body(world, assigned_to=str(world.consultant.pk)), format="json").status_code == 400


def test_create_rollback_on_invalid_reasons(world):
    count = PropertyFile.objects.count()
    response = client_for(world.consultant).post(BASE, body(world, valuable_reasons=["تکراری", "تکراری"]), format="json")
    assert response.status_code == 400
    assert PropertyFile.objects.count() == count


def test_list_pagination(world):
    for _ in range(50):
        PropertyFile.objects.create(workspace=world.workspace, assigned_to=world.consultant, city=world.city, region=world.region, area=100, bedrooms=0, total_price=1000, transaction_type="sale")
    client = client_for(world.consultant)
    first = client.get(BASE).data
    second = client.get(BASE, {"page": 2}).data
    assert first["count"] == 51
    assert len(first["results"]) == 50 and len(second["results"]) == 1
    assert not {row["id"] for row in first["results"]} & {row["id"] for row in second["results"]}
