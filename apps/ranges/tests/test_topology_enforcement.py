from uuid import uuid4
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.services import create_organizational_user
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.models import Workspace
from apps.organizations.topology import validate_workspace_topology
from apps.ranges.models import Range, RangeMembership
from apps.ranges.services import save_range, expand_single_team, replace_agency_authority
from .test_api import world, client_for, detail, membership, URL

pytestmark = pytest.mark.django_db
EXPAND = URL + "expand/"
REPLACE = URL + "replace-agency-manager/"


def single(world):
    Range.objects.filter(pk=world.other.pk).update(is_active=False)
    return world.manager


def user_path(user):
    return f"/api/v1/users/{user.pk}/"


def expansion(world):
    return {"agency_manager_id": str(world.agency.pk),
            "new_range_manager_id": str(world.other_manager.pk), "new_range_name": "گسترش"}


def valid(world, user):
    assert validate_workspace_topology(world.workspace).status == "valid"
    assert resolve_workspace_authority(world.workspace).authority_user.pk == user.pk


@pytest.mark.parametrize("role", ["consultant", "secretary", "admin", "range_manager"])
def test_non_topology_user_activity(world, role):
    target = world.make(role)
    if role == "range_manager":
        Range.objects.create(workspace=world.workspace, name="تاریخی", manager=target, is_active=False)
    client = client_for(world.agency)
    for active in (False, True):
        response = client.patch(user_path(target), {"is_active": active}, format="json")
        assert response.status_code == 200, response.data
        target.refresh_from_db()
        assert target.is_active == active
    valid(world, world.agency)


def test_active_manager_deactivation_rejected_atomically(world):
    response = client_for(world.agency).patch(user_path(world.manager),
        {"is_active": False, "first_name": "تغییر"}, format="json")
    assert response.status_code == 400 and "is_active" in response.data
    world.manager.refresh_from_db()
    assert world.manager.is_active and world.manager.first_name == ""
    valid(world, world.agency)


def test_reactivation_is_not_legacy_repair(world):
    # Local consultant scope survives invalid global topology; activity must not
    # repair a legacy active Range FK even when the referenced role is Consultant.
    User.objects.filter(pk=world.consultant.pk).update(is_active=False)
    Range.objects.filter(pk=world.other.pk).update(manager=world.consultant)
    response = client_for(world.manager).patch(user_path(world.consultant), {"is_active": True}, format="json")
    assert response.status_code == 400 and "is_active" in response.data
    world.consultant.refresh_from_db()
    assert not world.consultant.is_active


def test_agency_activity_remains_protected(world):
    single(world)
    response = client_for(world.manager).patch(user_path(world.agency), {"is_active": False}, format="json")
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["consultant", "secretary", "admin", "range_manager"])
def test_unassigned_user_creation_unchanged(world, role):
    response = client_for(world.agency).post("/api/v1/users/", {
        "username": "new", "password": "Unique-Password-839!", "role": role,
    }, format="json")
    assert response.status_code == 201, response.data
    valid(world, world.agency)


def test_user_creation_cannot_fill_active_range_manager(world):
    # Guard remains explicit even if a future caller relaxes an authority gate.
    with patch("apps.accounts.services.lock_actor", return_value=world.agency), \
         patch("apps.accounts.services.require_creatable_role"), \
         patch("apps.accounts.services.resolve_assignment", return_value=world.team):
        with pytest.raises(ValidationError):
            create_organizational_user(actor=world.agency, data={"role": "range_manager"})


def test_inactive_preparation_and_clear_are_ordinary(world):
    client = client_for(world.agency)
    response = client.post(URL, {"name": "آماده", "is_active": False, "manager": str(world.manager.pk)}, format="json")
    assert response.status_code == 201
    row = Range.objects.get(pk=response.data["id"])
    assert client.patch(detail(row), {"manager": None}, format="json").status_code == 200
    valid(world, world.agency)


