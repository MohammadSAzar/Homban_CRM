"""Workspace-scoped official announcements; private conversations are independent."""
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.services import publish_notification
from .models import Announcement, AnnouncementPublisher, MAX_TEXT_LENGTH, _CHAT_WRITE
from .services import lock_actor

DELEGATED_ROLES = (User.Role.RANGE_MANAGER, User.Role.SECRETARY)
RECIPIENT_BATCH_SIZE = 100


def require_manager(actor):
    if actor.role != User.Role.AGENCY_MANAGER:
        raise PermissionDenied(_('فقط مدیر املاک می‌تواند مجوز انتشار را مدیریت کند.'))


def can_publish(actor):
    return actor.role == User.Role.AGENCY_MANAGER or (
        actor.role in DELEGATED_ROLES and AnnouncementPublisher.objects.filter(
            workspace_id=actor.workspace_id, user=actor).exists())


def list_announcements(actor):
    # Caller retains its transaction through pagination/serialization, like private Chat.
    actor = lock_actor(actor)
    return Announcement.objects.filter(workspace_id=actor.workspace_id).select_related('author').order_by('-created_at', 'pk')


def list_publishers(actor):
    actor = lock_actor(actor)
    require_manager(actor)
    return AnnouncementPublisher.objects.filter(workspace_id=actor.workspace_id).select_related('user').order_by('-created_at', 'pk')


def _notify_announcement(row):
    """Trusted, in-transaction producer. Keyset batches avoid loading all members.

    The caller holds the Workspace lock, shared with user-management services.
    Every publication also rechecks and locks its recipient through Notification services.
    Errors propagate so the announcement and all recipient notifications roll back together.
    """
    last = None
    while True:
        recipients = User.objects.filter(workspace_id=row.workspace_id, is_active=True,
            role__in=User.Role.values).exclude(pk=row.author_id).order_by('pk')
        if last is not None:
            recipients = recipients.filter(pk__gt=last)
        batch = list(recipients[:RECIPIENT_BATCH_SIZE])
        if not batch:
            return
        for recipient in batch:
            publish_notification(recipient=recipient, kind=Notification.Kind.ANNOUNCEMENT,
                event_key=f'announcement:{row.pk}:created', title=_('اطلاعیه جدید'),
                message=_('یک اطلاعیه جدید در کانال رسمی مجموعه منتشر شد.'),
                action_url='/api/v1/chat/announcements/', source_type='announcement', source_id=row.pk)
        last = batch[-1].pk


@transaction.atomic
def publish_announcement(*, actor, text):
    actor = lock_actor(actor)
    if not can_publish(actor):
        raise PermissionDenied(_('اجازه انتشار اطلاعیه ندارید.'))
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT_LENGTH:
        raise ValidationError({'text': _('متن اطلاعیه الزامی و حداکثر ۴۰۰۰ نویسه است.')})
    row = Announcement(workspace=actor.workspace, author=actor, text=text.strip())
    row.save(_token=_CHAT_WRITE)
    _notify_announcement(row)
    from .announcement_realtime import schedule_announcement
    schedule_announcement(row)
    return row


@transaction.atomic
def delegate_publisher(*, actor, user_id):
    actor = lock_actor(actor)
    require_manager(actor)
    target = User.objects.select_for_update().filter(pk=user_id, workspace=actor.workspace,
        is_active=True, role__in=DELEGATED_ROLES).first()
    if target is None:
        raise NotFound(_('ناشر واجد شرایط یافت نشد.'))
    row = AnnouncementPublisher.objects.filter(workspace=actor.workspace, user=target).first()
    if row is not None:
        row.user = target
        return row, False
    row = AnnouncementPublisher(workspace=actor.workspace, user=target)
    row.save(_token=_CHAT_WRITE)
    return row, True


@transaction.atomic
def remove_publisher(*, actor, delegation_id):
    actor = lock_actor(actor)
    require_manager(actor)
    row = AnnouncementPublisher.objects.filter(pk=delegation_id, workspace=actor.workspace).first()
    if row is None:
        raise NotFound(_('مجوز انتشار یافت نشد.'))
    row.delete(_token=_CHAT_WRITE)
