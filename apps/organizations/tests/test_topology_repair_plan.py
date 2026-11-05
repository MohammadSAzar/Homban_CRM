from copy import deepcopy
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.organizations.topology_inventory import inventory_workspace
from apps.organizations.topology_repair_plan import parse_repair_plan, validate_repair_plan, RepairPlanError
from apps.ranges.models import Range
from .test_topology import setup

pytestmark = pytest.mark.django_db


def document(workspace, operations):
    current = inventory_workspace(workspace.pk)
    return dict(version=1, workspace_id=str(workspace.pk), expected_state_token=current.state_token,
                expected_workspace_mode=current.workspace_mode, expected_d2a_status=current.validation_status,
                expected_d2a_errors=list(current.d2a_errors), operations=operations,
                reviewer="reviewer-1", reason="Reviewed legacy classification")


def classification(workspace):
    return document(workspace, [{"type": "set_workspace_mode", "workspace_mode": "personal"}])


def test_parsed_frozen_readonly(setup):
    workspace, user, team = setup
    plan = parse_repair_plan(classification(workspace))
    with pytest.raises(FrozenInstanceError):
        plan.reason = "changed"
    with CaptureQueriesContext(connection) as queries:
        result = validate_repair_plan(plan)
    assert result.valid and not result.stale and not result.blocked
    assert "final_topology_not_simulated" in result.warnings
    assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
    workspace.refresh_from_db()
    assert workspace.workspace_mode == "team"


@pytest.mark.parametrize("field,value", [
    ("extra", True), ("reviewer", " "), ("reason", ""), ("workspace_id", "bad"),
    ("expected_workspace_mode", "auto"), ("version", True), ("expected_state_token", "bad"),
    ("operations", []), ("expected_d2a_errors", ["x", "x"]),
])
def test_bad_plan(setup, field, value):
    data = classification(setup[0]); data[field] = value
    with pytest.raises(RepairPlanError):
        parse_repair_plan(data)


@pytest.mark.parametrize("operation", [
    {"type": "auto_promote"}, {"type": "set_workspace_mode"},
    {"type": "set_workspace_mode", "workspace_mode": "auto"},
    {"type": "deactivate_range", "range_id": "bad"},
    {"type": "deactivate_range", "range_id": str(uuid4()), "force": True},
])
def test_bad_operation(setup, operation):
    with pytest.raises(RepairPlanError):
        parse_repair_plan(document(setup[0], [operation]))


def test_contradictory_operations(setup):
    data = classification(setup[0])
    data["operations"].append({"type": "set_workspace_mode", "workspace_mode": "team"})
    with pytest.raises(RepairPlanError, match="contradictory_operations"):
        parse_repair_plan(data)


@pytest.mark.parametrize("change", ["mode", "ranges", "manager", "agency", "activity", "status", "errors"])
def test_stale(setup, change):
    workspace, user, team = setup
    manager = user(); row = team(manager)
    data = classification(workspace)
    if change == "mode":
        Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None)
    elif change == "ranges":
        team(user())
    elif change == "manager":
        Range.objects.filter(pk=row.pk).update(manager=user())
    elif change == "agency":
        user("agency_manager")
    elif change == "activity":
        User.objects.filter(pk=manager.pk).update(is_active=False)
    elif change == "status":
        data["expected_d2a_status"] = "invalid"
    else:
        data["expected_d2a_errors"] = ["different"]
    result = validate_repair_plan(data)
    assert result.stale and result.errors == ("repair_plan_stale",)


def test_missing_workspace(setup):
    data = classification(setup[0]); data["workspace_id"] = str(uuid4())
    assert validate_repair_plan(data).errors == ("workspace_missing",)


@pytest.mark.parametrize("reference", ["manager", "range", "agency"])
def test_foreign_missing_reference_indistinguishable(setup, reference):
    workspace, user, team = setup
    local = team()
    other = Workspace.objects.create(name="other", slug="other", customer_type="consultant")
    foreign_user = User.objects.create(username="foreign", workspace=other, role="range_manager")
    foreign_range = Range.objects.create(workspace=other, name="foreign")
    errors = []
    for foreign_id in (str(foreign_range.pk if reference == "range" else foreign_user.pk), str(uuid4())):
        op = ({"type": "assign_active_range_manager", "range_id": str(local.pk), "manager_id": foreign_id}
              if reference == "manager" else {"type": "deactivate_range", "range_id": foreign_id}
              if reference == "range" else {"type": "activate_existing_agency_manager", "user_id": foreign_id})
        errors.append(validate_repair_plan(document(workspace, [op])).errors)
    assert errors[0] == errors[1] == ("invalid_local_reference",)


def test_owner_blocker(setup):
    workspace, user, team = setup
    owner = user("agency_manager", is_workspace_owner=True)
    data = document(workspace, [{"type": "deactivate_extra_agency_manager", "user_id": str(owner.pk)}])
    result = validate_repair_plan(data)
    assert result.blocked and result.errors == ("owner_agency_lifecycle_unresolved",)
    owner.refresh_from_db(); assert owner.is_active and owner.is_workspace_owner


def test_local_fk_repair_of_foreign_manager_and_history_warning(setup):
    workspace, user, team = setup
    manager = user(); row = team(manager, is_active=False)
    other = Workspace.objects.create(name="other", slug="other", customer_type="consultant")
    User.objects.filter(pk=manager.pk).update(workspace=other)
    data = document(workspace, [{"type": "clear_or_replace_malformed_inactive_manager_link",
                                 "range_id": str(row.pk), "manager_id": None}])
    with CaptureQueriesContext(connection) as queries:
        result = validate_repair_plan(data)
    assert result.valid and "inactive_historical_manager_anomaly" in result.warnings
    assert all("FOR UPDATE" not in q["sql"] for q in queries)
    row.refresh_from_db(); assert row.manager_id == manager.pk
    assert validate_repair_plan(classification(workspace)).valid


@pytest.mark.parametrize("kind", ["bootstrap_first_range", "assign_active_range_manager", "replace_active_range_manager",
                                  "deactivate_range", "activate_existing_agency_manager", "deactivate_extra_agency_manager"])
def test_operation_contracts_readonly(setup, kind):
    workspace, user, team = setup
    manager = user()
    if kind == "bootstrap_first_range":
        params = dict(manager_id=str(manager.pk), range_name="first", activity_scope="all")
    elif "agency" in kind:
        candidate = user("agency_manager", is_active=kind == "deactivate_extra_agency_manager")
        params = dict(user_id=str(candidate.pk))
    else:
        row = team(None if kind == "assign_active_range_manager" else user())
        params = dict(range_id=str(row.pk))
        if kind != "deactivate_range":
            params["manager_id"] = str(manager.pk)
    data = document(workspace, [dict(type=kind, **params)])
    before = inventory_workspace(workspace.pk).state_token
    assert validate_repair_plan(data).valid
    assert inventory_workspace(workspace.pk).state_token == before