@pytest.mark.parametrize("state,status", [("single", 400), ("zero", 403), ("invalid", 403), ("null", 403)])
def test_active_create_requires_valid_multi(world, state, status):
    actor = world.agency
    if state == "single":
        actor = single(world)
    elif state == "zero":
        Range.objects.filter(workspace=world.workspace).update(is_active=False)
    elif state == "invalid":
        Range.objects.filter(pk=world.other.pk).update(manager=None)
    else:
        Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode=None)
    before = Range.objects.count()
    response = client_for(actor).post(URL, {"name": "جدید", "manager": str(world.make("range_manager").pk)}, format="json")
    assert response.status_code == status
    assert Range.objects.count() == before


def test_multi_active_create_success(world):
    manager = world.make("range_manager")
    response = client_for(world.agency).post(URL, {"name": "جدید", "manager": str(manager.pk),
        "regions": [str(world.region.pk)]}, format="json")
    assert response.status_code == 201, response.data
    assert Range.objects.get(pk=response.data["id"]).regions.filter(pk=world.region.pk).exists()
    valid(world, world.agency)


@pytest.mark.parametrize("operation", ["create", "manager", "deactivate", "activate", "expand"])
def test_companion_failure_rolls_back(world, operation):
    manager = world.make("range_manager")
    if operation == "expand":
        actor = single(world)
    else:
        actor = world.agency
    prepared = Range.objects.create(workspace=world.workspace, name="آماده", manager=manager, is_active=False)
    before = list(Range.objects.order_by("pk").values())
    client = client_for(actor)
    if operation in ("create", "expand"):
        data = expansion(world) if operation == "expand" else {"name": "جدید", "manager": str(manager.pk)}
        response = client.post(EXPAND if operation == "expand" else URL,
                               {**data, "regions": [str(world.foreign_region.pk)]}, format="json")
    else:
        changes = {"manager": str(manager.pk)} if operation == "manager" else {"is_active": operation == "activate"}
        response = client.patch(detail(prepared if operation == "activate" else world.team),
                                {**changes, "regions": [str(world.foreign_region.pk)]}, format="json")
    assert response.status_code == 400
    assert list(Range.objects.order_by("pk").values()) == before
    valid(world, actor)


@pytest.mark.parametrize("multi", [False, True])
def test_manager_replacement_and_noop(world, multi):
    actor = world.agency if multi else single(world)
    manager = world.make("range_manager")
    client = client_for(actor)
    assert client.patch(detail(world.team), {"manager": str(world.manager.pk)}, format="json").status_code == 200
    response = client.patch(detail(world.team), {"manager": str(manager.pk), "name": "تغییر"}, format="json")
    assert response.status_code == 200, response.data
    valid(world, world.agency if multi else manager)
    if not multi:
        assert client.patch(detail(world.team), {"name": "دیگر"}, format="json").status_code == 403


@pytest.mark.parametrize("candidate", ["missing", "foreign", "inactive", "role", "assigned", "null"])
def test_active_manager_invalid(world, candidate):
    value = None
    if candidate == "missing":
        value = uuid4()
    elif candidate == "foreign":
        value = world.foreign_manager.pk
    elif candidate == "inactive":
        value = world.make("range_manager", is_active=False).pk
    elif candidate == "role":
        value = world.consultant.pk
    elif candidate == "assigned":
        value = world.other_manager.pk
    response = client_for(world.agency).patch(detail(world.team), {"manager": str(value) if value else None}, format="json")
    assert response.status_code == 400 and "manager" in response.data
    world.team.refresh_from_db()
    assert world.team.manager_id == world.manager.pk


@pytest.mark.parametrize("active,manager_null", [(True, False), (True, True), (False, False), (False, True)])
def test_multiple_topology_intents_rejected(world, active, manager_null):
    row = world.team if active else Range.objects.create(workspace=world.workspace, name="آماده", manager=world.make("range_manager"), is_active=False)
    old = row.manager_id
    response = client_for(world.agency).patch(detail(row), {"is_active": not active,
        "manager": None if manager_null else str(world.make("range_manager").pk)}, format="json")
    assert response.status_code == 400 and "non_field_errors" in response.data
    row.refresh_from_db()
    assert row.is_active == active and row.manager_id == old


