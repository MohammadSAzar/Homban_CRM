"""One optional Workspace group, using the existing Chat authorization boundary."""
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import GeneralGroupMessage, GeneralGroupSettings, MAX_TEXT_LENGTH, _CHAT_WRITE
from .policies import require_workspace_chat_authority
from .services import lock_actor


def _is_enabled(actor):
    return GeneralGroupSettings.objects.filter(workspace_id=actor.workspace_id, is_enabled=True).exists()


@transaction.atomic
def general_group_state(actor):
    actor = lock_actor(actor)
    return {'is_enabled': _is_enabled(actor)}


@transaction.atomic
def set_general_group_enabled(*, actor, is_enabled):
    actor = lock_actor(actor)
    require_workspace_chat_authority(actor)
    if type(is_enabled) is not bool:
        raise ValidationError({'is_enabled': _('مقدار فعال بودن باید درست یا نادرست باشد.')})
    row = GeneralGroupSettings.objects.filter(workspace=actor.workspace).first()
    if row is None:
        row = GeneralGroupSettings(workspace=actor.workspace, is_enabled=is_enabled)
        row.save(_token=_CHAT_WRITE)
    elif row.is_enabled != is_enabled:
        row.is_enabled = is_enabled
        row.updated_at = timezone.now()
        row.save(_token=_CHAT_WRITE)
    return {'is_enabled': row.is_enabled}


def _require_enabled(actor):
    if not _is_enabled(actor):
        raise PermissionDenied(_('گروه عمومی فعال نیست.'))


def require_general_group_access(actor):
    """Current-state boundary, called inside a Workspace-first transaction."""
    actor = lock_actor(actor)
    _require_enabled(actor)
    return actor


def list_general_group_messages(actor):
    # The view retains its Workspace-first transaction through pagination/serialization.
    actor = require_general_group_access(actor)
    return GeneralGroupMessage.objects.filter(workspace_id=actor.workspace_id).select_related(
        'sender').order_by('created_at', 'pk')


@transaction.atomic
def send_general_group_message(*, actor, text):
    actor = require_general_group_access(actor)
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT_LENGTH:
        raise ValidationError({'text': _('متن پیام الزامی و حداکثر ۴۰۰۰ نویسه است.')})
    row = GeneralGroupMessage(workspace=actor.workspace, sender=actor, text=text.strip())
    row.save(_token=_CHAT_WRITE)
    from .general_group_realtime import schedule_message
    schedule_message(row)
    return row
