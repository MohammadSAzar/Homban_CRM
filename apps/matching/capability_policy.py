"""Matching entitlement only; callers retain tenant, scope and privacy checks."""
from django.db.models import F, Subquery
from apps.organizations.capabilities import resolve_workspace_capability
from apps.organizations.models import Workspace


def cross_consultant_allowed(workspace):
    return resolve_workspace_capability(workspace, "cross_consultant_matching").allowed


def pair_entitled(workspace, file, customer, *, cross_allowed=None):
    if file.assigned_to_id == customer.assigned_to_id:
        return True
    return cross_consultant_allowed(workspace) if cross_allowed is None else cross_allowed


def entitled_recommendations(queryset, viewer):
    # Read current membership; stale actor objects must not grant old-Workspace entitlement.
    from apps.accounts.models import User
    workspace_id = Subquery(User.objects.filter(pk=viewer.pk).values("workspace_id")[:1])
    if cross_consultant_allowed(Workspace(pk=workspace_id)):
        return queryset
    return queryset.filter(property_file__assigned_to_id=F("customer__assigned_to_id"))
