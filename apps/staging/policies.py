"""Current review scope; no purchaser flags or new Range review authority."""
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, NotFound
from apps.accounts.models import User
from apps.accounts.authentication import customer_can_authenticate
from apps.organizations.capabilities import resolve_workspace_capability
from apps.organizations.authority import resolve_workspace_authority
from apps.organizations.models import Workspace
from .models import StagedRecord


def review_actor(actor):
    if not getattr(actor, "is_authenticated", False):
        raise PermissionDenied(_("دسترسی مجاز نیست."))
    current = User.objects.select_related("workspace").filter(pk=actor.pk, workspace_id=actor.workspace_id).first()
    if not customer_can_authenticate(current) or not resolve_workspace_capability(current.workspace, "staging").allowed:
        raise PermissionDenied(_("دسترسی به پیش‌نویس فعال نیست."))
    return current


def review_scope(current, queryset=None):
    rows = StagedRecord.objects.all() if queryset is None else queryset
    rows = rows.filter(workspace_id=current.workspace_id)
    if current.workspace.workspace_mode == Workspace.WorkspaceMode.PERSONAL:
        return rows
    authority = resolve_workspace_authority(current.workspace)
    if authority.status == "resolved" and authority.authority_user.pk == current.pk:
        return rows
    return rows.filter(created_by_id=current.pk)


def visible_staged_records(actor):
    return review_scope(review_actor(actor)).order_by("-created_at", "pk")


def review_record(actor, record_id):
    row = visible_staged_records(actor).filter(pk=record_id).first()
    if row is None:
        raise NotFound(_("پیش‌نویس یافت نشد."))
    return row
