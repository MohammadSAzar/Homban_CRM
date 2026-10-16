"""Purpose-bound announcement subscriptions and best-effort, post-commit delivery."""
import logging
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core import signing
from django.db import transaction
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from .announcement_serializers import announcement_data
from .models import Announcement
from .realtime import TICKET_AGE
from .services import lock_actor

logger = logging.getLogger(__name__)
TICKET_SALT = 'homban.chat.announcements.websocket.v1'


def group_name(workspace_id):
    return f'chat.announcements.{UUID(str(workspace_id)).hex}'


@transaction.atomic
def create_ticket(*, actor):
    actor = lock_actor(actor)
    return signing.dumps({'user': str(actor.pk), 'workspace': str(actor.workspace_id),
                          'purpose': 'announcement'}, salt=TICKET_SALT)


def parse_ticket(ticket):
    try:
        claims = signing.loads(ticket, salt=TICKET_SALT, max_age=TICKET_AGE)
        if (not isinstance(claims, dict) or set(claims) != {'user', 'workspace', 'purpose'}
                or claims['purpose'] != 'announcement'):
            return None
        return {'user': UUID(claims['user']), 'workspace': UUID(claims['workspace']),
                'purpose': 'announcement'}
    except (signing.BadSignature, ValueError, TypeError, AttributeError):
        return None


def authorized_actor(claims):
    actor = User.objects.filter(pk=claims['user'], workspace_id=claims['workspace']).first()
    if actor is None:
        raise PermissionDenied()
    return lock_actor(actor)


@transaction.atomic
def can_connect(claims):
    try:
        authorized_actor(claims)
    except PermissionDenied:
        return False
    return True


@transaction.atomic
def delivery_data(claims, announcement_id):
    try:
        actor = authorized_actor(claims)
    except PermissionDenied:
        return None
    row = Announcement.objects.filter(pk=announcement_id, workspace_id=actor.workspace_id).select_related('author').first()
    if row is None:
        return None
    return {'type': 'chat.announcement', 'announcement': announcement_data(row)}


def publish_event(workspace_id, announcement_id):
    try:
        async_to_sync(get_channel_layer().group_send)(group_name(workspace_id),
            {'type': 'chat.announcement', 'workspace_id': str(workspace_id),
             'announcement_id': str(announcement_id)})
    except (RedisConnectionError, RedisTimeoutError):
        logger.warning('Announcement realtime delivery unavailable for %s', announcement_id)


def schedule_announcement(row):
    workspace_id, announcement_id = row.workspace_id, row.pk
    def deliver():
        publish_event(workspace_id, announcement_id)
    # Unexpected callback errors are logged by Django without failing committed publication.
    transaction.on_commit(deliver, robust=True)
