"""Read-only target topology validation, independent of authority and permissions."""
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from apps.accounts.models import User
from apps.ranges.models import Range

from .models import Workspace


@dataclass(frozen=True)
class TopologyValidationResult:
    status: Literal["valid", "invalid", "unresolved"]
    mode: str | None
    topology: Literal["personal", "single_team", "multi_team", "unresolved"]
    errors: tuple[str, ...]


def validate_workspace_topology(workspace: Workspace) -> TopologyValidationResult:
    """Read current persisted state without locks or writes.

    Errors are unique and lexically ordered. Query count is constant; only active
    Range manager projections are loaded. Future transactional callers must hold
    their own Workspace lock when they need a consistent mutation boundary.
    """
    current = Workspace.objects.only("workspace_mode").filter(pk=workspace.pk).first()
    if current is None:
        return TopologyValidationResult("invalid", None, "unresolved", ("workspace_missing",))
    mode = current.workspace_mode
    if mode not in Workspace.WorkspaceMode.values:
        return TopologyValidationResult("unresolved", mode, "unresolved", ("unresolved_mode",))

    ranges = Range.objects.filter(workspace_id=current.pk, is_active=True)
    if mode == Workspace.WorkspaceMode.PERSONAL:
        errors = ("personal_has_active_ranges",) if ranges.exists() else ()
        return TopologyValidationResult("invalid" if errors else "valid", mode, "personal", errors)

    managers = list(ranges.values(
        "manager_id", "manager__is_active", "manager__workspace_id", "manager__role",
    ))
    if not managers:
        return TopologyValidationResult("invalid", mode, "unresolved", ("team_has_no_active_ranges",))
    errors = set()
    for manager in managers:
        if manager["manager_id"] is None:
            errors.add("active_range_missing_manager")
            continue
        if not manager["manager__is_active"]:
            errors.add("active_range_manager_inactive")
        if manager["manager__workspace_id"] != current.pk:
            errors.add("active_range_manager_wrong_workspace")
        if manager["manager__role"] != User.Role.RANGE_MANAGER:
            errors.add("active_range_manager_wrong_role")
    counts = Counter(row["manager_id"] for row in managers if row["manager_id"] is not None)
    if any(count > 1 for count in counts.values()):
        errors.add("range_manager_multiple_active_ranges")
    topology = "single_team" if len(managers) == 1 else "multi_team"
    if topology == "multi_team":
        candidates = list(User.objects.filter(
            workspace_id=current.pk, is_active=True, role=User.Role.AGENCY_MANAGER,
        ).values_list("pk", flat=True)[:2])
        if len(candidates) != 1:
            errors.add("multi_team_agency_manager_cardinality")
    return TopologyValidationResult("invalid" if errors else "valid", mode, topology, tuple(sorted(errors)))
