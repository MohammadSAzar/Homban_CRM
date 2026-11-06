from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.locations.models import City, Region
from apps.matching.models import RecommendationWork
from apps.organizations import topology_repair as repair
from apps.organizations.models import Workspace
from apps.organizations.topology_inventory import inventory_workspace
from apps.organizations.topology_repair_plan import RepairPlanError, parse_repair_plan
from apps.organizations.topology_services import TopologyMutationError
from apps.ranges.models import Range, RangeMembership
from .test_topology import setup
from .test_topology_repair_plan import document

pytestmark = pytest.mark.django_db(transaction=True)


def op(kind, **params):
    return dict(type=kind, **{key: str(value) if key.endswith("_id") and value is not None else value
                             for key, value in params.items()})


def classify(mode):
    return op("set_workspace_mode", workspace_mode=mode)


def bootstrap(manager):
    return op("bootstrap_first_range", manager_id=manager.pk, range_name="first", activity_scope="all")


def apply(workspace, *operations):
    return repair.apply_repair_plan(document(workspace, list(operations)))


def foreign_user(role="range_manager"):
    workspace = Workspace.objects.create(name="foreign", slug="foreign", customer_type="consultant")
    return User.objects.create_user(username="foreign", workspace=workspace, role=role)


def test_null_personal_frozen_result_and_replay(setup):
    workspace, user, _ = setup
    user("consultant", is_workspace_owner=True, is_staff=True, is_superuser=True)
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None)
    plan = parse_repair_plan(document(workspace, [classify("personal")]))
    result = repair.apply_repair_plan(plan)
    assert result.final_topology == "personal" and result.final_d2a_status == "valid"
    assert result.final_authority_status == "none" and result.final_authority_user_id is None
    assert result.before_state_token != result.after_state_token
    assert result.reviewer == plan.reviewer and result.reason == plan.reason
    with pytest.raises(FrozenInstanceError):
        result.reason = "changed"
    with pytest.raises(TopologyMutationError, match="repair_plan_stale"):
        repair.apply_repair_plan(plan)


@pytest.mark.parametrize("mode", [None, "team"])
def test_bootstrap_classification_atomic(setup, mode):
    workspace, user, _ = setup
    manager = user(is_workspace_owner=True)
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=mode)
    result = apply(workspace, bootstrap(manager), classify("team"))
    assert result.final_topology == "single_team"
    assert result.final_authority_user_id == str(manager.pk)
    assert Range.objects.get(pk=result.created_range_id).manager_id == manager.pk
    assert [item.type for item in result.applied_operations] == ["set_workspace_mode", "bootstrap_first_range"]
    manager.refresh_from_db(); workspace.refresh_from_db()
    assert manager.is_workspace_owner and manager.role == "range_manager"
    assert User.objects.count() == 1 and workspace.customer_type == "consultant"


@pytest.mark.parametrize("defect", ["missing", "inactive", "role", "foreign", "duplicate"])
def test_active_manager_repairs(setup, defect):
    workspace, user, team = setup
    old = user()
    row = team(None if defect == "missing" else old)
    if defect == "inactive":
        User.objects.filter(pk=old.pk).update(is_active=False)
    elif defect == "role":
        User.objects.filter(pk=old.pk).update(role="consultant")
    elif defect == "foreign":
        old = foreign_user()
        Range.objects.filter(pk=row.pk).update(manager=old)
    elif defect == "duplicate":
        team(old); user("agency_manager")
    before = User.objects.filter(pk=old.pk).values().get()
    incoming = user()
    kind = "assign_active_range_manager" if defect == "missing" else "replace_active_range_manager"
    result = apply(workspace, op(kind, range_id=row.pk, manager_id=incoming.pk))
    row.refresh_from_db()
    assert row.manager_id == incoming.pk and result.final_d2a_status == "valid"
    assert User.objects.filter(pk=old.pk).values().get() == before


