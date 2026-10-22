"""Read-only target authority resolution; does not authorize endpoint access."""
from dataclasses import dataclass
from typing import Literal

from apps.accounts.models import User
from apps.ranges.models import Range

from .models import Workspace


@dataclass(frozen=True)
class WorkspaceAuthorityResolution:
    status: Literal["resolved", "none", "invalid"]
    authority_user: User | None
    topology: Literal["personal", "single_team", "multi_team", "unresolved"]
    reason: str  # Internal diagnostic code, not customer-facing text.


def resolve_workspace_authority(workspace: Workspace) -> WorkspaceAuthorityResolution:
    """Reload current DB state without locks, writes, caching or legacy inference.

    Personal legitimately has no authority; unresolved mode and malformed Team
    topology are invalid. Repeated managers across Ranges remain schema-permitted.
    This is not an access check (including Workspace activation). Future consumers
    must enforce access and use their Workspace transaction lock where necessary.
    """
    current = Workspace.objects.only("workspace_mode").filter(pk=workspace.pk).first()
    if current is None:
        return WorkspaceAuthorityResolution("invalid", None, "unresolved", "workspace_missing")
    if current.workspace_mode == Workspace.WorkspaceMode.PERSONAL:
        return WorkspaceAuthorityResolution("none", None, "personal", "personal_mode")
    if current.workspace_mode != Workspace.WorkspaceMode.TEAM:
        return WorkspaceAuthorityResolution("invalid", None, "unresolved", "unresolved_mode")

    ranges = Range.objects.filter(workspace_id=current.pk, is_active=True)
    count = ranges.count()
    if not count:
        return WorkspaceAuthorityResolution("invalid", None, "unresolved", "no_active_ranges")
    topology = "single_team" if count == 1 else "multi_team"
    if ranges.exclude(
        manager__is_active=True,
        manager__workspace_id=current.pk,
        manager__role=User.Role.RANGE_MANAGER,
    ).exists():
        return WorkspaceAuthorityResolution("invalid", None, topology, "invalid_range_manager")

    if count == 1:
        manager = ranges.select_related("manager").get().manager
        return WorkspaceAuthorityResolution("resolved", manager, topology, "single_range_manager")

    # Fetch at most two candidates: enough to reject ambiguous cardinality.
    candidates = list(User.objects.filter(
        workspace_id=current.pk, is_active=True, role=User.Role.AGENCY_MANAGER,
    ).order_by("pk")[:2])
    if len(candidates) != 1:
        return WorkspaceAuthorityResolution("invalid", None, topology, "agency_manager_cardinality")
    return WorkspaceAuthorityResolution("resolved", candidates[0], topology, "single_agency_manager")
