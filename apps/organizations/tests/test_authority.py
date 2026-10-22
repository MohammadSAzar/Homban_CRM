from dataclasses import FrozenInstanceError
from itertools import count

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.models import Workspace
from apps.ranges.models import Range

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    workspace = Workspace.objects.create(
        name="مجموعه", slug="authority", customer_type="consultant", workspace_mode="team",
    )
    sequence = count()

    def user(role="range_manager", **kwargs):
        return User.objects.create_user(
            username=f"user-{next(sequence)}", workspace=workspace, role=role, **kwargs,
        )

    def team(manager=None, **kwargs):
        return Range.objects.create(
            name=f"رنج {next(sequence)}", workspace=workspace, manager=manager, **kwargs,
        )

    return workspace, user, team


def assert_unresolved(result, reason, topology="unresolved"):
    assert result.status == "invalid"
    assert result.authority_user is None
    assert result.reason == reason
    assert result.topology == topology


@pytest.mark.parametrize("legacy", ["consultant", "owner", "range_manager", "agency_manager", "range"])
def test_personal_never_has_organizational_authority(setup, legacy):
    workspace, user, team = setup
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode="personal")
    member = user("consultant" if legacy == "owner" else "range_manager" if legacy == "range" else legacy,
                  is_workspace_owner=legacy == "owner")
    if legacy == "range":
        team(member)
    result = resolve_workspace_authority(workspace)
    assert (result.status, result.topology, result.authority_user) == ("none", "personal", None)


@pytest.mark.parametrize("purchaser", Workspace.CustomerType.values)
def test_null_has_no_purchaser_owner_or_manager_fallback(setup, purchaser):
    workspace, user, team = setup
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None, customer_type=purchaser)
    team(user(is_workspace_owner=True, is_staff=True, is_superuser=True))
    user("agency_manager")
    assert_unresolved(resolve_workspace_authority(workspace), "unresolved_mode")


@pytest.mark.parametrize("agency_count", [0, 1, 2])
def test_single_range_manager_is_authority_despite_other_managers(setup, agency_count):
    workspace, user, team = setup
    manager = user()
    team(manager)
    user()  # Unassigned Range Manager is not authority.
    for _ in range(agency_count):
        user("agency_manager")
    result = resolve_workspace_authority(workspace)
    assert (result.status, result.topology, result.authority_user) == ("resolved", "single_team", manager)


@pytest.mark.parametrize("inactive_range", [False, True])
def test_team_requires_active_range_even_with_agency_manager(setup, inactive_range):
    workspace, user, team = setup
    user("agency_manager")
    if inactive_range:
        team(user(), is_active=False)
    assert_unresolved(resolve_workspace_authority(workspace), "no_active_ranges")


@pytest.mark.parametrize("defect", ["missing", "inactive", "wrong_role", "foreign"])
def test_invalid_manager_fails_for_single_and_multi_team(setup, defect):
    workspace, user, team = setup
    manager = user()
    row = team(manager)
    # Controlled legacy inconsistency: bypass model clean only in these fixtures.
    if defect == "missing":
        Range.objects.filter(pk=row.pk).update(manager=None)
    elif defect == "inactive":
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif defect == "wrong_role":
        User.objects.filter(pk=manager.pk).update(role="consultant")
    else:
        foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        User.objects.filter(pk=manager.pk).update(workspace=foreign)
    assert_unresolved(resolve_workspace_authority(workspace), "invalid_range_manager", "single_team")
    team(user())
    user("agency_manager")
    assert_unresolved(resolve_workspace_authority(workspace), "invalid_range_manager", "multi_team")


@pytest.mark.parametrize("shared_manager", [False, True])
def test_multi_team_resolves_only_agency_manager(setup, shared_manager):
    workspace, user, team = setup
    manager = user()
    team(manager)
    team(manager if shared_manager else user())
    agency = user("agency_manager")
    result = resolve_workspace_authority(workspace)
    assert (result.status, result.topology, result.authority_user) == ("resolved", "multi_team", agency)


@pytest.mark.parametrize("agency_state", ["missing", "multiple", "inactive", "foreign"])
def test_multi_team_requires_exactly_one_active_local_agency_manager(setup, agency_state):
    workspace, user, team = setup
    team(user())
    team(user())
    if agency_state != "missing":
        agency = user("agency_manager", is_active=agency_state != "inactive")
        if agency_state == "multiple":
            user("agency_manager")
        elif agency_state == "foreign":
            foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="agency_manager")
            User.objects.filter(pk=agency.pk).update(workspace=foreign)
    assert_unresolved(resolve_workspace_authority(workspace), "agency_manager_cardinality", "multi_team")


def test_inactive_malformed_range_does_not_count(setup):
    workspace, user, team = setup
    manager = user()
    team(manager)
    team(is_active=False)
    assert resolve_workspace_authority(workspace).authority_user == manager


def test_purchaser_and_privilege_flags_do_not_select_authority(setup):
    workspace, user, team = setup
    manager = user()
    team(manager)
    outsider = user("consultant", is_workspace_owner=True, is_staff=True, is_superuser=True)
    for purchaser in Workspace.CustomerType.values:
        Workspace.objects.filter(pk=workspace.pk).update(customer_type=purchaser)
        assert resolve_workspace_authority(workspace).authority_user == manager
    User.objects.filter(pk=manager.pk).update(is_active=False)
    Range.objects.filter(workspace=workspace).update(manager=outsider)
    assert_unresolved(resolve_workspace_authority(workspace), "invalid_range_manager", "single_team")


def test_resolution_is_read_only_immutable_and_query_count_is_bounded(setup):
    workspace, user, team = setup
    manager = user()
    for _ in range(8):
        team(manager)
    user("agency_manager")
    with CaptureQueriesContext(connection) as queries:
        result = resolve_workspace_authority(workspace)
    assert result.status == "resolved"
    assert len(queries) == 4
    assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
    with pytest.raises(FrozenInstanceError):
        result.status = "none"


def test_repeated_resolution_obeys_current_mode_topology_and_users(setup):
    workspace, user, team = setup
    manager = user()
    team(manager)
    assert resolve_workspace_authority(workspace).authority_user == manager
    second = team(user())
    assert_unresolved(resolve_workspace_authority(workspace), "agency_manager_cardinality", "multi_team")
    agency = user("agency_manager")
    assert resolve_workspace_authority(workspace).authority_user == agency
    Range.objects.filter(pk=second.pk).update(is_active=False)
    assert resolve_workspace_authority(workspace).authority_user == manager
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None)
    assert_unresolved(resolve_workspace_authority(workspace), "unresolved_mode")


def test_missing_workspace_fails_closed():
    assert_unresolved(resolve_workspace_authority(Workspace()), "workspace_missing")
