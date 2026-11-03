"""Explicit internal transitions; callers supply trusted authorization separately."""
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.ranges.models import Range

from .authority import resolve_workspace_authority
from .models import Workspace
from .topology_services import (
    TopologyMutationError, require_valid_locked_topology, topology_transaction,
)


def _start(locked, expected=None):
    workspace = locked.workspace
    if not workspace.is_active:
        raise TopologyMutationError("workspace_inactive")
    if workspace.workspace_mode != Workspace.WorkspaceMode.TEAM:
        raise TopologyMutationError("workspace_not_team")
    result = require_valid_locked_topology(workspace)
    if expected and result.topology != expected:
        raise TopologyMutationError("unexpected_start_topology", topology=result.topology)
    authority = resolve_workspace_authority(workspace)
    if authority.status != "resolved":
        raise TopologyMutationError("authority_unresolved")
    return authority.authority_user.pk


def _user(locked, user_id, role, *, active=True):
    user = next(user for user in locked.users if str(user.pk) == str(user_id))
    if user.role != role:
        raise TopologyMutationError("incoming_manager_wrong_role")
    if user.is_active != active:
        raise TopologyMutationError("incoming_manager_inactive" if active else "incoming_agency_manager_must_be_inactive")
    return user


def _active_range(locked, range_id):
    row = next(row for row in locked.ranges if str(row.pk) == str(range_id))
    if not row.is_active:
        raise TopologyMutationError("range_not_active")
    return row


def _unassigned(manager, *, except_range=None):
    assignments = Range.objects.filter(manager=manager, is_active=True)
    if except_range is not None:
        assignments = assignments.exclude(pk=except_range.pk)
    if assignments.exists():
        raise TopologyMutationError("range_manager_multiple_active_ranges")


def _save_range(row, **kwargs):
    try:
        row.save(**kwargs)
    except ValidationError as error:
        raise TopologyMutationError("range_validation_failed") from error


def _verify_authority(workspace, user_id):
    result = resolve_workspace_authority(workspace)
    if result.status != "resolved" or result.authority_user.pk != user_id:
        raise TopologyMutationError("unexpected_final_authority")


def transition_single_to_multi(*, workspace_id, agency_manager_id, new_range_manager_id,
                               new_range_name, activity_scope=Range.ActivityScope.ALL):
    with topology_transaction(workspace_id=workspace_id,
                              user_ids=(agency_manager_id, new_range_manager_id),
                              include_current_topology=True) as locked:
        _start(locked, "single_team")
        agency = _user(locked, agency_manager_id, User.Role.AGENCY_MANAGER)
        manager = _user(locked, new_range_manager_id, User.Role.RANGE_MANAGER)
        _unassigned(manager)
        row = Range(workspace=locked.workspace, manager=manager, name=new_range_name,
                    activity_scope=activity_scope, is_active=True)
        _save_range(row)
        require_valid_locked_topology(locked.workspace)
        _verify_authority(locked.workspace, agency.pk)
    return row


def transition_multi_to_single(*, workspace_id, keep_range_id):
    with topology_transaction(workspace_id=workspace_id, range_ids=(keep_range_id,),
                              include_current_topology=True) as locked:
        _start(locked, "multi_team")
        kept = _active_range(locked, keep_range_id)
        for row in locked.ranges:
            if row.pk != kept.pk and row.is_active:
                row.is_active = False
                _save_range(row, update_fields=["is_active", "updated_at"])
        require_valid_locked_topology(locked.workspace)
        _verify_authority(locked.workspace, kept.manager_id)
    return kept


def replace_range_manager(*, workspace_id, range_id, incoming_manager_id):
    with topology_transaction(workspace_id=workspace_id, user_ids=(incoming_manager_id,),
                              range_ids=(range_id,), include_current_topology=True) as locked:
        original_authority = _start(locked)
        row = _active_range(locked, range_id)
        manager = _user(locked, incoming_manager_id, User.Role.RANGE_MANAGER)
        if row.manager_id == manager.pk:
            raise TopologyMutationError("manager_unchanged")
        _unassigned(manager, except_range=row)
        single = sum(team.is_active for team in locked.ranges) == 1
        row.manager = manager
        _save_range(row, update_fields=["manager", "updated_at"])
        require_valid_locked_topology(locked.workspace)
        _verify_authority(locked.workspace, manager.pk if single else original_authority)
    return row


def replace_multi_team_agency_manager(*, workspace_id, incoming_agency_manager_id):
    with topology_transaction(workspace_id=workspace_id, user_ids=(incoming_agency_manager_id,),
                              include_current_topology=True) as locked:
        outgoing_id = _start(locked, "multi_team")
        if str(outgoing_id) == str(incoming_agency_manager_id):
            raise TopologyMutationError("manager_unchanged")
        incoming = _user(locked, incoming_agency_manager_id, User.Role.AGENCY_MANAGER, active=False)
        outgoing = next(user for user in locked.users if user.pk == outgoing_id)
        if outgoing.is_workspace_owner:
            raise TopologyMutationError("outgoing_agency_manager_is_owner")
        incoming.is_active = True
        incoming.save(update_fields=["is_active"])
        outgoing.is_active = False
        outgoing.save(update_fields=["is_active"])
        require_valid_locked_topology(locked.workspace)
        _verify_authority(locked.workspace, incoming.pk)
    return incoming


def add_active_range_to_multi_team(*, workspace_id, manager_id, range_name,
                                   activity_scope=Range.ActivityScope.ALL):
    """Expand valid multi-team topology without changing its Agency authority."""
    with topology_transaction(workspace_id=workspace_id, user_ids=(manager_id,),
                              include_current_topology=True) as locked:
        authority_id = _start(locked, "multi_team")
        manager = _user(locked, manager_id, User.Role.RANGE_MANAGER)
        _unassigned(manager)
        row = Range(workspace=locked.workspace, manager=manager, name=range_name,
                    activity_scope=activity_scope, is_active=True)
        _save_range(row)
        require_valid_locked_topology(locked.workspace)
        _verify_authority(locked.workspace, authority_id)
    return row


def activate_prepared_range(*, workspace_id, range_id, agency_manager_id=None):
    """Activate an existing prepared Range; never bootstrap or replace a manager."""
    user_ids = () if agency_manager_id is None else (agency_manager_id,)
    with topology_transaction(workspace_id=workspace_id, user_ids=user_ids,
                              range_ids=(range_id,), include_current_topology=True) as locked:
        authority_id = _start(locked)  # D2a rejects zero active Ranges as well.
        row = next(row for row in locked.ranges if str(row.pk) == str(range_id))
        if row.is_active:
            raise TopologyMutationError("range_already_active")
        if row.manager_id is None:
            raise TopologyMutationError("prepared_range_missing_manager")
        manager = _user(locked, row.manager_id, User.Role.RANGE_MANAGER)
        _unassigned(manager)
        if sum(team.is_active for team in locked.ranges) == 1:
            if agency_manager_id is None:
                raise TopologyMutationError("agency_manager_required")
            agency = _user(locked, agency_manager_id, User.Role.AGENCY_MANAGER)
            authority_id = agency.pk
        elif agency_manager_id is not None and str(agency_manager_id) != str(authority_id):
            raise TopologyMutationError("agency_manager_authority_mismatch")
        row.is_active = True
        _save_range(row, update_fields=["is_active", "updated_at"])
        require_valid_locked_topology(locked.workspace)
        _verify_authority(locked.workspace, authority_id)
    return row