@pytest.mark.parametrize("extra", [False, True])
def test_agency_repairs_track_after_commit(setup, extra, isolated_matching_broker):
    workspace, user, team = setup
    team(user()); team(user())
    chosen = user("agency_manager", is_active=extra)
    target = user("agency_manager") if extra else chosen
    before = RecommendationWork.objects.count()
    result = apply(workspace, op("deactivate_extra_agency_manager" if extra else "activate_existing_agency_manager",
                                 user_id=target.pk))
    assert result.final_authority_user_id == str(chosen.pk)
    assert RecommendationWork.objects.count() == before + 1
    assert isolated_matching_broker.call_count == 1
    target.refresh_from_db()
    assert target.is_active is (not extra) and target.role == "agency_manager"


@pytest.mark.parametrize("defect", ["foreign", "role", "none"])
def test_personal_deactivation_preserves_history(setup, defect):
    workspace, user, team = setup
    row = team(user())
    if defect == "foreign":
        Range.objects.filter(pk=row.pk).update(manager=foreign_user())
    elif defect == "role":
        User.objects.filter(pk=row.manager_id).update(role="consultant")
    else:
        Range.objects.filter(pk=row.pk).update(manager=None)
    row.refresh_from_db(); manager_id = row.manager_id
    city = City.objects.create(workspace=workspace, name="city")
    region = Region.objects.create(workspace=workspace, city=city, name="region")
    row.regions.add(region)
    membership = RangeMembership.objects.create(workspace=workspace, range=row, user=user("consultant"))
    result = apply(workspace, op("deactivate_range", range_id=row.pk), classify("personal"))
    row.refresh_from_db(); membership.refresh_from_db()
    assert not row.is_active and row.manager_id == manager_id
    assert list(row.regions.all()) == [region] and membership.range_id == row.pk
    assert result.final_topology == "personal"
    if defect != "none":
        assert "inactive_historical_manager_anomaly" in result.warnings


@pytest.mark.parametrize("replace", [False, True])
def test_inactive_cleanup(setup, replace):
    workspace, user, team = setup
    team(user())
    row = team(is_active=False)
    Range.objects.filter(pk=row.pk).update(manager=foreign_user())
    candidate = user(is_active=False) if replace else None
    result = apply(workspace, op("clear_or_replace_malformed_inactive_manager_link", range_id=row.pk,
                                 manager_id=candidate.pk if candidate else None))
    row.refresh_from_db()
    assert row.manager_id == (candidate.pk if candidate else None)
    assert result.final_d2a_status == "valid"


@pytest.mark.parametrize("change", ["mode", "manager", "agency", "token"])
def test_stale_under_workspace_lock(setup, change):
    workspace, user, team = setup
    row = team(user())
    plan = document(workspace, [classify("team")])
    if change == "mode":
        Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None)
    elif change == "manager":
        Range.objects.filter(pk=row.pk).update(manager=user())
    elif change == "agency":
        user("agency_manager")
    else:
        plan["expected_state_token"] = "0" * 64
    before = inventory_workspace(workspace.pk).state_token
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(TopologyMutationError, match="repair_plan_stale"):
            repair.apply_repair_plan(plan)
    selects = [q["sql"] for q in queries if q["sql"].upper().startswith("SELECT")]
    assert "FOR UPDATE" in selects[0] and "organizations_workspace" in selects[0]
    assert inventory_workspace(workspace.pk).state_token == before


@pytest.mark.parametrize("target", ["manager", "range", "agency"])
def test_foreign_missing_indistinguishable(setup, target):
    workspace, user, team = setup
    row = team()
    foreign = foreign_user()
    foreign_range = Range.objects.create(workspace=foreign.workspace, name="foreign")
    codes = []
    for candidate in (foreign_range.pk if target == "range" else foreign.pk, uuid4()):
        operation = (op("assign_active_range_manager", range_id=row.pk, manager_id=candidate) if target == "manager"
                     else op("deactivate_range", range_id=candidate) if target == "range"
                     else op("activate_existing_agency_manager", user_id=candidate))
        with pytest.raises(TopologyMutationError) as error:
            apply(workspace, operation)
        codes.append(error.value.errors)
    assert codes == [("invalid_local_reference",)] * 2


def test_owner_blocker_before_any_write(setup):
    workspace, user, team = setup
    team(user()); team(user()); user("agency_manager")
    owner = user("agency_manager", is_workspace_owner=True)
    before = inventory_workspace(workspace.pk).state_token
    with pytest.raises(TopologyMutationError, match="owner_agency_lifecycle_unresolved"):
        apply(workspace, classify("personal"), op("deactivate_extra_agency_manager", user_id=owner.pk))
    assert inventory_workspace(workspace.pk).state_token == before


