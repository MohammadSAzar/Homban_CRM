"""Internal topology primitives, never customer authorization or automatic repair.

Trusted callers must authorize separately. Cooperating mutations lock Workspace,
then Users by PK, then Ranges by PK, then dependent rows. Callers must declare
existing rows up front and must not mutate another Workspace inside the boundary.
"""
from contextlib import contextmanager
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.ranges.models import Range

from .models import Workspace
from .topology import validate_workspace_topology


class TopologyMutationError(Exception):
    def __init__(self, *errors, status="invalid", topology="unresolved"):
        self.errors = tuple(sorted(set(errors)))
        self.status = status
        self.topology = topology
        super().__init__(", ".join(self.errors))


@dataclass(frozen=True)
class LockedTopology:
    workspace: Workspace
    users: tuple[User, ...]
    ranges: tuple[Range, ...]


def require_valid_locked_topology(workspace):
    """Caller must hold the Workspace lock within its mutation transaction."""
    result = validate_workspace_topology(workspace)
    if result.status != "valid":
        raise TopologyMutationError(*result.errors, status=result.status, topology=result.topology)
    return result


@contextmanager
def topology_transaction(*, workspace_id, user_ids=(), range_ids=(), include_current_topology=False):
    """Yield current locked rows; validate persisted final state before exit.

    Missing/foreign requested rows are rejected, never locked or returned. Nested
    use is a savepoint: durability and on_commit callbacks await the outer commit.
    This boundary does not require existing authority, allowing explicit internal
    bootstrap/repair of invalid states. It grants no customer permission.
    Transitions may discover active topology IDs under the Workspace lock before
    acquiring any User/Range locks, preserving the same deterministic lock order.
    """
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().filter(pk=workspace_id).first()
        if workspace is None:
            raise TopologyMutationError("workspace_missing")
        user_ids, range_ids = set(map(str, user_ids)), set(map(str, range_ids))
        if include_current_topology:
            active_ranges = list(Range.objects.filter(workspace=workspace, is_active=True)
                                 .values_list("pk", "manager_id"))
            range_ids.update(str(pk) for pk, _ in active_ranges)
            user_ids.update(str(manager) for _, manager in active_ranges if manager is not None)
            user_ids.update(str(pk) for pk in User.objects.filter(
                workspace=workspace, is_active=True, role=User.Role.AGENCY_MANAGER,
            ).values_list("pk", flat=True))
        users = tuple(User.objects.select_for_update().filter(
            workspace=workspace, pk__in=user_ids,
        ).order_by("pk"))
        if len(users) != len(user_ids):
            raise TopologyMutationError("user_missing_or_wrong_workspace")
        ranges = tuple(Range.objects.select_for_update().filter(
            workspace=workspace, pk__in=range_ids,
        ).order_by("pk"))
        if len(ranges) != len(range_ids):
            raise TopologyMutationError("range_missing_or_wrong_workspace")
        yield LockedTopology(workspace, users, ranges)
        require_valid_locked_topology(workspace)


def bootstrap_team_first_range(*, workspace_id, manager_id, range_name,
                               activity_scope=Range.ActivityScope.ALL):
    """Existing zero-active-Range Team -> single-team; internal callers only."""
    try:
        with topology_transaction(workspace_id=workspace_id, user_ids=(manager_id,)) as locked:
            workspace = locked.workspace
            if not workspace.is_active:
                raise TopologyMutationError("workspace_inactive")
            if workspace.workspace_mode != Workspace.WorkspaceMode.TEAM:
                raise TopologyMutationError("workspace_not_team")
            if Range.objects.filter(workspace=workspace, is_active=True).exists():
                raise TopologyMutationError("team_already_has_active_range")
            manager = locked.users[0]
            if not manager.is_active:
                raise TopologyMutationError("manager_inactive")
            if manager.role != User.Role.RANGE_MANAGER:
                raise TopologyMutationError("manager_wrong_role")
            if Range.objects.filter(manager=manager, is_active=True).exists():
                raise TopologyMutationError("range_manager_multiple_active_ranges")
            team = Range(workspace=workspace, manager=manager, name=range_name,
                         activity_scope=activity_scope, is_active=True)
            try:
                team.save()  # Existing Range.full_clean(), including name uniqueness.
            except ValidationError as error:
                raise TopologyMutationError("range_validation_failed") from error
        return team
    except TopologyMutationError as error:
        if error.errors == ("user_missing_or_wrong_workspace",):
            code = "manager_wrong_workspace" if User.objects.filter(pk=manager_id).exists() else "manager_missing"
            raise TopologyMutationError(code) from error
        raise
