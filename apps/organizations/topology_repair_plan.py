"""Reviewed internal plans: strict parsing and read-only preflight, NEVER apply.

Successful preflight is not authorization or proof of valid resulting topology.
Future apply must recheck the token under Workspace lock and validate final D2a.
"""
from dataclasses import dataclass
import re
from uuid import UUID

from apps.accounts.models import User
from apps.ranges.models import Range
from .models import Workspace
from .topology_inventory import inventory_workspace


class RepairPlanError(ValueError):
    def __init__(self, code):
        self.errors = (code,)
        super().__init__(code)


@dataclass(frozen=True)
class RepairOperation:
    type: str
    parameters: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class RepairPlan:
    version: int
    workspace_id: str
    expected_state_token: str
    expected_workspace_mode: str | None
    expected_d2a_status: str
    expected_d2a_errors: tuple[str, ...]
    operations: tuple[RepairOperation, ...]
    reviewer: str
    reason: str


@dataclass(frozen=True)
class RepairPlanValidation:
    valid: bool
    stale: bool
    blocked: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    planned_operations: tuple[RepairOperation, ...]


PARAMETERS = {
    "set_workspace_mode": {"workspace_mode"},
    "bootstrap_first_range": {"manager_id", "range_name", "activity_scope"},
    "assign_active_range_manager": {"range_id", "manager_id"},
    "replace_active_range_manager": {"range_id", "manager_id"},
    "deactivate_range": {"range_id"},
    "activate_existing_agency_manager": {"user_id"},
    "deactivate_extra_agency_manager": {"user_id"},
    "clear_or_replace_malformed_inactive_manager_link": {"range_id", "manager_id"},
}


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise RepairPlanError("invalid_uuid") from None


def parse_repair_plan(data):
    if not isinstance(data, dict) or set(data) != set(RepairPlan.__dataclass_fields__):
        raise RepairPlanError("invalid_plan_fields")
    if type(data["version"]) is not int or data["version"] != 1:
        raise RepairPlanError("unsupported_plan_version")
    _uuid(data["workspace_id"])
    for field in ("reviewer", "reason"):
        if not isinstance(data[field], str) or not data[field].strip():
            raise RepairPlanError(f"{field}_required")
    if data["expected_workspace_mode"] not in (None, "personal", "team"):
        raise RepairPlanError("invalid_workspace_mode")
    if data["expected_d2a_status"] not in ("valid", "invalid", "unresolved"):
        raise RepairPlanError("invalid_expected_status")
    token = data["expected_state_token"]
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise RepairPlanError("invalid_state_token")
    errors = data["expected_d2a_errors"]
    if (not isinstance(errors, list) or any(not isinstance(code, str) or not code for code in errors)
            or errors != sorted(set(errors))):
        raise RepairPlanError("invalid_expected_errors")
    operations = data["operations"]
    if not isinstance(operations, list) or not operations:
        raise RepairPlanError("operations_required")
    parsed, writes = [], set()
    for op in operations:
        if not isinstance(op, dict) or not isinstance(op.get("type"), str) or op["type"] not in PARAMETERS:
            raise RepairPlanError("unknown_operation")
        kind = op["type"]
        if set(op) != PARAMETERS[kind] | {"type"}:
            raise RepairPlanError("invalid_operation_fields")
        for key in PARAMETERS[kind]:
            value = op[key]
            if key.endswith("_id"):
                if value is None and kind == "clear_or_replace_malformed_inactive_manager_link" and key == "manager_id":
                    continue
                _uuid(value)
            elif key == "workspace_mode" and value not in ("personal", "team"):
                raise RepairPlanError("invalid_workspace_mode")
            elif key == "activity_scope" and value not in Range.ActivityScope.values:
                raise RepairPlanError("invalid_activity_scope")
            elif key == "range_name" and (not isinstance(value, str) or not value.strip() or len(value) > 100):
                raise RepairPlanError("invalid_range_name")
        # Reject repeated/conflicting writes, not legitimate atomic companion
        # operations on different fields (e.g. replace FK then deactivate Range).
        if "range_id" in op:
            key = (op["range_id"], "activity" if kind == "deactivate_range" else "manager")
        elif "user_id" in op:
            key = (op["user_id"], "activity")
        else:
            key = (data["workspace_id"], kind)
        if key in writes:
            raise RepairPlanError("contradictory_operations")
        writes.add(key)
        parsed.append(RepairOperation(kind, tuple(sorted((key, op[key]) for key in PARAMETERS[kind]))))
    return RepairPlan(**{**data, "operations": tuple(parsed), "expected_d2a_errors": tuple(errors)})