def test_nested_transaction_rejected(setup):
    with transaction.atomic():
        with pytest.raises(TopologyMutationError, match="repair_requires_outermost_transaction"):
            apply(setup[0], classify("personal"))


def test_contradictory_plan_rejected(setup):
    with pytest.raises(RepairPlanError, match="contradictory_operations"):
        apply(setup[0], classify("personal"), classify("team"))


@pytest.mark.parametrize("failure", ["midway", "final", "authority"])
def test_complete_rollback_and_callbacks(setup, monkeypatch, failure, isolated_matching_broker):
    workspace, user, team = setup
    old = user(); row = team(old); team(user())
    incoming = user(); agency = user("agency_manager", is_active=False)
    operations = [op("replace_active_range_manager", range_id=row.pk, manager_id=incoming.pk),
                  op("activate_existing_agency_manager", user_id=agency.pk)]
    before = inventory_workspace(workspace.pk).state_token
    work_before = RecommendationWork.objects.count()
    execute = repair._execute_operation
    callbacks = []
    def wrapped(*args):
        value = execute(*args)
        transaction.on_commit(lambda: callbacks.append("published"))
        if failure == "midway" and args[0].type == "activate_existing_agency_manager":
            raise RuntimeError("test failure after activity save")
        return value
    monkeypatch.setattr(repair, "_execute_operation", wrapped)
    if failure == "final":
        def reject(workspace):
            raise TopologyMutationError("test_final_invalid")
        monkeypatch.setattr(repair, "require_valid_locked_topology", reject)
    elif failure == "authority":
        from apps.organizations.authority import WorkspaceAuthorityResolution
        monkeypatch.setattr(repair, "resolve_workspace_authority", lambda workspace:
                            WorkspaceAuthorityResolution("invalid", None, "multi_team", "test_invalid"))
    with pytest.raises((RuntimeError, TopologyMutationError)):
        apply(workspace, *operations)
    assert inventory_workspace(workspace.pk).state_token == before
    assert RecommendationWork.objects.count() == work_before
    assert callbacks == [] and isolated_matching_broker.call_count == 0


def test_bootstrap_failure_rolls_back_classification_and_created_range(setup, monkeypatch):
    workspace, user, _ = setup
    manager = user()
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None)
    def reject(workspace):
        assert Range.objects.filter(workspace=workspace).count() == 1
        raise TopologyMutationError("test_invalid")
    monkeypatch.setattr(repair, "require_valid_locked_topology", reject)
    with pytest.raises(TopologyMutationError, match="repair_final_topology_invalid"):
        apply(workspace, bootstrap(manager), classify("team"))
    workspace.refresh_from_db()
    assert workspace.workspace_mode is None and not Range.objects.exists()


def test_real_final_invalid_no_automatic_selection(setup):
    workspace, user, team = setup
    team(user()); team(user()); user("agency_manager", is_active=False)
    before = inventory_workspace(workspace.pk).state_token
    with pytest.raises(TopologyMutationError, match="repair_final_topology_invalid"):
        apply(workspace, classify("team"))
    assert inventory_workspace(workspace.pk).state_token == before


def test_locks_local_only_in_order_and_double_preflight(setup, monkeypatch):
    workspace, user, team = setup
    foreign = foreign_user()
    row = team()
    Range.objects.filter(pk=row.pk).update(manager=foreign)
    incoming = user()
    checks = []
    preflight = repair._preflight
    def checked(plan):
        assert connection.in_atomic_block
        checks.append(1)
        return preflight(plan)
    monkeypatch.setattr(repair, "_preflight", checked)
    with CaptureQueriesContext(connection) as queries:
        apply(workspace, op("replace_active_range_manager", range_id=row.pk, manager_id=incoming.pk))
    locks = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
    assert "organizations_workspace" in locks[0]
    user_lock = next(i for i, sql in enumerate(locks) if "accounts_user" in sql)
    range_lock = next(i for i, sql in enumerate(locks) if "ranges_range" in sql)
    assert user_lock < range_lock
    assert "ORDER BY" in locks[user_lock] and "ORDER BY" in locks[range_lock]
    assert foreign.pk.hex not in locks[user_lock].replace("-", "")
    assert checks == [1, 1]


