import itertools
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import User
from apps.locations.models import City, Region
from apps.organizations.models import Workspace
from apps.ranges.models import Range, RangeMembership
from apps.ranges.services import assign_consultant, remove_consultant, save_range

pytestmark = pytest.mark.django_db
URL = "/api/v1/ranges/"
USER_FIELDS = {"id", "username", "first_name", "last_name"}


def client_for(actor):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + str(AccessToken.for_user(actor)))
    return client


def detail(team):
    return f"{URL}{team.pk}/"


def members(team):
    return detail(team) + "consultants/"


def membership(team, user):
    return members(team) + str(user.pk) + "/"


@pytest.fixture
def world():
    workspace = Workspace.objects.create(name="اول", slug="first", customer_type="agency_manager")
    foreign_workspace = Workspace.objects.create(name="دوم", slug="second", customer_type="agency_manager")
    counter = itertools.count()

    def make(role="consultant", **kwargs):
        return User.objects.create_user(**{"username": f"user-{next(counter)}", "workspace": workspace,
                                          "role": role, **kwargs})

    agency = make("agency_manager")
    manager = make("range_manager")
    other_manager = make("range_manager")
    consultant = make()
    unassigned = make()
    unrelated = make()
    foreign_user = make(workspace=foreign_workspace)
    foreign_manager = make("range_manager", workspace=foreign_workspace)
    secretary = make("secretary")
    admin = make("admin")
    owner = make(is_workspace_owner=True)
    team = Range.objects.create(workspace=workspace, name="اول", manager=manager)
    other = Range.objects.create(workspace=workspace, name="دوم", manager=other_manager)
    foreign = Range.objects.create(workspace=foreign_workspace, name="خارجی", manager=foreign_manager)
    RangeMembership.objects.create(workspace=workspace, range=team, user=consultant)
    RangeMembership.objects.create(workspace=workspace, range=other, user=unrelated)
    city = City.objects.create(workspace=workspace, name="تهران")
    region = Region.objects.create(workspace=workspace, city=city, name="مرکز")
    second_region = Region.objects.create(workspace=workspace, city=city, name="شرق")
    foreign_city = City.objects.create(workspace=foreign_workspace, name="تهران")
    foreign_region = Region.objects.create(workspace=foreign_workspace, city=foreign_city, name="مرکز")
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("with_manager", [False, True])
def test_create_range(world, with_manager):
    data = {"name": "جدید", "regions": [str(world.region.pk)]}
    if with_manager:
        data["manager"] = str(world.manager.pk)
    response = client_for(world.agency).post(URL, data, format="json")
    assert response.status_code == 201, response.data
    team = Range.objects.get(pk=response.data["id"])
    assert team.workspace_id == world.workspace.pk and team.activity_scope == "all" and team.is_active
    assert team.manager_id == (world.manager.pk if with_manager else None)
    assert list(team.regions.all()) == [world.region]
    assert set(response.data) == {"id", "name", "manager", "activity_scope", "activity_scope_display",
                                  "is_active", "regions", "consultant_count", "has_region_constraints"}
    if with_manager:
        assert set(response.data["manager"]) == USER_FIELDS


@pytest.mark.parametrize("scope,valid", [("all", True), ("sale", True), ("rent", True), ("other", False), ("", False), (None, False)])
def test_activity_scope_validation(world, scope, valid):
    response = client_for(world.agency).post(URL, {"name": "جدید", "activity_scope": scope}, format="json")
    assert response.status_code == (201 if valid else 400)
    response = client_for(world.agency).patch(detail(world.team), {"activity_scope": scope}, format="json")
    assert response.status_code == (200 if valid else 400)


@pytest.mark.parametrize("target", ["consultant", "agency", "secretary", "admin", "foreign_manager", "missing"])
def test_invalid_manager_create_and_update(world, target):
    manager_id = uuid.uuid4() if target == "missing" else getattr(world, target).pk
    client = client_for(world.agency)
    assert client.post(URL, {"name": "جدید", "manager": str(manager_id)}, format="json").status_code == 400
    assert client.patch(detail(world.team), {"manager": str(manager_id)}, format="json").status_code == 400
    world.team.refresh_from_db()
    assert world.team.manager_id == world.manager.pk


