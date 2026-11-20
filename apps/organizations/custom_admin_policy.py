"""Delegated admin eligibility only; never replaces a feature's own policy."""
from django.db.models import F
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from .authority import resolve_workspace_authority
from .capabilities import resolve_workspace_capability
from .custom_admin_catalog import AdminPermission
from .models import CustomAdminGrant


def current_custom_admin_actor(actor):
    if not getattr(actor, "is_authenticated", False):
        return None
    current = User.objects.select_related("workspace").filter(pk=actor.pk).first()
    if (not customer_can_authenticate(current) or current.role not in User.Role.values
            or not resolve_workspace_capability(current.workspace, "custom_admin_permissions").allowed):
        return None
    return current


def require_custom_admin_access(actor):
    current = current_custom_admin_actor(actor)
    if current is None:
        raise PermissionDenied(_("دسترسی به مدیریت سفارشی مجاز نیست."))
    return current


def require_grant_authority(actor):
    current = require_custom_admin_access(actor)
    authority = resolve_workspace_authority(current.workspace)
    if authority.status != "resolved" or authority.authority_user.pk != current.pk:
        raise PermissionDenied(_("فقط مدیر مجاز مجموعه می‌تواند مجوزها را مدیریت کند."))
    return current


def has_custom_admin_permission(actor, permission):
    if permission not in AdminPermission.values:
        return False
    current = current_custom_admin_actor(actor)
    if current is None:
        return False
    authority = resolve_workspace_authority(current.workspace)
    if authority.status != "resolved":
        return False
    if authority.authority_user.pk == current.pk:
        return True
    return CustomAdminGrant.objects.filter(
        workspace=current.workspace, user=current, permission=permission,
        user__workspace_id=F("workspace_id"), granted_by__workspace_id=F("workspace_id"),
    ).exists()


def custom_admin_current_state(actor):
    """Read fresh stored grants, not synthetic authority powers or action access.

    Operational means catalog-active only; consumers still enforce local scope.
    Malformed legacy tenant links and unknown permission IDs never apply.
    """
    from .custom_admin_catalog import permission_metadata

    current = require_custom_admin_access(actor)
    authority = resolve_workspace_authority(current.workspace)
    if authority.status != "resolved":
        raise PermissionDenied(_("وضعیت مدیریت مجموعه معتبر نیست."))
    stored = set(CustomAdminGrant.objects.filter(
        workspace=current.workspace, user=current, permission__in=AdminPermission.values,
        user__workspace_id=F("workspace_id"), granted_by__workspace_id=F("workspace_id"),
    ).values_list("permission", flat=True))
    granted = [permission for permission in AdminPermission.values if permission in stored]
    return {
        "is_authority": authority.authority_user.pk == current.pk,
        "granted_permissions": granted,
        "operational_permissions": [permission for permission in granted
                                    if permission_metadata(permission)["operational"]],
    }
