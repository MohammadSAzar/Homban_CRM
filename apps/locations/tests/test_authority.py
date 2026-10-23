"""Location-only canonical authority and unchanged read boundaries."""
import pytest
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.locations.services import save_city, save_region, set_region_mode
from apps.organizations.models import Workspace
from apps.ranges.models import Range
from .test_api import world, client_for, endpoint, body, MODE

pytestmark = pytest.mark.django_db


def user(world, name, role="range_manager"):
    return User.objects.create_user(username=name, workspace=world.workspace, role=role)


def assert_writes(world, actor, allowed):
    client = client_for(actor)
    for kind, obj in [("cities", world.city), ("regions", world.region)]:
        response = client.post(endpoint(kind), body(world, kind), format="json")
        assert response.status_code == (201 if allowed else 403), response.data
        for active in [False, True]:
            response = client.patch(endpoint(kind, obj), {"is_active": active}, format="json")
            assert response.status_code == (200 if allowed else 403), response.data
    assert client.patch(MODE, {"region_mode": "custom"}, format="json").status_code == (200 if allowed else 403)


@pytest.mark.parametrize("topology", ["single", "multi"])
def test_canonical_authority_all_writes(world, topology):
    agency = user(world, "agency", "agency_manager")
    other = user(world, "other")
    if topology == "multi":
        Range.objects.create(workspace=world.workspace, name="second", manager=other)
    authority = world.actor if topology == "single" else agency
    denied = agency if topology == "single" else world.actor
    assert_writes(world, denied, False)
    assert_writes(world, other, False)
    assert_writes(world, authority, True)


@pytest.mark.parametrize("state", [
    "personal", "null", "zero", "missing", "inactive_manager", "wrong_role",
    "foreign_manager", "no_agency", "multiple_agencies",
])
def test_unresolved_authority_denies_mutations_but_preserves_reads(world, state):
    ranges = Range.objects.filter(workspace=world.workspace)
    if state in ("personal", "null"):
        Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode="personal" if state == "personal" else None)
    elif state == "zero":
        ranges.update(is_active=False)
    elif state == "missing":
        ranges.update(manager=None)
    elif state == "inactive_manager":
        manager = user(world, "inactive")
        User.objects.filter(pk=manager.pk).update(is_active=False)
        ranges.update(manager=manager)
    elif state == "wrong_role":
        User.objects.filter(pk=world.actor.pk).update(role="consultant")
    elif state == "foreign_manager":
        manager = User.objects.create_user(username="foreign", workspace=world.foreign_workspace, role="range_manager")
        ranges.update(manager=manager)
    else:
        Range.objects.create(workspace=world.workspace, name="second", manager=world.actor)
        if state == "multiple_agencies":
            user(world, "agency1", "agency_manager")
            user(world, "agency2", "agency_manager")
    User.objects.filter(pk=world.actor.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    assert_writes(world, world.actor, False)
    client = client_for(world.actor)
    for path in [endpoint("cities"), endpoint("regions"), MODE]:
        assert client.get(path).status_code == 200


@pytest.mark.parametrize("mode", ["personal", None])
@pytest.mark.parametrize("role", ["consultant", "agency_manager", "range_manager"])
def test_personal_and_null_legacy_owner_roles_never_administer(world, mode, role):
    Workspace.objects.filter(pk=world.workspace.pk).update(workspace_mode=mode)
    User.objects.filter(pk=world.actor.pk).update(role=role, is_workspace_owner=True)
    assert_writes(world, world.actor, False)


@pytest.mark.parametrize("customer_type", Workspace.CustomerType.values)
def test_purchaser_metadata_and_privilege_flags_do_not_select_authority(world, customer_type):
    Workspace.objects.filter(pk=world.workspace.pk).update(customer_type=customer_type)
    impostor = user(world, "impostor", "agency_manager")
    User.objects.filter(pk=impostor.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    assert_writes(world, impostor, False)
    assert_writes(world, world.actor, True)


def test_inactive_range_ignored_and_live_topology_transition(world):
    agency = user(world, "agency", "agency_manager")
    second = Range.objects.create(workspace=world.workspace, name="second", manager=world.actor, is_active=False)
    set_region_mode(actor=world.actor, data={"region_mode": "custom"})
    Range.objects.filter(pk=second.pk).update(is_active=True)
    with pytest.raises(PermissionDenied):
        set_region_mode(actor=world.actor, data={"region_mode": "divar"})
    set_region_mode(actor=agency, data={"region_mode": "divar"})
    Range.objects.filter(pk=second.pk).update(is_active=False)
    with pytest.raises(PermissionDenied):
        set_region_mode(actor=agency, data={"region_mode": "custom"})
    set_region_mode(actor=world.actor, data={"region_mode": "custom"})


@pytest.mark.parametrize("state", ["inactive_user", "moved", "inactive_workspace"])
def test_stale_actor_cannot_mutate_through_services(world, state):
    if state == "inactive_user":
        User.objects.filter(pk=world.actor.pk).update(is_active=False)
    elif state == "moved":
        User.objects.filter(pk=world.actor.pk).update(workspace=world.foreign_workspace)
    else:
        Workspace.objects.filter(pk=world.workspace.pk).update(is_active=False)
    for service, data in [
        (save_city, {"name": "new"}),
        (save_region, {"name": "new", "city": world.city.pk}),
        (set_region_mode, {"region_mode": "custom"}),
    ]:
        with pytest.raises(PermissionDenied):
            service(actor=world.actor, data=data)
