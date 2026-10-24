"""C4: global organizational authority is separate from local scope/lifecycle."""
import pytest
from rest_framework.exceptions import PermissionDenied
from apps.accounts.models import User
from apps.accounts.services import update_organizational_user
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.models import Workspace
from apps.ranges.models import Range, RangeMembership
from apps.ranges.tests.test_api import detail as range_detail, members, membership
from .test_user_management import world, client_for, payload, URL, detail

pytestmark = pytest.mark.django_db
RANGES = "/api/v1/ranges/"


def single(world):
    Range.objects.filter(pk=world.other_team.pk).update(is_active=False)
    return world.manager


@pytest.mark.parametrize("topology", ["single", "multi"])
@pytest.mark.parametrize("role", ["range_manager", "consultant", "secretary", "admin"])
def test_authority_creates_global_roles_and_sees_workspace(world, topology, role):
    actor = single(world) if topology == "single" else world.agency
    client = client_for(actor)
    response = client.post(URL, payload(role=role), format="json")
    assert response.status_code == 201, response.data
    assert not RangeMembership.objects.filter(user_id=response.data["id"]).exists()
    assert client.get(detail(world.secretary)).status_code == 200
    assert client.get(URL).data["count"] == User.objects.filter(workspace=world.workspace).count()


@pytest.mark.parametrize("operation", ["name", "manager", "activity_scope", "regions", "is_active"])
def test_single_authority_structural_mutations_return_saved_result(world, operation):
    actor = single(world)
    data = {"name": "renamed", "manager": None, "activity_scope": "sale", "regions": [], "is_active": False}
    response = client_for(actor).patch(range_detail(world.team), {operation: data[operation]}, format="json")
    assert response.status_code == 200, response.data
    if operation in ("manager", "is_active"):
        assert resolve_workspace_authority(world.workspace).status == "invalid"
        assert client_for(actor).patch(range_detail(world.team), {"name": "denied"}, format="json").status_code in (403, 404)


@pytest.mark.parametrize("role", ["agency_manager", "consultant", "secretary", "admin"])
def test_single_non_authority_owner_has_no_global_powers(world, role):
    single(world)
    actor = world.agency if role == "agency_manager" else world.make(role)
    User.objects.filter(pk=actor.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    client = client_for(actor)
    assert client.get(URL).status_code == 403
    assert client.post(URL, payload(), format="json").status_code == 403
    assert client.patch(detail(world.consultant), {"is_active": False}, format="json").status_code == 403
    ranges = client.get(RANGES)
    assert ranges.status_code == (200 if role == "consultant" else 403)
    if role == "consultant":
        assert ranges.data["results"] == []
    assert client.get(members(world.team)).status_code in (403, 404)
    assert client.post(RANGES, {"name": "denied"}, format="json").status_code == 403
    assert client.put(membership(world.team, world.unrelated), {}, format="json").status_code == 403


@pytest.mark.parametrize("state", ["personal", "null", "zero", "missing", "inactive", "wrong_role", "foreign", "no_agency", "multiple_agencies"])
def test_invalid_global_authority_denies_global_operations(world, state):
    if state in ("personal", "null"):
        Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode="personal" if state == "personal" else None)
    elif state == "zero":
        Range.objects.filter(workspace=world.workspace).update(is_active=False)
    elif state == "missing":
        Range.objects.filter(pk=world.team.pk).update(manager=None)
    elif state == "inactive":
        User.objects.filter(pk=world.manager.pk).update(is_active=False)
    elif state == "wrong_role":
        User.objects.filter(pk=world.manager.pk).update(role="consultant")
    elif state == "foreign":
        User.objects.filter(pk=world.manager.pk).update(workspace=world.other_workspace)
    elif state == "no_agency":
        User.objects.filter(pk=world.agency.pk).update(is_active=False)
    else:
        world.make("agency_manager")
    client = client_for(world.agency)
    expected = 401 if state == "no_agency" else 403
    assert client.get(URL).status_code == expected
    assert client.post(RANGES, {"name": "denied"}, format="json").status_code == expected


