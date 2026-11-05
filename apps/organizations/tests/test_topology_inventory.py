from dataclasses import FrozenInstanceError, asdict
from io import StringIO
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.organizations.topology_inventory import inventory_workspace, inventory_workspaces
from apps.ranges.models import Range
from .test_topology import setup

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("mode,count", [("personal", 0), ("team", 1), ("team", 2)])
def test_valid_inventory(setup, mode, count):
    workspace, user, team = setup
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=mode)
    for _ in range(count):
        team(user())
    if count == 2:
        user("agency_manager")
    result = inventory_workspace(workspace.pk)
    assert result.validation_status == "valid" and result.issue_classes == ()
    assert result.authority_status == ("none" if mode == "personal" else "resolved")
    with pytest.raises(FrozenInstanceError):
        result.workspace_mode = "team"


@pytest.mark.parametrize("state,issue", [
    ("null", "unresolved_workspace_mode"), ("zero", "team_no_active_ranges"),
    ("personal", "personal_has_active_ranges"), ("missing", "active_range_missing_manager"),
    ("inactive", "active_range_manager_inactive"), ("role", "active_range_manager_wrong_role"),
    ("foreign", "active_range_manager_wrong_workspace"), ("duplicate", "range_manager_multiple_active_ranges"),
    ("no_agency", "multi_team_no_active_agency_manager"),
    ("agencies", "multi_team_multiple_active_agency_managers"),
    ("owner", "owner_agency_repair_blocker"),
])
def test_issue_classes(setup, state, issue):
    workspace, user, team = setup
    manager = user()
    row = team(manager)
    if state in ("null", "personal"):
        Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None if state == "null" else "personal")
    elif state == "zero":
        Range.objects.filter(pk=row.pk).update(is_active=False)
    elif state == "missing":
        Range.objects.filter(pk=row.pk).update(manager=None)
    elif state == "inactive":
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif state == "role":
        User.objects.filter(pk=manager.pk).update(role="consultant")
    elif state == "foreign":
        other = Workspace.objects.create(name="other", slug="other", customer_type="consultant")
        User.objects.filter(pk=manager.pk).update(workspace=other)
    elif state in ("duplicate", "no_agency", "agencies"):
        team(manager if state == "duplicate" else user())
        if state != "no_agency":
            user("agency_manager")
        if state == "agencies":
            user("agency_manager")
    else:
        user("agency_manager", is_workspace_owner=True)
    result = inventory_workspace(workspace.pk)
    assert issue in result.issue_classes
    assert result.issue_classes == tuple(sorted(set(result.issue_classes)))
    if state == "owner":
        assert result.owner_agency_blocker and result.validation_status == "valid"
    if state == "duplicate":
        assert result.validation_status == "invalid" and result.authority_status == "resolved"


@pytest.mark.parametrize("anomaly", ["null", "inactive", "repeat", "role", "foreign"])
def test_inactive_history(setup, anomaly):
    workspace, user, team = setup
    manager = user()
    team(user())
    row = team(manager, is_active=False)
    if anomaly == "null":
        Range.objects.filter(pk=row.pk).update(manager=None)
    elif anomaly == "inactive":
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif anomaly == "repeat":
        team(manager, is_active=False)
    elif anomaly == "role":
        User.objects.filter(pk=manager.pk).update(role="consultant")
    else:
        other = Workspace.objects.create(name="other", slug="other", customer_type="consultant")
        User.objects.filter(pk=manager.pk).update(workspace=other)
    result = inventory_workspace(workspace.pk)
    assert result.validation_status == "valid"
    assert ("inactive_historical_manager_anomaly" in result.issue_classes) == (anomaly in ("role", "foreign"))


def test_read_only_deterministic_bounded_queries(setup):
    workspace, user, team = setup
    team(user()); team(user()); user("agency_manager")
    with CaptureQueriesContext(connection) as small:
        first = inventory_workspace(workspace.pk)
    for _ in range(8):
        team(user())
    with CaptureQueriesContext(connection) as large:
        result = inventory_workspace(workspace.pk)
    assert len(small) == len(large)
    assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in large)
    assert result.active_range_ids == tuple(sorted(result.active_range_ids))
    assert result == inventory_workspace(workspace.pk)
    assert first.state_token != result.state_token
    assert not any(key in asdict(result) for key in ("password", "phone_number", "email"))


def test_command_all_filter_json_and_invalid_only(setup):
    workspace, user, team = setup
    other = Workspace.objects.create(name="personal", slug="personal", customer_type="consultant", workspace_mode="personal")
    output = StringIO()
    call_command("topology_inventory", "--json", stdout=output)
    rows = json.loads(output.getvalue())
    assert [row["workspace_id"] for row in rows] == sorted([str(workspace.pk), str(other.pk)])
    output = StringIO()
    call_command("topology_inventory", "--json", "--invalid-only", stdout=output)
    assert [row["workspace_id"] for row in json.loads(output.getvalue())] == [str(workspace.pk)]
    output = StringIO()
    call_command("topology_inventory", "--workspace", str(other.pk), stdout=output)
    assert "valid personal" in output.getvalue() and "Workspaces: 1" in output.getvalue()
    assert len(list(inventory_workspaces([workspace.pk]))) == 1
    outputs = []
    with CaptureQueriesContext(connection) as queries:
        for _ in range(2):
            output = StringIO()
            call_command("topology_inventory", stdout=output)
            outputs.append(output.getvalue())
        output = StringIO()
        call_command("topology_inventory", "--invalid-only", stdout=output)
        assert str(workspace.pk) in output.getvalue() and str(other.pk) not in output.getvalue()
        with pytest.raises(CommandError, match="invalid UUID"):
            call_command("topology_inventory", "--workspace", "not-a-uuid", stdout=StringIO())
    assert outputs[0] == outputs[1] and "Workspaces: 2" in outputs[0]
    assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