def test_last_range_rejected(world):
    actor = single(world)
    response = client_for(actor).patch(detail(world.team), {"is_active": False}, format="json")
    assert response.status_code == 400 and "is_active" in response.data
    valid(world, actor)


@pytest.mark.parametrize("three", [False, True])
def test_deactivation_handoff_history(world, three):
    if three:
        Range.objects.create(workspace=world.workspace, name="سوم", manager=world.make("range_manager"))
    world.team.regions.add(world.region)
    response = client_for(world.agency).patch(detail(world.team), {"is_active": False, "name": "غیرفعال"}, format="json")
    assert response.status_code == 200, response.data
    world.team.refresh_from_db()
    assert not world.team.is_active and world.team.manager_id == world.manager.pk
    assert world.team.regions.filter(pk=world.region.pk).exists()
    assert RangeMembership.objects.filter(range=world.team, user=world.consultant).exists()
    valid(world, world.agency if three else world.other_manager)


@pytest.mark.parametrize("state,expected", [("multi", 200), ("single", 400), ("zero", 403), ("invalid", 403), ("bad_manager", 400)])
def test_activation(world, state, expected):
    row = Range.objects.create(workspace=world.workspace, name="آماده", manager=world.make("range_manager"), is_active=False)
    actor = world.agency
    if state == "single":
        actor = single(world)
    elif state == "zero":
        Range.objects.filter(workspace=world.workspace).update(is_active=False)
    elif state == "invalid":
        Range.objects.filter(pk=world.team.pk).update(manager=None)
    elif state == "bad_manager":
        User.objects.filter(pk=row.manager_id).update(is_active=False)
    response = client_for(actor).patch(detail(row), {"is_active": True}, format="json")
    assert response.status_code == expected, response.data
    row.refresh_from_db()
    assert row.is_active == (expected == 200)
    if expected == 200:
        valid(world, world.agency)


def test_explicit_expansion_and_handoff(world):
    actor = single(world)
    response = client_for(actor).post(EXPAND, {**expansion(world), "regions": [str(world.region.pk)]}, format="json")
    assert response.status_code == 201, response.data
    valid(world, world.agency)
    assert client_for(actor).post(EXPAND, expansion(world), format="json").status_code == 403


@pytest.mark.parametrize("field", ["agency_manager_id", "new_range_manager_id"])
def test_expansion_candidate_privacy(world, field):
    actor = single(world)
    responses = []
    for candidate in (world.foreign_user.pk, uuid4()):
        response = client_for(actor).post(EXPAND, {**expansion(world), field: str(candidate)}, format="json")
        assert response.status_code == 400
        responses.append(response.data)
    assert responses[0] == responses[1]
    valid(world, actor)


@pytest.mark.parametrize("owner", [False, True])
def test_agency_replacement_and_owner_block(world, owner):
    User.objects.filter(pk=world.agency.pk).update(is_workspace_owner=owner)
    incoming = world.make("agency_manager", is_active=False)
    response = client_for(world.agency).post(REPLACE, {"incoming_agency_manager_id": str(incoming.pk)}, format="json")
    assert response.status_code == (400 if owner else 204), response.data
    world.agency.refresh_from_db()
    incoming.refresh_from_db()
    assert incoming.is_active != owner and world.agency.is_active == owner
    valid(world, world.agency if owner else incoming)


def test_agency_replacement_candidate_privacy(world):
    responses = []
    for candidate in (world.foreign_user.pk, uuid4()):
        response = client_for(world.agency).post(REPLACE, {"incoming_agency_manager_id": str(candidate)}, format="json")
        assert response.status_code == 400
        responses.append(response.data)
    assert responses[0] == responses[1]