def test_manager_assign_replace_remove_and_multiplicity_preserved(world):
    client = client_for(world.agency)
    for manager in [world.other_manager, None, world.manager]:
        response = client.patch(detail(world.team), {"manager": str(manager.pk) if manager else None}, format="json")
        assert response.status_code == 200
        world.team.refresh_from_db()
        assert world.team.manager_id == (manager.pk if manager else None)
    response = client.post(URL, {"name": "مدیر مشترک", "manager": str(world.manager.pk)}, format="json")
    assert response.status_code == 201
    assert Range.objects.filter(manager=world.manager).count() == 2
    # Existing user-management guard still fails closed for ambiguous active ranges.
    assert client_for(world.manager).post("/api/v1/users/", {
        "username": "new", "password": "Unique-test-Password-826!", "role": "consultant",
    }, format="json").status_code == 400


def test_name_uniqueness_scoped_to_workspace(world):
    client = client_for(world.agency)
    assert client.post(URL, {"name": world.team.name}, format="json").status_code == 400
    assert client.post(URL, {"name": world.foreign.name}, format="json").status_code == 201
    assert client.patch(detail(world.team), {"name": world.other.name}, format="json").status_code == 400


@pytest.mark.parametrize("field", ["workspace", "workspace_id", "id", "consultants", "is_workspace_owner", "source"])
def test_range_payload_allowlist(world, field):
    client = client_for(world.agency)
    data = {field: str(world.foreign_workspace.pk)}
    assert client.post(URL, {"name": "جدید", **data}, format="json").status_code == 400
    assert client.patch(detail(world.team), data, format="json").status_code == 400


def test_header_and_query_do_not_override_workspace(world):
    response = client_for(world.agency).post(URL + "?workspace=" + str(world.foreign_workspace.pk),
        {"name": "جدید"}, format="json", HTTP_X_WORKSPACE_SLUG=world.foreign_workspace.slug)
    assert response.status_code == 201
    assert Range.objects.get(pk=response.data["id"]).workspace_id == world.workspace.pk


@pytest.mark.parametrize("actor_name", ["manager", "consultant", "secretary", "admin", "owner"])
@pytest.mark.parametrize("operation", ["create", "manager", "regions", "status", "name"])
def test_non_agency_cannot_change_structure(world, actor_name, operation):
    actor = getattr(world, actor_name)
    client = client_for(actor)
    if operation == "create":
        response = client.post(URL, {"name": "جدید"}, format="json")
    else:
        data = {"manager": None} if operation == "manager" else {"regions": []} if operation == "regions" else {"is_active": False} if operation == "status" else {"name": "تغییر"}
        response = client.patch(detail(world.team), data, format="json")
    assert response.status_code == 403


@pytest.mark.parametrize("role", User.Role.values)
def test_owner_can_read_all_structure_but_role_controls_writes(world, role):
    owner = world.make(role, is_workspace_owner=True)
    client = client_for(owner)
    response = client.get(URL)
    assert response.status_code == 200
    assert {row["id"] for row in response.data["results"]} == {str(world.team.pk), str(world.other.pk)}
    assert client.get(members(world.team)).status_code == 200
    response = client.patch(detail(world.team), {"name": "تغییر"}, format="json")
    assert response.status_code == (200 if role == "agency_manager" else 403)


@pytest.mark.parametrize("actor_name,expected", [("agency", "all"), ("manager", "own"), ("consultant", "own"), ("unassigned", "none"), ("secretary", "denied"), ("admin", "denied")])
def test_range_read_scope(world, actor_name, expected):
    client = client_for(getattr(world, actor_name))
    response = client.get(URL)
    if expected == "denied":
        assert response.status_code == 403
        return
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert ids == ({str(world.team.pk), str(world.other.pk)} if expected == "all" else {str(world.team.pk)} if expected == "own" else set())
    assert client.get(detail(world.other)).status_code == (200 if expected == "all" else 404)