@pytest.mark.parametrize("state", ["null", "no_agency", "multiple_agencies"])
def test_local_management_survives_invalid_global_topology(world, state):
    if state == "null":
        Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode=None)
    elif state == "no_agency":
        User.objects.filter(pk=world.agency.pk).update(is_active=False)
    else:
        world.make("agency_manager")
    client = client_for(world.manager)
    assert client.get(detail(world.manager)).status_code == 200
    assert client.get(detail(world.consultant)).status_code == 200
    assert client.get(detail(world.unrelated)).status_code == 404
    assert client.post(URL, payload(), format="json").status_code == 201
    assert client.post(URL, payload(username="higher", role="secretary"), format="json").status_code == 400
    assert client.patch(detail(world.consultant), {"first_name": "local"}, format="json").status_code == 200
    assert client.delete(membership(world.team, world.consultant)).status_code == 204
    assert client.put(membership(world.team, world.consultant), {}, format="json").status_code == 204
    assert client.put(membership(world.team, world.unrelated), {}, format="json").status_code == 404
    assert client.patch(range_detail(world.team), {"name": "denied"}, format="json").status_code == 403


@pytest.mark.parametrize("target", ["manager", "owner", "agency"])
def test_single_authority_preserves_protected_targets(world, target):
    actor = single(world)
    with pytest.raises(PermissionDenied):
        update_organizational_user(actor=actor, user_id=getattr(world, target).pk, data={"is_active": False})


@pytest.mark.parametrize("customer_type", Workspace.CustomerType.values)
def test_customer_type_does_not_change_authority(world, customer_type):
    actor = single(world)
    Workspace.objects.filter(pk=world.workspace.pk).update(customer_type=customer_type)
    User.objects.filter(pk=world.agency.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    assert client_for(actor).patch(detail(world.secretary), {"first_name": "ok"}, format="json").status_code == 200
    assert client_for(world.agency).get(URL).status_code == 403


def test_single_global_assignment_move_and_unassignment(world):
    actor = single(world)
    client = client_for(actor)
    # Global authority may move a retained consultant from an inactive Range.
    assert client.patch(detail(world.unrelated), {"range_id": str(world.team.pk)}, format="json").status_code == 200
    assert client.patch(detail(world.unrelated), {"range_id": None}, format="json").status_code == 200
    assert client.put(membership(world.team, world.unrelated), {}, format="json").status_code == 204
    assert client.delete(membership(world.team, world.unrelated)).status_code == 204


def test_dynamic_topology_creation_and_authority_handoff(world):
    actor = single(world)
    User.objects.filter(pk=world.agency.pk).update(is_active=False)
    client = client_for(actor)
    response = client.post(RANGES, {"name": "second active", "manager": str(world.other_manager.pk)}, format="json")
    assert response.status_code == 201, response.data
    assert resolve_workspace_authority(world.workspace).status == "invalid"
    assert client.patch(range_detail(world.team), {"name": "denied"}, format="json").status_code == 403
    User.objects.filter(pk=world.agency.pk).update(is_active=True)
    assert resolve_workspace_authority(world.workspace).authority_user.pk == world.agency.pk
    extra = world.make("agency_manager")
    assert resolve_workspace_authority(world.workspace).status == "invalid"
    User.objects.filter(pk=extra.pk).update(is_active=False)
    Range.objects.filter(pk=response.data["id"]).update(is_active=False)
    assert resolve_workspace_authority(world.workspace).authority_user.pk == actor.pk


@pytest.mark.parametrize("role", ["range_manager", "agency_manager"])
def test_personal_legacy_managers_do_not_get_organizational_surface(world, role):
    Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode="personal")
    actor = world.manager if role == "range_manager" else world.agency
    assert client_for(actor).get(URL).status_code == 403
    assert client_for(actor).get(RANGES).status_code == 403
