"""Private chat commands. Workspace locks serialize pair creation, send and read."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Case, Count, DateTimeField, ExpressionWrapper, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Substr
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.services import publish_notification
from apps.organizations.models import Workspace
from .models import Conversation, Message, MAX_TEXT_LENGTH, _CHAT_WRITE


def lock_actor(actor):
    workspace = Workspace.objects.select_for_update().filter(pk=actor.workspace_id, is_active=True).first()
    user = User.objects.select_for_update().filter(pk=actor.pk, workspace=workspace).first() if workspace else None
    if user is not None:
        user.workspace = workspace
    if not customer_can_authenticate(user) or user.role not in User.Role.values:
        raise PermissionDenied(_('دسترسی به گفتگو مجاز نیست.'))
    return user


def conversations(actor):
    return Conversation.objects.filter(
        Q(participant_a=actor) | Q(participant_b=actor), workspace_id=actor.workspace_id,
        participant_a__workspace_id=F('workspace_id'), participant_b__workspace_id=F('workspace_id'),
        participant_a__is_active=True, participant_b__is_active=True,
        participant_a__role__in=User.Role.values, participant_b__role__in=User.Role.values,
    ).select_related('participant_a', 'participant_b')


def summaries(actor):
    rows = conversations(actor).annotate(read_marker=Case(
        When(participant_a=actor, then=F('participant_a_read_at')),
        default=F('participant_b_read_at'), output_field=DateTimeField()))
    latest = Message.objects.filter(conversation_id=OuterRef('pk')).order_by('-created_at', '-pk')
    # NULL markers mean nothing has been read; keep the condition in the correlated query.
    unread = Message.objects.filter(conversation_id=OuterRef('pk')).exclude(sender=actor).annotate(
        marker=ExpressionWrapper(OuterRef('read_marker'), output_field=DateTimeField())
    ).filter(Q(marker__isnull=True) | Q(created_at__gt=F('marker'))
    ).order_by().values('conversation_id').annotate(total=Count('pk')).values('total')
    return rows.annotate(
        unread_count=Coalesce(Subquery(unread), Value(0)),
        last_preview=Subquery(latest.annotate(preview=Substr('text', 1, 120)).values('preview')[:1]),
        last_message_at=Subquery(latest.values('created_at')[:1]),
    ).order_by('-updated_at', 'pk')


def owned_conversation(actor, pk):
    row = conversations(actor).filter(pk=pk).first()
    if row is None:
        raise NotFound(_('گفتگو یافت نشد.'))
    return row


@transaction.atomic
def open_conversation(*, actor, participant_id):
    actor = lock_actor(actor)
    if participant_id == actor.pk:
        raise ValidationError({'participant_id': _('گفتگو با خود مجاز نیست.')})
    target = User.objects.select_for_update().filter(pk=participant_id, workspace=actor.workspace,
        is_active=True, role__in=User.Role.values).first()
    if target is None:
        raise NotFound(_('کاربر یافت نشد.'))
    first, second = sorted((actor, target), key=lambda user: user.pk)
    row = Conversation.objects.filter(workspace=actor.workspace, participant_a=first, participant_b=second).first()
    if row is not None:
        return row, False
    row = Conversation(workspace=actor.workspace, participant_a=first, participant_b=second)
    row.save(_token=_CHAT_WRITE)
    return row, True


def notify_message(message, recipient):
    """Trusted send-path hook; repeat publication of the same message is idempotent."""
    return publish_notification(recipient=recipient, kind=Notification.Kind.CHAT,
        event_key=f'chat-message:{message.pk}:created', title=_('پیام جدید'),
        message=_('یک پیام جدید در گفتگوی خصوصی دریافت کردید.'),
        action_url=f'/api/v1/chat/conversations/{message.conversation_id}/',
        source_type='chat_message', source_id=message.pk)


@transaction.atomic
def send_message(*, actor, pk, text):
    actor = lock_actor(actor)
    row = owned_conversation(actor, pk)
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT_LENGTH:
        raise ValidationError({'text': _('متن پیام باید بین ۱ تا ۴۰۰۰ نویسه باشد.')})
    last = row.messages.order_by('-created_at', '-pk').values_list('created_at', flat=True).first()
    # Strictly increasing instants make read watermarks safe even on clock ties/rollback.
    stamp = max(timezone.now(), last + timedelta(microseconds=1)) if last else timezone.now()
    message = Message(conversation=row, sender=actor, text=text.strip(), created_at=stamp)
    message.save(_token=_CHAT_WRITE)
    row.updated_at = stamp
    row.save(_token=_CHAT_WRITE)
    recipient = row.participant_b if actor.pk == row.participant_a_id else row.participant_a
    notify_message(message, recipient)
    from .realtime import schedule_message
    schedule_message(message)
    return message


@transaction.atomic
def mark_conversation_read(*, actor, pk):
    actor = lock_actor(actor)
    row = owned_conversation(actor, pk)
    last = row.messages.order_by('-created_at', '-pk').values_list('created_at', flat=True).first()
    field = 'participant_a_read_at' if actor.pk == row.participant_a_id else 'participant_b_read_at'
    if last is not None and getattr(row, field) != last:
        setattr(row, field, last)
        row.save(_token=_CHAT_WRITE)
    return row
