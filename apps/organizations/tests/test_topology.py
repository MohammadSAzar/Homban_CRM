from dataclasses import FrozenInstanceError
from itertools import count

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.organizations.topology import validate_workspace_topology
from apps.ranges.models import Range

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    workspace = Workspace.objects.create(name="مجموعه", slug="topology", customer_type="consultant", workspace_mode="team")
    sequence = count()

    def user(role="range_manager", **kwargs):
        return User.objects.create_user(username=f"user-{next(sequence)}", workspace=workspace, role=role, **kwargs)

    def team(manager=None, **kwargs):
        return Range.objects.create(name=f"رنج {next(sequence)}", workspace=workspace, manager=manager, **kwargs)

    return workspace, user, team


@pytest.mark.parametrize("active", [False, True])
def test_personal(setup, active):
    workspace, user, team = setup
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode="personal")
    team(user(), is_active=active)
    result = validate_workspace_topology(workspace)
    assert result.mode == "personal"  # Reloads mode rather than trusting the stale instance.
    assert result.topology == "personal"
    assert result.status == ("invalid" if active else "valid")
    assert result.errors == (("personal_has_active_ranges",) if active else ())


def test_unresolved_mode(setup):
    workspace, user, team = setup
    team(user())
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None)
    result = validate_workspace_topology(workspace)
    assert (result.status, result.mode, result.topology, result.errors) == (
        "unresolved", None, "unresolved", ("unresolved_mode",),
    )


def test_team_without_active_range(setup):
    workspace, user, team = setup
    team(is_active=False)
    result = validate_workspace_topology(workspace)
    assert result.status == "invalid"
    assert result.errors == ("team_has_no_active_ranges",)


@pytest.mark.parametrize("agency_count", [0, 1, 2])
def test_valid_single_ignores_agency_cardinality(setup, agency_count):
    workspace, user, team = setup
    team(user())
    for _ in range(agency_count):
        user("agency_manager")
    result = validate_workspace_topology(workspace)
    assert (result.status, result.topology, result.errors) == ("valid", "single_team", ())


@pytest.mark.parametrize("defect,code", [
    ("missing", "active_range_missing_manager"),
    ("inactive", "active_range_manager_inactive"),
    ("role", "active_range_manager_wrong_role"),
    ("workspace", "active_range_manager_wrong_workspace"),
])
def test_invalid_manager(setup, defect, code):
    workspace, user, team = setup
    manager = user()
    row = team(manager)
    if defect == "missing":
        Range.objects.filter(pk=row.pk).update(manager=None)
    elif defect == "inactive":
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif defect == "role":
        User.objects.filter(pk=manager.pk).update(role="consultant")
    else:
        foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
        User.objects.filter(pk=manager.pk).update(workspace=foreign)
    result = validate_workspace_topology(workspace)
    assert result.status == "invalid"
    assert result.errors == (code,)


@pytest.mark.parametrize("agency_count", [0, 1, 2])
def test_multi_agency_cardinality(setup, agency_count):
    workspace, user, team = setup
    team(user())
    team(user())
    user("agency_manager", is_active=False)
    for _ in range(agency_count):
        user("agency_manager")
    result = validate_workspace_topology(workspace)
    assert result.topology == "multi_team"
    assert result.status == ("valid" if agency_count == 1 else "invalid")
    assert result.errors == (() if agency_count == 1 else ("multi_team_agency_manager_cardinality",))


@pytest.mark.parametrize("second_active", [False, True])
def test_manager_multiplicity(setup, second_active):
    workspace, user, team = setup
    manager = user()
    team(manager)
    team(manager, is_active=second_active)
    team(manager, is_active=False)
    user("agency_manager")
    result = validate_workspace_topology(workspace)
    assert result.status == ("invalid" if second_active else "valid")
    assert result.errors == (("range_manager_multiple_active_ranges",) if second_active else ())


def test_inactive_malformed_range_ignored(setup):
    workspace, user, team = setup
    team(user())
    team(is_active=False)
    assert validate_workspace_topology(workspace).status == "valid"


@pytest.mark.parametrize("purchaser", Workspace.CustomerType.values)
def test_identity_flags_do_not_change_validation(setup, purchaser):
    workspace, user, team = setup
    Workspace.objects.filter(pk=workspace.pk).update(customer_type=purchaser)
    manager = user(is_workspace_owner=True, is_staff=True, is_superuser=True)
    team(manager)
    assert validate_workspace_topology(workspace).status == "valid"
    User.objects.filter(pk=manager.pk).update(role="consultant")
    assert validate_workspace_topology(workspace).errors == ("active_range_manager_wrong_role",)


def test_all_errors_are_deterministic(setup):
    workspace, user, team = setup
    manager = user()
    team(manager)
    team(manager)
    team()
    foreign = Workspace.objects.create(name="دیگر", slug="foreign", customer_type="consultant")
    User.objects.filter(pk=manager.pk).update(is_active=False, role="consultant", workspace=foreign)
    expected = (
        "active_range_manager_inactive", "active_range_manager_wrong_role",
        "active_range_manager_wrong_workspace", "active_range_missing_manager",
        "multi_team_agency_manager_cardinality", "range_manager_multiple_active_ranges",
    )
    assert validate_workspace_topology(workspace).errors == expected
    assert validate_workspace_topology(workspace).errors == expected


def test_read_only_immutable_and_bounded_queries(setup):
    workspace, user, team = setup
    team(user())
    team(user())
    user("agency_manager")
    with CaptureQueriesContext(connection) as small:
        result = validate_workspace_topology(workspace)
    for _ in range(8):
        team(user())
    with CaptureQueriesContext(connection) as large:
        assert validate_workspace_topology(workspace) == result
        assert validate_workspace_topology(workspace) == result
    assert len(small) == 3
    assert len(large) == 6
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in [*small, *large])
    assert all("FOR UPDATE" not in query["sql"].upper() for query in [*small, *large])
    with pytest.raises(FrozenInstanceError):
        result.status = "invalid"
