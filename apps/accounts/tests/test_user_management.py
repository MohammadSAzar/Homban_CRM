import itertools
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.models import User
from apps.accounts.services import create_organizational_user, update_organizational_user
from apps.organizations.models import Workspace
from apps.ranges.models import Range, RangeMembership

pytestmark = pytest.mark.django_db
URL = "/api/v1/users/"
PASSWORD = "Unrelated-Strong-Password-839!"
RESPONSE_FIELDS = {
    "id", "username", "first_name", "last_name", "phone_number", "role", "role_display",
    "is_active", "is_workspace_owner", "range", "managed_ranges",
}


def detail(user):
    return f"{URL}{user.pk}/"


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(user)))
    return client


def payload(**overrides):
    return {"username": "new-user", "password": PASSWORD, "role": "consultant", **overrides}


@pytest.fixture
def world(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    workspace = Workspace.objects.create(name="مجموعه اول", slug="first", customer_type="agency_manager")
    other_workspace = Workspace.objects.create(name="مجموعه دوم", slug="second", customer_type="agency_manager")
    serial = itertools.count()

    def make(role="consultant", **kwargs):
        values = {"workspace": workspace, "username": f"user-{next(serial)}", "role": role, **kwargs}
        return User.objects.create_user(password=PASSWORD, **values)

    agency = make("agency_manager")
    manager = make("range_manager")
    other_manager = make("range_manager")
    team = Range.objects.create(workspace=workspace, name="رنج اول", manager=manager)
    other_team = Range.objects.create(workspace=workspace, name="رنج دوم", manager=other_manager)
    empty_team = Range.objects.create(workspace=workspace, name="رنج بدون مدیر")
    foreign_team = Range.objects.create(workspace=other_workspace, name="رنج خارجی")
    consultant = make()
    RangeMembership.objects.create(workspace=workspace, user=consultant, range=team)
    unrelated = make()
    RangeMembership.objects.create(workspace=workspace, user=unrelated, range=other_team)
    foreign = make(workspace=other_workspace)
    secretary = make("secretary")
    admin = make("admin")
    owner = make(is_workspace_owner=True)
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("role", ["range_manager", "consultant", "secretary", "admin"])
def test_agency_creates_allowed_roles(world, role):
    response = client_for(world.agency).post(URL, payload(role=role), format="json")
    assert response.status_code == 201, response.data
    user = User.objects.get(pk=response.data["id"])
    assert user.workspace_id == world.workspace.pk and user.role == role
    assert user.check_password(PASSWORD) and user.password != PASSWORD
    assert not user.is_staff and not user.is_superuser and not user.is_workspace_owner
    assert set(response.data) == RESPONSE_FIELDS
    assert PASSWORD not in str(response.data) and user.password not in str(response.data)
    assert not RangeMembership.objects.filter(user=user).exists()


def test_agency_assigns_consultant(world):
    response = client_for(world.agency).post(URL, payload(range_id=str(world.team.pk)), format="json")
    assert response.status_code == 201
    membership = RangeMembership.objects.get(user_id=response.data["id"])
    assert membership.workspace_id == world.workspace.pk and membership.range_id == world.team.pk
    assert response.data["range"]["id"] == str(world.team.pk)


def test_agency_assigns_range_manager_without_membership(world):
    response = client_for(world.agency).post(URL, payload(role="range_manager", range_id=str(world.empty_team.pk)), format="json")
    assert response.status_code == 201, response.data
    world.empty_team.refresh_from_db()
    assert str(world.empty_team.manager_id) == response.data["id"]
    assert not RangeMembership.objects.filter(user_id=response.data["id"]).exists()
    assert response.data["managed_ranges"] == [{"id": str(world.empty_team.pk), "name": world.empty_team.name}]


def test_existing_range_manager_is_not_replaced(world):
    response = client_for(world.agency).post(URL, payload(role="range_manager", range_id=str(world.team.pk)), format="json")
    assert response.status_code == 400
    world.team.refresh_from_db()
    assert world.team.manager_id == world.manager.pk
    assert not User.objects.filter(username="new-user").exists()


def test_range_manager_creates_consultant_in_own_range(world):
    response = client_for(world.manager).post(URL, payload(), format="json")
    assert response.status_code == 201, response.data
    assert RangeMembership.objects.get(user_id=response.data["id"]).range_id == world.team.pk


@pytest.mark.parametrize("role", ["agency_manager", "range_manager", "secretary", "admin"])
def test_range_manager_cannot_create_other_roles(world, role):
    assert client_for(world.manager).post(URL, payload(role=role), format="json").status_code == 400
    assert not User.objects.filter(username="new-user").exists()


def test_agency_cannot_create_another_agency_manager(world):
    assert client_for(world.agency).post(URL, payload(role="agency_manager"), format="json").status_code == 400


@pytest.mark.parametrize("actor_name", ["consultant", "secretary", "admin"])
@pytest.mark.parametrize("operation", ["create", "list", "detail", "update", "deactivate"])
def test_denied_roles_have_no_management_access(world, actor_name, operation):
    actor = getattr(world, actor_name)
    # Django privileges and ownership do not elevate a product role.
    User.objects.filter(pk=actor.pk).update(is_staff=True, is_superuser=True, is_workspace_owner=True)
    client = client_for(actor)
    response = {
        "create": lambda: client.post(URL, payload(), format="json"),
        "list": lambda: client.get(URL),
        "detail": lambda: client.get(detail(world.unrelated)),
        "update": lambda: client.patch(detail(world.unrelated), {"first_name": "تغییر"}, format="json"),
        "deactivate": lambda: client.patch(detail(world.unrelated), {"is_active": False}, format="json"),
    }[operation]()
    assert response.status_code == 403


@pytest.mark.parametrize("field,value", [
    ("workspace", "foreign"), ("workspace_id", "foreign"), ("is_workspace_owner", True),
    ("is_staff", True), ("is_superuser", True), ("groups", []), ("user_permissions", []),
    ("id", "foreign"), ("backend", "anything"),
])
def test_creation_rejects_protected_fields(world, field, value):
    if value == "foreign":
        value = str(world.other_workspace.pk)
    response = client_for(world.agency).post(URL, payload(**{field: value}), format="json")
    assert response.status_code == 400 and field in response.data
    assert not User.objects.filter(username="new-user").exists()


def test_request_header_and_query_do_not_override_actor_workspace(world):
    response = client_for(world.agency).post(
        URL + "?workspace_id=" + str(world.other_workspace.pk), payload(), format="json",
        HTTP_X_WORKSPACE_SLUG=world.other_workspace.slug,
    )
    assert response.status_code == 201
    assert User.objects.get(pk=response.data["id"]).workspace_id == world.workspace.pk


def test_duplicate_username_rejected_in_workspace(world):
    response = client_for(world.agency).post(URL, payload(username=world.consultant.username), format="json")
    assert response.status_code == 400 and "username" in response.data


def test_same_username_allowed_in_other_workspace(world):
    response = client_for(world.agency).post(URL, payload(username=world.foreign.username), format="json")
    assert response.status_code == 201
    assert User.objects.filter(username=world.foreign.username).count() == 2


@pytest.mark.parametrize("password", ["short", "123456789012", "password", ""])
def test_weak_password_rejected(world, password):
    response = client_for(world.agency).post(URL, payload(password=password), format="json")
    assert response.status_code == 400 and "password" in response.data
    assert not User.objects.filter(username="new-user").exists()


@pytest.mark.parametrize("team_name", ["foreign_team", "missing", "inactive"])
def test_agency_cannot_assign_invalid_range(world, team_name):
    team_id = uuid.uuid4() if team_name == "missing" else world.foreign_team.pk
    if team_name == "inactive":
        Range.objects.filter(pk=world.empty_team.pk).update(is_active=False)
        team_id = world.empty_team.pk
    response = client_for(world.agency).post(URL, payload(range_id=str(team_id)), format="json")
    assert response.status_code == 400
    assert not User.objects.filter(username="new-user").exists()


@pytest.mark.parametrize("role", ["secretary", "admin"])
def test_non_consultant_membership_rejected(world, role):
    assert client_for(world.agency).post(URL, payload(role=role, range_id=str(world.team.pk)), format="json").status_code == 400


@pytest.mark.parametrize("team_name", ["team", "other_team", "foreign_team", "null"])
def test_range_manager_cannot_supply_range(world, team_name):
    team_id = None if team_name == "null" else str(getattr(world, team_name).pk)
    assert client_for(world.manager).post(URL, payload(range_id=team_id), format="json").status_code == 400


@pytest.mark.parametrize("state", ["no_range", "inactive", "multiple", "foreign"])
def test_invalid_manager_scope_fails_safely(world, state):
    if state == "no_range":
        Range.objects.filter(pk=world.team.pk).update(manager=None)
    elif state == "inactive":
        Range.objects.filter(pk=world.team.pk).update(is_active=False)
    elif state == "multiple":
        Range.objects.filter(pk=world.empty_team.pk).update(manager=world.manager)
    else:
        Range.objects.filter(pk=world.team.pk).update(workspace=world.other_workspace)
    client = client_for(world.manager)
    response = client.post(URL, payload(), format="json")
    assert response.status_code == 400 and "range_id" in response.data
    assert "رنج" in str(response.data["range_id"])
    assert [row["id"] for row in client.get(URL).data["results"]] == [str(world.manager.pk)]


def test_agency_list_is_workspace_scoped_and_allowlisted(world):
    response = client_for(world.agency).get(URL, {"workspace_id": str(world.other_workspace.pk)})
    assert response.status_code == 200
    expected = {str(pk) for pk in User.objects.filter(workspace=world.workspace).values_list("pk", flat=True)}
    assert {row["id"] for row in response.data["results"]} == expected
    assert all(set(row) == RESPONSE_FIELDS for row in response.data["results"])
    assert world.foreign.password not in str(response.data)


def test_range_manager_visibility_is_own_profile_and_consultants(world):
    response = client_for(world.manager).get(URL)
    assert {row["id"] for row in response.data["results"]} == {str(world.manager.pk), str(world.consultant.pk)}
    assert client_for(world.manager).get(detail(world.manager)).status_code == 200
    assert client_for(world.manager).get(detail(world.consultant)).status_code == 200
    assert client_for(world.manager).get(detail(world.unrelated)).status_code == 404


@pytest.mark.parametrize("actor_name", ["agency", "manager"])
@pytest.mark.parametrize("operation", ["get", "patch"])
def test_foreign_uuid_is_indistinguishable_from_missing(world, actor_name, operation):
    client = client_for(getattr(world, actor_name))
    method = getattr(client, operation)
    kwargs = {"data": {"is_active": False}, "format": "json"} if operation == "patch" else {}
    foreign = method(detail(world.foreign), **kwargs)
    absent = method(f"{URL}{uuid.uuid4()}/", **kwargs)
    assert foreign.status_code == absent.status_code == 404
    assert foreign.data == absent.data
    world.foreign.refresh_from_db()
    assert world.foreign.is_active


@pytest.mark.parametrize("actor_name", ["agency", "manager"])
def test_allowed_profile_update(world, actor_name):
    response = client_for(getattr(world, actor_name)).patch(detail(world.consultant), {
        "first_name": "علی", "last_name": "احمدی", "phone_number": "09121111111",
    }, format="json")
    assert response.status_code == 200
    assert response.data["first_name"] == "علی"
    assert set(response.data) == RESPONSE_FIELDS
    world.consultant.refresh_from_db()
    assert world.consultant.phone_number == "09121111111"


@pytest.mark.parametrize("actor_name", ["agency", "manager"])
def test_deactivate_reactivate_preserves_membership_and_blocks_tokens(world, actor_name):
    refresh = RefreshToken.for_user(world.consultant)
    consultant_client = client_for(world.consultant)
    manager_client = client_for(getattr(world, actor_name))
    response = manager_client.patch(detail(world.consultant), {"is_active": False}, format="json")
    assert response.status_code == 200 and response.data["is_active"] is False
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.team.pk
    assert consultant_client.get("/api/v1/auth/me/").status_code == 401
    assert consultant_client.post("/api/v1/auth/refresh/", {"refresh": str(refresh)}).status_code == 401
    assert manager_client.patch(detail(world.consultant), {"is_active": True}, format="json").status_code == 200
    world.consultant.refresh_from_db()
    assert world.consultant.is_active


@pytest.mark.parametrize("field,value", [
    ("role", "agency_manager"), ("workspace", "foreign"), ("workspace_id", "foreign"),
    ("is_workspace_owner", True), ("is_staff", True), ("is_superuser", True),
    ("groups", []), ("user_permissions", []), ("username", "renamed"), ("password", PASSWORD),
])
def test_protected_update_fields_are_rejected(world, field, value):
    if value == "foreign":
        value = str(world.other_workspace.pk)
    response = client_for(world.agency).patch(detail(world.consultant), {field: value}, format="json")
    assert response.status_code == 400 and field in response.data
    world.consultant.refresh_from_db()
    assert world.consultant.role == "consultant" and not world.consultant.is_workspace_owner
    assert world.consultant.workspace_id == world.workspace.pk


@pytest.mark.parametrize("target_name", ["agency", "owner", "other_agency"])
@pytest.mark.parametrize("data", [{"first_name": "تغییر"}, {"is_active": False}])
def test_sensitive_targets_are_read_only(world, target_name, data):
    target = world.make("agency_manager") if target_name == "other_agency" else getattr(world, target_name)
    client = client_for(world.agency)
    assert client.get(detail(target)).status_code == 200
    assert client.patch(detail(target), data, format="json").status_code == 403


def test_range_manager_cannot_update_self_or_other_range(world):
    client = client_for(world.manager)
    assert client.patch(detail(world.manager), {"is_active": False}, format="json").status_code == 403
    assert client.patch(detail(world.unrelated), {"is_active": False}, format="json").status_code == 404


def test_owner_consultant_in_manager_range_is_read_only(world):
    User.objects.filter(pk=world.consultant.pk).update(is_workspace_owner=True)
    assert client_for(world.manager).patch(detail(world.consultant), {"is_active": False}, format="json").status_code == 403


def test_agency_reassigns_and_removes_consultant_membership(world):
    client = client_for(world.agency)
    response = client.patch(detail(world.consultant), {"range_id": str(world.other_team.pk)}, format="json")
    assert response.status_code == 200
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.other_team.pk
    assert client_for(world.manager).get(detail(world.consultant)).status_code == 404
    assert client.patch(detail(world.consultant), {"range_id": None}, format="json").status_code == 200
    assert not RangeMembership.objects.filter(user=world.consultant).exists()
    assert client.patch(detail(world.consultant), {"range_id": str(world.team.pk)}, format="json").status_code == 200


def test_invalid_reassignment_rolls_back_profile_update(world):
    response = client_for(world.agency).patch(detail(world.consultant), {
        "first_name": "تغییر", "range_id": str(world.foreign_team.pk),
    }, format="json")
    assert response.status_code == 400
    world.consultant.refresh_from_db()
    assert world.consultant.first_name == ""
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.team.pk


@pytest.mark.parametrize("target_range", ["team", "other_team", "foreign_team", "null"])
def test_manager_cannot_change_membership(world, target_range):
    value = None if target_range == "null" else str(getattr(world, target_range).pk)
    assert client_for(world.manager).patch(detail(world.consultant), {"range_id": value}, format="json").status_code == 400


def test_non_consultant_update_cannot_add_membership(world):
    assert client_for(world.agency).patch(detail(world.secretary), {"range_id": str(world.team.pk)}, format="json").status_code == 400


def test_no_hard_delete_or_put(world):
    client = client_for(world.agency)
    assert client.delete(detail(world.consultant)).status_code == 405
    assert client.put(detail(world.consultant), {}, format="json").status_code == 405
    assert User.objects.filter(pk=world.consultant.pk).exists()


def test_anonymous_requests_require_authentication(world):
    client = APIClient()
    assert client.get(URL).status_code == 401
    assert client.get(detail(world.consultant)).status_code == 401
    assert client.post(URL, payload(), format="json").status_code == 401


def test_creation_rolls_back_if_membership_write_fails(world):
    before = User.objects.count()
    with patch("apps.accounts.services.RangeMembership.objects.create", side_effect=ModelValidationError("invalid")):
        with pytest.raises(ModelValidationError):
            create_organizational_user(actor=world.manager, data=payload())
    assert User.objects.count() == before


def test_services_enforce_scope_without_view(world):
    with pytest.raises(PermissionDenied):
        create_organizational_user(actor=world.consultant, data=payload())
    with pytest.raises(ValidationError):
        update_organizational_user(actor=world.agency, user_id=world.consultant.pk, data={"role": "admin"})
    User.objects.filter(pk=world.agency.pk).update(role="consultant")
    with pytest.raises(PermissionDenied):
        create_organizational_user(actor=world.agency, data=payload())


@pytest.mark.parametrize("actor_name,expected_queries", [("agency", 4), ("manager", 5)])
def test_management_list_has_constant_query_count(world, actor_name, expected_queries):
    client = client_for(getattr(world, actor_name))
    with CaptureQueriesContext(connection) as initial:
        assert client.get(URL).status_code == 200
    for _ in range(8):
        user = world.make()
        RangeMembership.objects.create(workspace=world.workspace, user=user, range=world.team)
    with CaptureQueriesContext(connection) as expanded:
        response = client.get(URL)
    assert response.status_code == 200
    assert len(initial) == len(expanded) == expected_queries


@pytest.mark.parametrize("actor_name,expected_queries", [("agency", 3), ("manager", 4)])
def test_detail_query_count_and_field_allowlist(world, actor_name, expected_queries):
    with CaptureQueriesContext(connection) as queries:
        response = client_for(getattr(world, actor_name)).get(detail(world.consultant))
    assert response.status_code == 200
    assert set(response.data) == RESPONSE_FIELDS
    assert len(queries) == expected_queries


@pytest.mark.parametrize("state", ["inactive_user", "inactive_workspace", "removed_workspace", "demoted"])
def test_existing_actor_tokens_obey_current_management_permissions(world, state):
    client = client_for(world.agency)
    if state == "inactive_user":
        User.objects.filter(pk=world.agency.pk).update(is_active=False)
    elif state == "inactive_workspace":
        Workspace.objects.filter(pk=world.workspace.pk).update(is_active=False)
    elif state == "removed_workspace":
        User.objects.filter(pk=world.agency.pk).update(workspace=None)
    else:
        User.objects.filter(pk=world.agency.pk).update(role="admin")
    expected = 403 if state == "demoted" else 401
    assert client.get(URL).status_code == expected
    assert client.post(URL, payload(), format="json").status_code == expected


def test_created_user_can_login_and_me_stays_allowlisted(world, settings):
    settings.CUSTOMER_ALLOW_WORKSPACE_HEADER = True
    response = client_for(world.agency).post(URL, payload(), format="json")
    assert response.status_code == 201
    client = APIClient()
    login = client.post("/api/v1/auth/login/", {
        "username": "new-user", "password": PASSWORD,
    }, HTTP_X_WORKSPACE_SLUG=world.workspace.slug)
    assert login.status_code == 200
    client.credentials(HTTP_AUTHORIZATION="Bearer " + login.data["access"])
    me = client.get("/api/v1/auth/me/")
    assert me.status_code == 200
    assert me.data["id"] == response.data["id"]
    assert me.data["workspace_id"] == str(world.workspace.pk)
    assert "password" not in me.data


def test_reassignment_rolls_back_if_profile_validation_fails(world):
    with pytest.raises(ValidationError):
        update_organizational_user(actor=world.agency, user_id=world.consultant.pk, data={
            "range_id": world.other_team.pk, "phone_number": "x" * 21,
        })
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.team.pk


def test_username_is_normalized_before_uniqueness_check(world):
    world.make(username="alice")
    response = client_for(world.agency).post(URL, payload(username="ａｌｉｃｅ"), format="json")
    assert response.status_code == 400 and "username" in response.data


def test_invalid_username_rejected(world):
    response = client_for(world.agency).post(URL, payload(username="bad name"), format="json")
    assert response.status_code == 400 and "username" in response.data


def test_similar_password_rejected_using_candidate_profile(world):
    response = client_for(world.agency).post(URL, payload(username="longdistinctname", password="longdistinctname"), format="json")
    assert response.status_code == 400 and "password" in response.data


def test_manager_deactivation_keeps_range_and_consultants(world):
    response = client_for(world.agency).patch(detail(world.manager), {"is_active": False}, format="json")
    assert response.status_code == 200
    world.team.refresh_from_db()
    world.consultant.refresh_from_db()
    assert world.team.manager_id == world.manager.pk
    assert world.consultant.is_active
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.team.pk


def test_inactive_secondary_range_does_not_create_ambiguity(world):
    Range.objects.filter(pk=world.empty_team.pk).update(manager=world.manager, is_active=False)
    assert client_for(world.manager).post(URL, payload(), format="json").status_code == 201


def test_missing_payload_fields_rejected(world):
    response = client_for(world.agency).post(URL, {}, format="json")
    assert response.status_code == 400
    assert set(response.data) == {"username", "password", "role"}


def test_list_pagination_is_bounded(world):
    for _ in range(52):
        world.make()
    client = client_for(world.agency)
    first = client.get(URL)
    second = client.get(URL, {"page": 2})
    assert len(first.data["results"]) == 50
    first_ids = {row["id"] for row in first.data["results"]}
    second_ids = {row["id"] for row in second.data["results"]}
    assert not first_ids & second_ids
    assert len(first_ids | second_ids) == User.objects.filter(workspace=world.workspace).count()
