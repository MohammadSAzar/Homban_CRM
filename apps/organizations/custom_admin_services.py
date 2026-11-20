"""Workspace -> local Users by PK -> grants. No recursive delegation."""
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from .custom_admin_catalog import AdminPermission
from .custom_admin_policy import require_grant_authority
from .models import Workspace, CustomAdminGrant


def _lock_context(actor, user_id=None):
    workspace = Workspace.objects.select_for_update().filter(pk=actor.workspace_id, is_active=True).first()
    if workspace is None:
        raise PermissionDenied(_("دسترسی به مجموعه مجاز نیست."))
    ids = {actor.pk}
    if user_id is not None:
        ids.add(user_id)
    users = {str(u.pk): u for u in User.objects.select_for_update().filter(workspace=workspace, pk__in=ids).order_by("pk")}
    current = users.get(str(actor.pk))
    if current is None:
        raise PermissionDenied(_("دسترسی به مجموعه مجاز نیست."))
    current.workspace = workspace
    require_grant_authority(current)
    return workspace, current, users


@transaction.atomic
def create_grant(*, actor, user_id, permission):
    workspace, current, users = _lock_context(actor, user_id)
    if permission not in AdminPermission.values:
        raise ValidationError({"permission": _("مجوز نامعتبر است.")})
    target = users.get(str(user_id))
    if target is None or not target.is_active or target.role not in User.Role.values:
        raise ValidationError({"user_id": _("کاربر انتخاب‌شده معتبر نیست.")})
    if target.pk == current.pk:
        raise ValidationError({"user_id": _("مدیر مجاز مجموعه نیازی به اعطای مجوز ندارد.")})
    if CustomAdminGrant.objects.filter(workspace=workspace, user=target, permission=permission).exists():
        raise ValidationError({"permission": _("این مجوز قبلاً اعطا شده است.")})
    return CustomAdminGrant.objects.create(workspace=workspace, user=target, permission=permission, granted_by=current)


@transaction.atomic
def revoke_grant(*, actor, grant_id):
    workspace, current, users = _lock_context(actor)
    row = CustomAdminGrant.objects.select_for_update().filter(pk=grant_id, workspace=workspace).first()
    if row is None:
        raise NotFound(_("مجوز یافت نشد."))
    row.delete()


@transaction.atomic
def list_grants(*, actor):
    workspace, current, users = _lock_context(actor)
    return list(CustomAdminGrant.objects.filter(workspace=workspace, user__workspace=workspace)
                .select_related("user").order_by("created_at", "pk"))