@pytest.mark.parametrize("operation", ["get", "patch", "members", "assign", "remove"])
def test_foreign_range_is_indistinguishable_from_missing(world, operation):
    client = client_for(world.agency)
    missing = SimpleNamespace(pk=uuid.uuid4())
    def call(team):
        if operation == "get":
            return client.get(detail(team))
        if operation == "patch":
            return client.patch(detail(team), {"name": "تغییر"}, format="json")
        if operation == "members":
            return client.get(members(team))
        if operation == "assign":
            return client.put(membership(team, world.unassigned), {}, format="json")
        return client.delete(membership(team, world.consultant))
    foreign, absent = call(world.foreign), call(missing)
    assert foreign.status_code == absent.status_code == 404 and foreign.data == absent.data


def test_region_constraints_add_retain_inactive_remove(world):
    client = client_for(world.agency)
    response = client.patch(detail(world.team), {"regions": [str(world.region.pk), str(world.second_region.pk)]}, format="json")
    assert response.status_code == 200 and len(response.data["regions"]) == 2
    Region.objects.filter(pk=world.region.pk).update(is_active=False)
    response = client.patch(detail(world.team), {"regions": [str(world.region.pk)]}, format="json")
    assert response.status_code == 200 and response.data["regions"][0]["is_active"] is False
    assert client_for(world.manager).get(detail(world.team)).data["regions"][0]["is_active"] is False
    response = client_for(world.consultant).get(detail(world.team))
    assert response.data["regions"] == [] and response.data["has_region_constraints"] is True
    assert client.patch(detail(world.team), {"regions": []}, format="json").data["has_region_constraints"] is False
    assert client.patch(detail(world.team), {"regions": [str(world.region.pk)]}, format="json").status_code == 400


@pytest.mark.parametrize("state", ["foreign", "inactive", "inactive_city", "missing", "bad"])
def test_invalid_new_region_assignment_rolls_back(world, state):
    region_id = world.region.pk
    if state == "foreign":
        region_id = world.foreign_region.pk
    elif state == "inactive":
        Region.objects.filter(pk=region_id).update(is_active=False)
    elif state == "inactive_city":
        City.objects.filter(pk=world.city.pk).update(is_active=False)
    elif state == "missing":
        region_id = uuid.uuid4()
    else:
        region_id = "bad"
    client = client_for(world.agency)
    data = {"name": "تغییر", "regions": [str(region_id)]}
    assert client.post(URL, data, format="json").status_code == 400
    assert client.patch(detail(world.team), data, format="json").status_code == 400
    world.team.refresh_from_db()
    assert world.team.name == "اول" and not world.team.regions.exists()


def test_deactivation_preserves_manager_members_and_regions_no_hard_delete(world):
    world.team.regions.add(world.region)
    client = client_for(world.agency)
    for active in [False, True]:
        response = client.patch(detail(world.team), {"is_active": active}, format="json")
        assert response.status_code == 200 and response.data["is_active"] is active
        assert world.team.memberships.count() == world.team.regions.count() == 1
        assert response.data["manager"]["id"] == str(world.manager.pk)
    assert client.delete(detail(world.team)).status_code == 405
    assert client.put(detail(world.team), {}, format="json").status_code == 405


@pytest.mark.parametrize("actor_name", ["agency", "manager"])
def test_add_remove_consultant_and_idempotent_assignment(world, actor_name):
    client = client_for(getattr(world, actor_name))
    path = membership(world.team, world.unassigned)
    for _ in range(2):
        assert client.put(path, {}, format="json").status_code == 204
    assert RangeMembership.objects.filter(user=world.unassigned).count() == 1
    assert client.delete(path).status_code == 204
    assert not RangeMembership.objects.filter(user=world.unassigned).exists()
    assert User.objects.filter(pk=world.unassigned.pk).exists()


def test_agency_move_requires_explicit_matching_source(world):
    client = client_for(world.agency)
    path = membership(world.other, world.consultant)
    for data in [{}, {"from_range": None}, {"from_range": str(world.foreign.pk)}]:
        assert client.put(path, data, format="json").status_code == 400
        assert RangeMembership.objects.get(user=world.consultant).range_id == world.team.pk
    response = client.put(path, {"from_range": str(world.team.pk)}, format="json")
    assert response.status_code == 204
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.other.pk
    assert client_for(world.manager).get("/api/v1/users/" + str(world.consultant.pk) + "/").status_code == 404


