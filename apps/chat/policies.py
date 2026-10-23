"""Current Chat-only team policy; no purchaser type or privilege-flag authority."""
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from apps.organizations.authority import resolve_workspace_authority


def chat_is_available(workspace_id):
    """Temporary manager-presence availability, independent of canonical authority."""
    return bool(workspace_id) and User.objects.filter(
        workspace_id=workspace_id, workspace__is_active=True, is_active=True,
        role__in=(User.Role.AGENCY_MANAGER, User.Role.RANGE_MANAGER),
    ).exists()


def require_chat_access(actor):
    """Actor is freshly loaded by the Workspace-first lock_actor boundary."""
    if (not customer_can_authenticate(actor) or actor.role not in User.Role.values
            or not chat_is_available(actor.workspace_id)):
        raise PermissionDenied(_('دسترسی به گفتگو مجاز نیست.'))


def has_workspace_chat_authority(actor):
    if not customer_can_authenticate(actor):
        return False
    resolution = resolve_workspace_authority(actor.workspace)
    return (resolution.status == 'resolved'
            and resolution.authority_user.pk == actor.pk)


def require_workspace_chat_authority(actor):
    if not has_workspace_chat_authority(actor):
        raise PermissionDenied(_('فقط مدیر مجاز گفتگوی مجموعه می‌تواند مجوز انتشار را مدیریت کند.'))