def test_second_preflight_failure_prevents_writes(setup, monkeypatch):
    workspace, user, team = setup
    team(user())
    before = inventory_workspace(workspace.pk).state_token
    original = repair.validate_repair_plan
    calls = []
    def check(plan):
        result = original(plan)
        calls.append(1)
        if len(calls) == 2:
            from dataclasses import replace
            return replace(result, valid=False, stale=True, errors=("repair_plan_stale",))
        return result
    monkeypatch.setattr(repair, "validate_repair_plan", check)
    with pytest.raises(TopologyMutationError, match="repair_plan_stale"):
        apply(workspace, classify("team"))
    assert inventory_workspace(workspace.pk).state_token == before


def test_operation_order_and_final_only_uniqueness(setup):
    workspace, user, team = setup
    first_manager = user(); second_manager = user()
    first = team(first_manager); second = team(second_manager); user("agency_manager")
    operations = [op("replace_active_range_manager", range_id=second.pk, manager_id=first_manager.pk),
                  op("replace_active_range_manager", range_id=first.pk, manager_id=second_manager.pk)]
    result = apply(workspace, *reversed(operations))
    assert [dict(item.parameters)["range_id"] for item in result.applied_operations] == sorted([str(first.pk), str(second.pk)])
    first.refresh_from_db(); second.refresh_from_db()
    assert first.manager_id == second_manager.pk and second.manager_id == first_manager.pk


def test_logical_noop_changes_token(setup):
    workspace, user, team = setup
    team(user())
    plan = document(workspace, [classify("team")])
    result = repair.apply_repair_plan(plan)
    assert result.before_state_token != result.after_state_token
    with pytest.raises(TopologyMutationError, match="repair_plan_stale"):
        repair.apply_repair_plan(plan)


def test_missing_workspace(setup):
    plan = document(setup[0], [classify("personal")]); plan["workspace_id"] = str(uuid4())
    with pytest.raises(TopologyMutationError, match="workspace_missing"):
        repair.apply_repair_plan(plan)


@pytest.mark.parametrize("flag", ["is_workspace_owner", "is_staff", "is_superuser"])
def test_metadata_does_not_make_candidate_eligible(setup, flag):
    workspace, user, team = setup
    row = team()
    candidate = user("consultant", **{flag: True})
    with pytest.raises(TopologyMutationError, match="invalid_manager_candidate"):
        apply(workspace, op("assign_active_range_manager", range_id=row.pk, manager_id=candidate.pk))
    candidate.refresh_from_db(); row.refresh_from_db()
    assert candidate.role == "consultant" and row.manager_id is None


def test_bootstrap_validation_failure_rolls_back_mode(setup):
    workspace, user, _ = setup
    manager = user()
    Range.objects.create(workspace=workspace, name="first", is_active=False)
    Workspace.objects.filter(pk=workspace.pk).update(workspace_mode=None)
    with pytest.raises(TopologyMutationError, match="range_validation_failed"):
        apply(workspace, classify("team"), bootstrap(manager))
    workspace.refresh_from_db()
    assert workspace.workspace_mode is None
    assert Range.objects.filter(workspace=workspace, is_active=True).count() == 0


def test_failed_deactivation_restores_range_activity(setup):
    workspace, user, team = setup
    row = team(user())
    before = inventory_workspace(workspace.pk).state_token
    with pytest.raises(TopologyMutationError, match="repair_final_topology_invalid"):
        apply(workspace, op("deactivate_range", range_id=row.pk))
    row.refresh_from_db()
    assert row.is_active and inventory_workspace(workspace.pk).state_token == before


def test_revision_advances_even_with_future_timestamp(setup):
    from datetime import timedelta
    from django.utils import timezone
    workspace, user, team = setup
    team(user())
    future = timezone.now() + timedelta(days=1)
    Workspace.objects.filter(pk=workspace.pk).update(updated_at=future)
    apply(workspace, classify("team"))
    workspace.refresh_from_db()
    assert workspace.updated_at > future