@pytest.mark.parametrize("action", ["create", "expand", "replace"])
def test_privilege_flags_do_not_grant_structure_authority(world, action):
    User.objects.filter(pk=world.manager.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    Workspace.objects.filter(pk=world.workspace.pk).update(customer_type="range_manager")
    path, data = (URL, {"name": "آماده", "is_active": False}) if action == "create" else (
        (EXPAND, expansion(world)) if action == "expand" else (REPLACE, {"incoming_agency_manager_id": str(world.make("agency_manager", is_active=False).pk)}))
    assert client_for(world.manager).post(path, data, format="json").status_code == 403


@pytest.mark.parametrize("state", ["null", "invalid"])
def test_local_neutral_operations_survive_global_invalidity(world, state):
    if state == "null":
        Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode=None)
    else:
        User.objects.filter(pk=world.agency.pk).update(is_active=False)
    client = client_for(world.manager)
    assert client.get(detail(world.team)).status_code == 200
    assert client.put(membership(world.team, world.unassigned), {}, format="json").status_code == 204
    assert client.patch(user_path(world.consultant), {"first_name": "جدید"}, format="json").status_code == 200
    assert client.delete(membership(world.team, world.unassigned), {}, format="json").status_code == 204
    assert client.post("/api/v1/users/", {"username": "local", "password": "Strong-Password-879!", "role": "consultant"}, format="json").status_code == 201


def test_foreign_path_non_disclosure(world):
    client = client_for(world.agency)
    foreign = client.patch(detail(world.foreign), {"manager": None}, format="json")
    missing = client.patch(URL + str(uuid4()) + "/", {"manager": None}, format="json")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.data == missing.data


@pytest.mark.parametrize("operation", ["create", "manager", "activate", "deactivate", "expand", "agency"])
def test_structural_lock_order_and_no_old_lock_actor(world, operation):
    manager = world.make("range_manager")
    prepared = Range.objects.create(workspace=world.workspace, name="آماده", manager=manager, is_active=False)
    actor = single(world) if operation == "expand" else world.agency
    incoming = world.make("agency_manager", is_active=False)
    with patch("apps.ranges.services.lock_actor", side_effect=AssertionError("old subordinate locking")), CaptureQueriesContext(connection) as queries:
        if operation == "expand":
            expand_single_team(actor=actor, data={"agency_manager_id": world.agency.pk,
                "new_range_manager_id": world.other_manager.pk, "new_range_name": "گسترش"})
        elif operation == "agency":
            replace_agency_authority(actor=actor, data={"incoming_agency_manager_id": incoming.pk})
        else:
            data = ({"name": "جدید", "manager": manager.pk} if operation == "create" else
                    {"manager": manager.pk} if operation == "manager" else {"is_active": operation == "activate"})
            save_range(actor=actor, data=data, range_id=None if operation == "create" else
                       prepared.pk if operation == "activate" else world.team.pk)
    locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"].upper()]
    first_user = next(i for i, q in enumerate(locks) if "accounts_user" in q)
    first_range = next(i for i, q in enumerate(locks) if "ranges_range" in q)
    assert all("organizations_workspace" in q for q in locks[:first_user])
    assert first_user < first_range
    assert "ORDER BY" in locks[first_user] and "ORDER BY" in locks[first_range]


def test_callbacks_cancel_on_failed_companion(world, django_capture_on_commit_callbacks):
    manager = world.make("range_manager")
    original = Range.save
    def save_with_callback(row, *args, **kwargs):
        original(row, *args, **kwargs)
        transaction.on_commit(lambda: pytest.fail("callback survived rollback"))
    with django_capture_on_commit_callbacks(execute=True) as callbacks, patch.object(Range, "save", save_with_callback):
        with pytest.raises(ValidationError):
            save_range(actor=world.agency, range_id=world.team.pk,
                       data={"manager": manager.pk, "regions": [world.foreign_region.pk]})
    assert callbacks == []
    world.team.refresh_from_db()
    assert world.team.manager_id == world.manager.pk


@pytest.mark.parametrize("path", [EXPAND, REPLACE])
def test_explicit_payloads_strict(world, path):
    data = expansion(world) if path == EXPAND else {"incoming_agency_manager_id": str(world.agency.pk)}
    response = client_for(world.agency).post(path, {**data, "workspace_mode": "team"}, format="json")
    assert response.status_code == 400 and "workspace_mode" in response.data