def test_range_manager_cannot_take_or_move_another_ranges_consultants(world):
    client = client_for(world.manager)
    assert client.put(membership(world.other, world.consultant), {}, format="json").status_code == 404
    assert client.put(membership(world.team, world.unrelated), {}, format="json").status_code == 404
    assert client.delete(membership(world.team, world.unrelated)).status_code == 404
    assert client.put(membership(world.team, world.unassigned), {"from_range": None}, format="json").status_code == 400
    assert client.delete(membership(world.other, world.unrelated)).status_code == 404


@pytest.mark.parametrize("target_name", ["agency", "manager", "secretary", "admin"])
def test_membership_requires_consultant_role(world, target_name):
    response = client_for(world.agency).put(membership(world.team, getattr(world, target_name)), {}, format="json")
    assert response.status_code == 400


@pytest.mark.parametrize("actor_name", ["agency", "manager"])
def test_membership_foreign_consultant_is_not_found(world, actor_name):
    client = client_for(getattr(world, actor_name))
    missing = SimpleNamespace(pk=uuid.uuid4())
    for method in [client.put, client.delete]:
        foreign = method(membership(world.team, world.foreign_user), {}, format="json")
        absent = method(membership(world.team, missing), {}, format="json")
        assert foreign.status_code == absent.status_code == 404 and foreign.data == absent.data


@pytest.mark.parametrize("actor_name", ["consultant", "secretary", "admin", "owner"])
def test_membership_writes_not_granted_by_ownership_or_django_flags(world, actor_name):
    actor = getattr(world, actor_name)
    User.objects.filter(pk=actor.pk).update(is_staff=True, is_superuser=True)
    client = client_for(actor)
    assert client.put(membership(world.team, world.unassigned), {}, format="json").status_code == 403
    assert client.delete(membership(world.team, world.consultant)).status_code == 403


@pytest.mark.parametrize("actor_name", ["agency", "manager"])
def test_owner_consultant_is_protected_from_membership_changes(world, actor_name):
    client = client_for(getattr(world, actor_name))
    assert client.put(membership(world.team, world.owner), {}, format="json").status_code == 403
    RangeMembership.objects.create(workspace=world.workspace, range=world.team, user=world.owner)
    assert client.delete(membership(world.team, world.owner)).status_code == 403


@pytest.mark.parametrize("state", ["inactive", "multiple", "no_range"])
def test_manager_membership_writes_require_one_valid_active_range(world, state):
    if state == "inactive":
        Range.objects.filter(pk=world.team.pk).update(is_active=False)
    elif state == "multiple":
        Range.objects.filter(pk=world.other.pk).update(manager=world.manager)
    else:
        Range.objects.filter(pk=world.team.pk).update(manager=None)
    response = client_for(world.manager).put(membership(world.team, world.unassigned), {}, format="json")
    assert response.status_code == (404 if state == "no_range" else 400)
    assert not RangeMembership.objects.filter(user=world.unassigned).exists()


def test_roster_and_counts_are_scoped_and_allowlisted(world):
    client = client_for(world.manager)
    response = client.get(members(world.team))
    assert response.status_code == 200
    assert response.data["results"] == [{"id": str(world.consultant.pk), "username": world.consultant.username,
                                         "first_name": "", "last_name": ""}]
    assert client.get(members(world.other)).status_code == 404
    assert client_for(world.consultant).get(members(world.team)).status_code == 403
    RangeMembership.objects.create(workspace=world.workspace, range=world.team, user=world.unassigned)
    for actor in [world.agency, world.manager, world.consultant]:
        assert client_for(actor).get(detail(world.team)).data["consultant_count"] == 2


@pytest.mark.parametrize("field", ["workspace", "workspace_id", "range", "role", "user", "is_workspace_owner"])
def test_membership_payload_cannot_override_authority(world, field):
    client = client_for(world.agency)
    data = {field: str(world.foreign_workspace.pk)}
    assert client.put(membership(world.team, world.unassigned), data, format="json").status_code == 400
    assert client.delete(membership(world.team, world.consultant), data, format="json").status_code == 400