def validate_repair_plan(plan):
    # Re-parse typed objects too: frozen dataclasses alone do not validate inputs.
    if isinstance(plan, RepairPlan):
        plan = {**plan.__dict__, "expected_d2a_errors": list(plan.expected_d2a_errors),
                "operations": [{"type": op.type, **dict(op.parameters)} for op in plan.operations]}
    plan = parse_repair_plan(plan)
    def result(errors=(), *, stale=False, warnings=()):
        return RepairPlanValidation(not errors, stale, bool(errors) and not stale,
                                    tuple(sorted(set(errors))), tuple(sorted(set(warnings))), plan.operations)
    try:
        current = inventory_workspace(plan.workspace_id)
    except Workspace.DoesNotExist:
        return result(("workspace_missing",))
    if (current.state_token != plan.expected_state_token
            or current.workspace_mode != plan.expected_workspace_mode
            or current.validation_status != plan.expected_d2a_status
            or current.d2a_errors != plan.expected_d2a_errors):
        return result(("repair_plan_stale",), stale=True)
    references = [dict(op.parameters) for op in plan.operations]
    user_ids = {row[key] for row in references for key in ("user_id", "manager_id") if row.get(key)}
    range_ids = {row["range_id"] for row in references if "range_id" in row}
    users = {str(row["pk"]): row for row in User.objects.filter(workspace_id=plan.workspace_id, pk__in=user_ids)
             .values("pk", "role", "is_active", "is_workspace_owner")}
    ranges = {str(row["pk"]): row for row in Range.objects.filter(workspace_id=plan.workspace_id, pk__in=range_ids)
              .values("pk", "is_active", "manager_id")}
    errors = set()
    planned_mode = next((params["workspace_mode"] for op, params in zip(plan.operations, references)
                         if op.type == "set_workspace_mode"), current.workspace_mode)
    if set(users) != user_ids or set(ranges) != range_ids:
        errors.add("invalid_local_reference")  # Missing and foreign are indistinguishable.
    for op, params in zip(plan.operations, references):
        kind = op.type
        row = ranges.get(params.get("range_id"))
        candidate = users.get(params.get("manager_id") or params.get("user_id"))
        if row is not None:
            inactive_edit = kind == "clear_or_replace_malformed_inactive_manager_link"
            if row["is_active"] == inactive_edit:
                errors.add("invalid_range_state")
            if kind == "assign_active_range_manager" and row["manager_id"] is not None:
                errors.add("range_already_has_manager")
            if kind == "replace_active_range_manager" and row["manager_id"] is None:
                errors.add("range_has_no_manager")
        if candidate is not None:
            if "manager_id" in params:
                if candidate["role"] != "range_manager" or (
                    kind != "clear_or_replace_malformed_inactive_manager_link" and not candidate["is_active"]
                ):
                    errors.add("invalid_manager_candidate")
            else:
                if candidate["role"] != "agency_manager":
                    errors.add("invalid_agency_candidate")
                if kind == "activate_existing_agency_manager" and candidate["is_active"]:
                    errors.add("agency_already_active")
                if kind == "deactivate_extra_agency_manager":
                    if not candidate["is_active"]:
                        errors.add("agency_already_inactive")
                    if candidate["is_workspace_owner"]:
                        errors.add("owner_agency_lifecycle_unresolved")
        if kind == "bootstrap_first_range" and current.active_range_count:
            errors.add("bootstrap_requires_zero_active_ranges")
        if kind == "bootstrap_first_range":
            if planned_mode != "team":
                errors.add("bootstrap_requires_team_mode")
            if not current.workspace_active:
                errors.add("workspace_inactive")
    warnings = {"final_topology_not_simulated"}
    if "inactive_historical_manager_anomaly" in current.issue_classes:
        warnings.add("inactive_historical_manager_anomaly")
    return result(errors, warnings=warnings)
