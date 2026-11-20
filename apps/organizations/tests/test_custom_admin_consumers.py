"""CA2 consumer boundaries; execution belongs to the external verification flow."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.policies import require_user_manager, visible_users
from apps.organizations.models import Workspace, CustomAdminGrant
from apps.organizations.tests.test_authority import setup
from apps.locations.models import City, Region
from apps.locations.services import set_region_mode
from apps.ranges.models import RangeMembership
from apps.ranges.services import save_range

pytestmark = pytest.mark.django_db


def client(actor):
    api = APIClient()
    api.force_authenticate(actor)
    return api


@pytest.fixture
def world(setup):
    ws, user, team = setup
    authority = user("agency_manager")
    manager = user()
    own = team(manager)
    other = team(user())
    member = user("consultant")
    RangeMembership.objects.create(workspace=ws, range=own, user=member)
    return SimpleNamespace(ws=ws, user=user, team=team, authority=authority,
                           manager=manager, own=own, other=other, member=member)


def grant(w, permission, actor=None):
    return CustomAdminGrant.objects.create(workspace=w.ws, user=actor or w.manager,
                                          permission=permission, granted_by=w.authority)


def metadata(w, actor=None, **data):
    return client(actor or w.manager).patch(f"/api/v1/ranges/{w.own.pk}/", data, format="json")


def settings_patch(actor):
    return client(actor).patch("/api/v1/location-settings/", {"region_mode": "custom"}, format="json")


def test_local_user_scope_needs_no_grant_and_never_expands(world):
    w = world
    require_user_manager(w.manager)
    assert w.member in visible_users(w.manager)
    assert w.other.manager not in visible_users(w.manager)
    for permission in ("manage_users", "manage_ranges", "manage_workspace_settings"):
        row = grant(w, permission)
        assert w.other.manager not in visible_users(w.manager)
        row.delete()


@pytest.mark.parametrize("role", ["consultant", "secretary", "admin"])
@pytest.mark.parametrize("permission", ["manage_users", "manage_ranges", "manage_workspace_settings"])
def test_grants_do_not_create_user_management_scope(world, role, permission):
    actor = world.user(role)
    grant(world, permission, actor)
    with pytest.raises(PermissionDenied):
        require_user_manager(actor)


@pytest.mark.parametrize("permission", [None, "manage_users", "manage_workspace_settings"])
def test_exact_range_grant_required(world, permission):
    if permission:
        grant(world, permission)
    assert metadata(world, name="Denied").status_code == 403


def test_own_metadata_and_revocation(world):
    w = world
    row = grant(w, "manage_ranges")
    city = City.objects.create(workspace=w.ws, name="City")
    region = Region.objects.create(workspace=w.ws, city=city, name="Region")
    assert metadata(w, name="Renamed", activity_scope="sale", regions=[str(region.pk)]).status_code == 200
    w.own.refresh_from_db()
    assert (w.own.name, w.own.activity_scope) == ("Renamed", "sale")
    assert list(w.own.regions.all()) == [region]
    assert client(w.manager).patch(f"/api/v1/ranges/{w.other.pk}/", {"name": "No"}, format="json").status_code == 404
    row.delete()
    assert metadata(w, name="Denied").status_code == 403


@pytest.mark.parametrize("data", [{"is_active": False}, {"is_active": True}, {"manager": None}])
def test_structural_intent_even_neutral_is_denied_before_write(world, data):
    grant(world, "manage_ranges")
    assert metadata(world, name="Must rollback", **data).status_code == 403
    world.own.refresh_from_db()
    assert world.own.name != "Must rollback"
    assert world.own.is_active


@pytest.mark.parametrize("path", ["/api/v1/ranges/", "/api/v1/ranges/expand/", "/api/v1/ranges/replace-agency-manager/"])
def test_structural_endpoints_remain_authority_only(world, path):
    grant(world, "manage_ranges")
    assert client(world.manager).post(path, {}, format="json").status_code == 403


@pytest.mark.parametrize("mode", ["personal", None])
def test_modes_deny_both_consumers_despite_grants(world, mode):
    grant(world, "manage_ranges")
    grant(world, "manage_workspace_settings")
    world.ws.workspace_mode = mode
    world.ws.save(update_fields=["workspace_mode"])
    assert metadata(world, name="No").status_code == 403
    assert settings_patch(world.manager).status_code == 403


@pytest.mark.parametrize("permission", [None, "manage_users", "manage_ranges"])
def test_settings_requires_exact_grant(world, permission):
    if permission:
        grant(world, permission)
    assert settings_patch(world.manager).status_code == 403


def test_settings_delegate_not_city_region_or_grant_admin(world):
    w = world
    row = grant(w, "manage_workspace_settings", w.member)
    assert settings_patch(w.member).status_code == 200
    assert client(w.member).get("/api/v1/location-settings/").status_code == 200
    for path in ("/api/v1/cities/", "/api/v1/regions/", "/api/v1/custom-admin/grants/"):
        assert client(w.member).post(path, {}, format="json").status_code == 403
    assert client(w.member).get("/api/v1/custom-admin/grants/").status_code == 403
    assert client(w.member).delete(f"/api/v1/custom-admin/grants/{row.pk}/").status_code == 403
    row.delete()
    assert settings_patch(w.member).status_code == 403


def test_authority_unchanged(world):
    assert metadata(world, world.authority, name="Authority").status_code == 200
    assert settings_patch(world.authority).status_code == 200


def test_flags_do_not_replace_grants(world):
    actor = world.manager
    actor.is_workspace_owner = actor.is_staff = actor.is_superuser = True
    actor.save()
    world.ws.customer_type = "agency_manager"
    world.ws.save(update_fields=["customer_type"])
    assert metadata(world, name="No").status_code == 403
    assert settings_patch(actor).status_code == 403


def test_current_state_and_lost_scope(world):
    w = world
    grant(w, "manage_ranges")
    grant(w, "manage_workspace_settings")
    w.own.is_active = False
    w.own.save()
    assert metadata(w, name="No").status_code == 403
    w.manager.is_active = False
    w.manager.save()
    with pytest.raises(PermissionDenied):
        set_region_mode(actor=w.manager, data={"region_mode": "custom"})


def test_feature_capability_independent(world):
    grant(world, "manage_ranges")
    grant(world, "manage_workspace_settings")
    with patch("apps.ranges.policies.resolve_workspace_capability", return_value=SimpleNamespace(allowed=False)):
        assert metadata(world, name="No").status_code == 403
    with patch("apps.locations.policies.resolve_workspace_capability", return_value=SimpleNamespace(allowed=False)):
        assert settings_patch(world.manager).status_code == 403


def test_foreign_grant_and_target_cannot_redirect(world):
    w = world
    row = grant(w, "manage_workspace_settings")
    foreign = Workspace.objects.create(name="Foreign", slug="foreign", workspace_mode="team")
    # Simulate a legacy malformed grant without legitimizing it via model validation.
    CustomAdminGrant.objects.filter(pk=row.pk).update(workspace=foreign)
    assert settings_patch(w.manager).status_code == 403
    grant(w, "manage_ranges")
    foreign_range = w.team(w.user())
    type(foreign_range).objects.filter(pk=foreign_range.pk).update(workspace=foreign)
    response = client(w.manager).patch(f"/api/v1/ranges/{foreign_range.pk}/", {"name": "No"}, format="json")
    assert response.status_code == 404
    with pytest.raises(ValidationError):
        save_range(actor=w.manager, data={"name": "No", "workspace_id": foreign.pk}, range_id=w.own.pk)


@pytest.mark.parametrize("change", ["workspace_inactive", "user_moved"])
def test_stored_grants_do_not_authorize_changed_tenancy_or_activity(world, change):
    w = world
    grant(w, "manage_ranges")
    grant(w, "manage_workspace_settings")
    if change == "workspace_inactive":
        Workspace.objects.filter(pk=w.ws.pk).update(is_active=False)
    else:
        foreign = Workspace.objects.create(name="Moved", slug="moved", workspace_mode="team")
        type(w.manager).objects.filter(pk=w.manager.pk).update(workspace=foreign)
    # Deliberately pass the stale actor: services must re-read under Workspace lock.
    with pytest.raises(PermissionDenied):
        save_range(actor=w.manager, data={"name": "No"}, range_id=w.own.pk)
    with pytest.raises(PermissionDenied):
        set_region_mode(actor=w.manager, data={"region_mode": "custom"})


def test_range_grant_never_creates_scope_for_consultant(world):
    grant(world, "manage_ranges", world.member)
    assert metadata(world, world.member, name="No").status_code == 403


def test_authority_handoff_does_not_promote_delegate(world):
    from apps.organizations.authority import resolve_workspace_authority
    w = world
    grant(w, "manage_workspace_settings", w.member)
    incoming = w.user("agency_manager", is_active=False)
    from apps.organizations.topology_transitions import replace_multi_team_agency_manager
    replace_multi_team_agency_manager(workspace_id=w.ws.pk, incoming_agency_manager_id=incoming.pk)
    assert resolve_workspace_authority(w.ws).authority_user.pk == incoming.pk
    assert settings_patch(w.member).status_code == 200
    assert client(w.member).get("/api/v1/custom-admin/grants/").status_code == 403
