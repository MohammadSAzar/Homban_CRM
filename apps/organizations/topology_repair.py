"""Trusted reviewed repair only; no customer authorization or automatic selection."""
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.ranges.models import Range
from .authority import resolve_workspace_authority
from .models import Workspace
from .topology_inventory import inventory_workspace
from .topology_repair_plan import RepairOperation, RepairPlan, parse_repair_plan, validate_repair_plan
from .topology_services import (
    TopologyMutationError, _bootstrap_team_first_range_locked,
    require_valid_locked_topology, topology_transaction,
)


OPERATION_ORDER = (
    "set_workspace_mode", "assign_active_range_manager", "replace_active_range_manager",
    "clear_or_replace_malformed_inactive_manager_link", "deactivate_range",
    "deactivate_extra_agency_manager", "activate_existing_agency_manager", "bootstrap_first_range",
)


@dataclass(frozen=True)
class RepairApplyResult:
    workspace_id: str
    reviewer: str
    reason: str
    completed_at: str
    before_state_token: str
    after_state_token: str
    applied_operations: tuple[RepairOperation, ...]
    created_range_id: str | None
    final_workspace_mode: str
    final_topology: str
    final_d2a_status: str
    final_d2a_errors: tuple[str, ...]
    final_authority_status: str
    final_authority_user_id: str | None
    warnings: tuple[str, ...]


def _preflight(plan):
    result = validate_repair_plan(plan)
    if not result.valid:
        raise TopologyMutationError(*result.errors)


def _execute_operation(operation, locked, users, ranges):
    """Preconditions were checked against the locked START state, not file order."""
    params = dict(operation.parameters)
    kind = operation.type
    workspace = locked.workspace
    if kind == "set_workspace_mode":
        workspace.workspace_mode = params["workspace_mode"]
        workspace.save(update_fields=("workspace_mode", "updated_at"))
    elif kind in ("assign_active_range_manager", "replace_active_range_manager",
                  "clear_or_replace_malformed_inactive_manager_link"):
        row = ranges[params["range_id"]]
        row.manager = users[params["manager_id"]] if params["manager_id"] else None
        row.save(update_fields=("manager", "updated_at"))
    elif kind == "deactivate_range":
        row = ranges[params["range_id"]]
        # Repair-only exception: full_clean rejects preserved foreign/wrong-role
        # historical links even when only deactivating. Never bypass other fields.
        malformed = row.manager_id is not None and (
            row.manager.workspace_id != workspace.pk or row.manager.role != User.Role.RANGE_MANAGER
        )
        if malformed:
            changed = Range.objects.filter(pk=row.pk, workspace=workspace).update(
                is_active=False, updated_at=timezone.now(),
            )
            if changed != 1:
                raise TopologyMutationError("repair_operation_precondition_failed")
        else:
            row.is_active = False
            row.save(update_fields=("is_active", "updated_at"))
        row.refresh_from_db()
    elif kind in ("deactivate_extra_agency_manager", "activate_existing_agency_manager"):
        user = users[params["user_id"]]
        if kind == "deactivate_extra_agency_manager" and user.is_workspace_owner:
            raise TopologyMutationError("owner_agency_lifecycle_unresolved")
        user.is_active = kind == "activate_existing_agency_manager"
        user.save(update_fields=("is_active", "updated_at"))
    elif kind == "bootstrap_first_range":
        return _bootstrap_team_first_range_locked(
            workspace=workspace, manager=users[params["manager_id"]],
            range_name=params["range_name"], activity_scope=params["activity_scope"],
        )
    return None


def apply_repair_plan(plan):
    """Apply one reviewed repair; pre-commit failures roll back all writes.

    No database preflight occurs before the Workspace lock. Explicit LOCAL IDs
    avoid D2b discovery attempting to lock malformed foreign manager references.
    A caller must not wrap this service in another transaction. Post-commit
    delivery failures cannot undo a committed repair.
    """
    if connection.in_atomic_block or not connection.get_autocommit():
        raise TopologyMutationError("repair_requires_outermost_transaction")
    if isinstance(plan, RepairPlan):
        plan = {**plan.__dict__, "expected_d2a_errors": list(plan.expected_d2a_errors),
                "operations": [{"type": op.type, **dict(op.parameters)} for op in plan.operations]}
    plan = parse_repair_plan(plan)
    operations = tuple(sorted(plan.operations, key=lambda op: (
        OPERATION_ORDER.index(op.type),
        dict(op.parameters).get("range_id") or dict(op.parameters).get("user_id")
        or dict(op.parameters).get("manager_id") or plan.workspace_id,
    )))
    with transaction.atomic(durable=True):
        workspace = Workspace.objects.select_for_update().filter(pk=plan.workspace_id).first()
        if workspace is None:
            raise TopologyMutationError("workspace_missing")
        _preflight(plan)
        # Rare operational repair: lock every local User and Range in PK order.
        user_ids = tuple(User.objects.filter(workspace=workspace).values_list("pk", flat=True))
        range_ids = tuple(Range.objects.filter(workspace=workspace).values_list("pk", flat=True))
        with topology_transaction(workspace_id=workspace.pk, user_ids=user_ids, range_ids=range_ids) as locked:
            _preflight(plan)
            users = {str(user.pk): user for user in locked.users}
            ranges = {str(row.pk): row for row in locked.ranges}
            created = None
            try:
                for operation in operations:
                    row = _execute_operation(operation, locked, users, ranges)
                    if row is not None:
                        created = str(row.pk)
            except ValidationError as error:
                raise TopologyMutationError("repair_operation_validation_failed") from error
            try:
                final = require_valid_locked_topology(locked.workspace)
            except TopologyMutationError as error:
                raise TopologyMutationError("repair_final_topology_invalid", *error.errors,
                                            status=error.status, topology=error.topology) from error
            authority = resolve_workspace_authority(locked.workspace)
            consistent = (
                authority.topology == final.topology
                and ((final.topology == "personal" and authority.status == "none" and authority.authority_user is None)
                     or (final.topology != "personal" and authority.status == "resolved"
                         and authority.authority_user is not None))
            )
            if not consistent:
                raise TopologyMutationError("repair_final_authority_invalid")
            # Deliberate revision even for a logical no-op. QuerySet.update avoids
            # auto_now overriding this strictly increasing timestamp.
            completed = timezone.now()
            revision = max(completed, max(workspace.updated_at, locked.workspace.updated_at)
                           + timedelta(microseconds=1))
            Workspace.objects.filter(pk=workspace.pk).update(updated_at=revision)
            after = inventory_workspace(workspace.pk)
            result = RepairApplyResult(
                str(workspace.pk), plan.reviewer, plan.reason, completed.isoformat(),
                plan.expected_state_token, after.state_token, operations, created,
                after.workspace_mode, after.topology, after.validation_status, after.d2a_errors,
                after.authority_status, after.authority_user_id, after.issue_classes,
            )
    return result
