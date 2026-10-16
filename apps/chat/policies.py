"""Current Chat-only team policy; no purchaser type or privilege-flag authority."""
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User


def workspace_chat_authority_role(workspace_id):
    """Read current topology, without caching it across requests or socket events."""
    roles = set(User.objects.filter(
        workspace_id=workspace_id, workspace__is_active=True, is_active=True,
        role__in=(User.Role.AGENCY_MANAGER, User.Role.RANGE_MANAGER),
    ).values_list('role', flat=True).distinct()) if workspace_id else set()
    if User.Role.AGENCY_MANAGER in roles:
        return User.Role.AGENCY_MANAGER
    if User.Role.RANGE_MANAGER in roles:
        return User.Role.RANGE_MANAGER
    return None


def require_chat_access(actor):
    """Actor is freshly loaded by the Workspace-first lock_actor boundary."""
    if (not customer_can_authenticate(actor) or actor.role not in User.Role.values
            or workspace_chat_authority_role(actor.workspace_id) is None):
        raise PermissionDenied(_('دسترسی به گفتگو مجاز نیست.'))


def has_workspace_chat_authority(actor):
    return (customer_can_authenticate(actor)
            and actor.role == workspace_chat_authority_role(actor.workspace_id))


def require_workspace_chat_authority(actor):
    if not has_workspace_chat_authority(actor):
        raise PermissionDenied(_('فقط مدیر مجاز گفتگوی مجموعه می‌تواند مجوز انتشار را مدیریت کند.'))
