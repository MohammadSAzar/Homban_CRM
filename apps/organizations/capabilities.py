"""Read-only product-mode eligibility; not authentication, authority or topology."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .models import Workspace


CORE = frozenset({
    "property_files", "customers", "own_matching", "matching_profile",
    "manual_tasks", "calendar", "notifications", "geography",
})
TEAM_ONLY = frozenset({
    "chat", "announcements", "general_group", "collaboration",
    "cross_consultant_matching", "cross_consultant_pass",
})
TOPOLOGY_DEPENDENT_TEAM = frozenset({
    "user_management", "range_management", "workspace_administration",
    "global_reporting", "map_territory_assignment", "custom_admin_permissions",
})
CAPABILITY_CLASSES = MappingProxyType({
    **dict.fromkeys(CORE, "core"),
    **dict.fromkeys(TEAM_ONLY, "team_only"),
    **dict.fromkeys(TOPOLOGY_DEPENDENT_TEAM, "topology_dependent_team"),
})


@dataclass(frozen=True)
class WorkspaceCapabilityResolution:
    allowed: bool
    capability: str
    workspace_mode: str | None
    capability_class: Literal["core", "team_only", "topology_dependent_team"] | None
    reason: str


def resolve_workspace_capability(workspace: Workspace, capability: str) -> WorkspaceCapabilityResolution:
    """Read persisted mode in one query, ignoring purchaser/user identity.

    Allowed means product-mode eligible ONLY. Callers independently enforce
    authentication, Workspace activity/isolation, authority, topology and privacy.
    Missing Workspaces, like NULL mode, fail closed. Unknown IDs have no class.
    E1 supplies this foundation without migrating any feature consumers.
    """
    mode = Workspace.objects.filter(pk=workspace.pk).values_list("workspace_mode", flat=True).first()
    capability_class = CAPABILITY_CLASSES.get(capability)
    if capability_class is None:
        allowed, reason = False, "unknown_capability"
    elif mode not in Workspace.WorkspaceMode.values:
        allowed, reason = False, "workspace_mode_unresolved"
    elif mode == Workspace.WorkspaceMode.PERSONAL and capability_class != "core":
        allowed, reason = False, "capability_not_available_in_personal"
    else:
        allowed = True
        reason = "mode_eligible" if capability_class == "topology_dependent_team" else "allowed"
    return WorkspaceCapabilityResolution(allowed, capability, mode, capability_class, reason)