def test_transaction_rolls_back_range_when_m2m_write_fails(world):
    with patch("apps.ranges.signals.validate_region_assignment_workspace", side_effect=ModelValidationError({"regions": "invalid"})):
        with pytest.raises(ValidationError):
            save_range(actor=world.agency, range_id=world.team.pk, data={"name": "تغییر", "regions": [world.region.pk]})
    world.team.refresh_from_db()
    assert world.team.name == "اول" and not world.team.regions.exists()


def test_membership_move_is_atomic(world):
    original_save = RangeMembership.save
    def fail_after_save(instance, *args, **kwargs):
        original_save(instance, *args, **kwargs)
        raise RuntimeError("simulated downstream failure")
    with patch.object(RangeMembership, "save", fail_after_save):
        with pytest.raises(RuntimeError):
            assign_consultant(actor=world.agency, range_id=world.other.pk, user_id=world.consultant.pk,
                              data={"from_range": world.team.pk})
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.team.pk


def test_services_recheck_current_role_and_scope(world):
    with pytest.raises(NotFound):
        save_range(actor=world.agency, range_id=world.foreign.pk, data={"name": "تغییر"})
    with pytest.raises(ValidationError):
        save_range(actor=world.agency, data={"name": "جدید", "workspace": world.foreign_workspace.pk})
    User.objects.filter(pk=world.agency.pk).update(role="consultant")
    with pytest.raises(PermissionDenied):
        save_range(actor=world.agency, data={"name": "جدید"})


@pytest.mark.parametrize("state", ["inactive_user", "inactive_workspace", "workspace_less"])
def test_current_customer_eligibility_required(world, state):
    client = client_for(world.agency)
    if state == "inactive_user":
        User.objects.filter(pk=world.agency.pk).update(is_active=False)
    elif state == "inactive_workspace":
        Workspace.objects.filter(pk=world.workspace.pk).update(is_active=False)
    else:
        User.objects.filter(pk=world.agency.pk).update(workspace=None)
    assert client.get(URL).status_code == 401
    assert client.put(membership(world.team, world.unassigned), {}, format="json").status_code == 401


def test_anonymous_denied(world):
    client = APIClient()
    for path in [URL, detail(world.team), members(world.team)]:
        assert client.get(path).status_code == 401
    assert client.put(membership(world.team, world.unassigned), {}, format="json").status_code == 401


@pytest.mark.parametrize("actor_name", ["agency", "manager", "consultant"])
def test_range_list_queries_do_not_grow_with_relations(world, actor_name):
    world.team.regions.add(world.region)
    client = client_for(getattr(world, actor_name))
    with CaptureQueriesContext(connection) as initial:
        assert client.get(URL).status_code == 200
    for i in range(8):
        team = Range.objects.create(workspace=world.workspace, name=f"رنج {i}", manager=world.manager)
        team.regions.add(world.region, world.second_region)
        RangeMembership.objects.create(workspace=world.workspace, range=world.team, user=world.make())
    with CaptureQueriesContext(connection) as expanded:
        assert client.get(URL).status_code == 200
    assert len(initial) == len(expanded) == 4


@pytest.mark.parametrize("actor_name", ["agency", "manager"])
def test_detail_and_roster_query_counts(world, actor_name):
    world.team.regions.add(world.region)
    client = client_for(getattr(world, actor_name))
    with CaptureQueriesContext(connection) as detail_queries:
        assert client.get(detail(world.team)).status_code == 200
    with CaptureQueriesContext(connection) as roster_queries:
        assert client.get(members(world.team)).status_code == 200
    assert len(detail_queries) == 3 and len(roster_queries) == 4


def test_lists_and_rosters_are_paginated(world):
    Range.objects.bulk_create([Range(workspace=world.workspace, name=f"رنج {i}") for i in range(51)])
    for _ in range(51):
        RangeMembership.objects.create(workspace=world.workspace, range=world.team, user=world.make())
    client = client_for(world.agency)
    for url in [URL, members(world.team)]:
        first, second = client.get(url).data, client.get(url, {"page": 2}).data
        assert len(first["results"]) == 50
        ids = [row["id"] for row in first["results"] + second["results"]]
        assert len(ids) == len(set(ids)) == first["count"]


