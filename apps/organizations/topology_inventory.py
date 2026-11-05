"""Internal, read-only inventory. Canonical validity/authority remain D2a/C1.

No consistent-snapshot guarantee under concurrent writes: a future apply service
must lock Workspace and recheck the token. Query count is bounded per Workspace,
not per Range/user; the multi-Workspace iterator deliberately reuses C1/D2a.
"""
from dataclasses import dataclass
from hashlib import sha256
import json

from apps.accounts.models import User
from apps.ranges.models import Range
from .authority import resolve_workspace_authority
from .models import Workspace
from .topology import validate_workspace_topology


@dataclass(frozen=True)
class WorkspaceTopologyInventory:
    workspace_id: str
    workspace_name: str
    workspace_mode: str | None
    workspace_active: bool
    validation_status: str
    topology: str
    d2a_errors: tuple[str, ...]
    authority_status: str
    authority_user_id: str | None
    authority_reason: str
    active_range_count: int
    inactive_range_count: int
    active_range_ids: tuple[str, ...]
    inactive_range_ids: tuple[str, ...]
    active_range_manager_ids: tuple[str, ...]
    active_agency_manager_ids: tuple[str, ...]
    owner_user_ids: tuple[str, ...]
    unresolved_mode: bool
    owner_agency_blocker: bool
    issue_classes: tuple[str, ...]
    state_token: str


def inventory_workspace(workspace_id):
    workspace = Workspace.objects.get(pk=workspace_id)
    validation = validate_workspace_topology(workspace)
    authority = resolve_workspace_authority(workspace)
    ranges = list(Range.objects.filter(workspace=workspace).order_by("pk").values(
        "id", "name", "is_active", "activity_scope", "manager_id",
        "manager__workspace_id", "manager__role", "manager__is_active",
    ))
    users = list(User.objects.filter(workspace=workspace).order_by("pk").values(
        "id", "role", "is_active", "is_workspace_owner",
    ))
    active = tuple(str(row["id"]) for row in ranges if row["is_active"])
    inactive = tuple(str(row["id"]) for row in ranges if not row["is_active"])
    agencies = tuple(str(row["id"]) for row in users if row["is_active"] and row["role"] == "agency_manager")
    owners = tuple(str(row["id"]) for row in users if row["is_workspace_owner"])
    owner_blocker = bool(set(agencies) & set(owners))
    aliases = {"unresolved_mode": "unresolved_workspace_mode", "team_has_no_active_ranges": "team_no_active_ranges"}
    issues = {aliases.get(code, code) for code in validation.errors}
    if "multi_team_agency_manager_cardinality" in issues:
        issues.remove("multi_team_agency_manager_cardinality")
        issues.add("multi_team_no_active_agency_manager" if not agencies else "multi_team_multiple_active_agency_managers")
    if owner_blocker:
        issues.add("owner_agency_repair_blocker")
    if any(not row["is_active"] and row["manager_id"] and (
        row["manager__workspace_id"] != workspace.pk or row["manager__role"] != "range_manager"
    ) for row in ranges):
        issues.add("inactive_historical_manager_anomaly")
    # Include local candidate eligibility and foreign FK eligibility projections,
    # not passwords, usernames, phone numbers or purchaser-based inference.
    snapshot = [str(workspace.pk), workspace.workspace_mode, workspace.is_active,
                workspace.updated_at.isoformat(), ranges, users]
    token = sha256(json.dumps(snapshot, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    return WorkspaceTopologyInventory(
        str(workspace.pk), workspace.name, workspace.workspace_mode, workspace.is_active,
        validation.status, validation.topology, validation.errors, authority.status,
        str(authority.authority_user.pk) if authority.authority_user else None, authority.reason,
        len(active), len(inactive), active, inactive,
        tuple(sorted({str(row["manager_id"]) for row in ranges if row["is_active"] and row["manager_id"]})),
        agencies, owners, workspace.workspace_mode is None, owner_blocker, tuple(sorted(issues)), token,
    )


def inventory_workspaces(workspace_ids=None):
    workspaces = Workspace.objects.order_by("pk")
    if workspace_ids is not None:
        workspaces = workspaces.filter(pk__in=workspace_ids)
    for workspace_id in workspaces.values_list("pk", flat=True).iterator():
        yield inventory_workspace(workspace_id)