def test_inactive_assigned_range_is_readable_but_not_membership_writable(world):
    Range.objects.filter(pk=world.team.pk).update(is_active=False)
    for actor in [world.agency, world.owner, world.manager, world.consultant]:
        response = client_for(actor).get(detail(world.team))
        assert response.status_code == 200 and response.data["is_active"] is False
    assert client_for(world.agency).put(membership(world.team, world.unassigned), {}, format="json").status_code == 400
    # Agency can clean up an inactive range, without deleting the consultant.
    assert client_for(world.agency).delete(membership(world.team, world.consultant)).status_code == 204
    assert User.objects.filter(pk=world.consultant.pk).exists()


def test_range_manager_owner_read_scope_does_not_expand_membership_write_scope(world):
    User.objects.filter(pk=world.manager.pk).update(is_workspace_owner=True)
    client = client_for(world.manager)
    assert client.get(detail(world.other)).status_code == 200
    assert client.get(members(world.other)).status_code == 200
    assert client.put(membership(world.other, world.unassigned), {}, format="json").status_code == 404
    assert client.delete(membership(world.other, world.unrelated)).status_code == 404


def test_manager_reassignment_revokes_old_manager_scope_immediately(world):
    old_client = client_for(world.manager)
    client_for(world.agency).patch(detail(world.team), {"manager": None}, format="json")
    assert old_client.get(detail(world.team)).status_code == 404
    assert old_client.get(members(world.team)).status_code == 404
    assert old_client.put(membership(world.team, world.unassigned), {}, format="json").status_code == 404


def test_external_region_can_be_linked_without_mutating_external_identity(world):
    world.region.source = "divar"
    world.region.external_id = "source-id"
    world.region.save()
    response = client_for(world.agency).patch(detail(world.team), {"regions": [str(world.region.pk)]}, format="json")
    assert response.status_code == 200
    assert set(response.data["regions"][0]) == {"id", "name", "is_active"}
    world.region.refresh_from_db()
    assert world.region.source == "divar" and world.region.external_id == "source-id"


def test_existing_schema_allows_inactive_manager_without_activating_user(world):
    User.objects.filter(pk=world.manager.pk).update(is_active=False)
    response = client_for(world.agency).post(URL, {"name": "جدید", "manager": str(world.manager.pk)}, format="json")
    assert response.status_code == 201
    world.manager.refresh_from_db()
    assert not world.manager.is_active


def test_form_default_and_invalid_name_validation(world):
    client = client_for(world.agency)
    assert client.post(URL, {"name": "جدید"}).data["is_active"] is True
    assert client.post(URL, {}, format="json").status_code == 400
    for name in ["", " ", "x" * 101]:
        assert client.patch(detail(world.team), {"name": name}, format="json").status_code == 400
    with pytest.raises(ValidationError):
        save_range(actor=world.agency, range_id=world.team.pk, data={"name": ""})


def test_agency_cannot_remove_membership_from_wrong_range(world):
    with pytest.raises(NotFound):
        remove_consultant(actor=world.agency, range_id=world.other.pk, user_id=world.consultant.pk, data={})
    assert RangeMembership.objects.get(user=world.consultant).range_id == world.team.pk


def test_duplicate_region_ids_are_idempotent(world):
    response = client_for(world.agency).patch(detail(world.team),
        {"regions": [str(world.region.pk), str(world.region.pk)]}, format="json")
    assert response.status_code == 200 and len(response.data["regions"]) == 1


def test_inconsistent_legacy_relations_never_leak_foreign_identity(world):
    # Direct database writes deliberately bypass model guards to simulate legacy corruption.
    Range.objects.filter(pk=world.team.pk).update(manager=world.foreign_manager)
    RangeMembership.objects.filter(user=world.consultant).update(workspace=world.foreign_workspace)
    world.team.regions.through.objects.create(range=world.team, region=world.foreign_region)
    response = client_for(world.agency).get(detail(world.team))
    assert response.status_code == 200
    assert response.data["manager"] is None and response.data["regions"] == []
    assert response.data["consultant_count"] == 0
    assert client_for(world.agency).get(members(world.team)).data["results"] == []
    assert client_for(world.agency).put(membership(world.team, world.consultant), {}, format="json").status_code == 404
