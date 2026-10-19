"""Purpose-bound General Group subscriptions and best-effort, post-commit delivery."""
import logging
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core import signing
from django.db import transaction
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from .general_group_serializers import general_group_message_data
from .models import GeneralGroupMessage
from .realtime import TICKET_AGE
from .general_group_services import require_general_group_access, send_general_group_message

logger = logging.getLogger(__name__)
TICKET_SALT = 'homban.chat.general-group.websocket.v1'


def group_name(workspace_id):
    return f'chat.general-group.{UUID(str(workspace_id)).hex}'


@transaction.atomic
def create_ticket(*, actor):
    actor = require_general_group_access(actor)
    return signing.dumps({'user': str(actor.pk), 'workspace': str(actor.workspace_id),
                          'purpose': 'general_group'}, salt=TICKET_SALT)


def parse_ticket(ticket):
    try:
        claims = signing.loads(ticket, salt=TICKET_SALT, max_age=TICKET_AGE)
        if (not isinstance(claims, dict) or set(claims) != {'user', 'workspace', 'purpose'}
                or claims['purpose'] != 'general_group'):
            return None
        return {'user': UUID(claims['user']), 'workspace': UUID(claims['workspace']),
                'purpose': 'general_group'}
    except (signing.BadSignature, ValueError, TypeError, AttributeError):
        return None


def authorized_actor(claims):
    actor = User.objects.filter(pk=claims['user'], workspace_id=claims['workspace']).first()
    if actor is None:
        raise PermissionDenied()
    return require_general_group_access(actor)


@transaction.atomic
def can_connect(claims):
    try:
        authorized_actor(claims)
    except PermissionDenied:
        return False
    return True


@transaction.atomic
def delivery_data(claims, message_id):
    try:
        actor = authorized_actor(claims)
    except PermissionDenied:
        return None
    row = GeneralGroupMessage.objects.filter(pk=message_id, workspace_id=actor.workspace_id).select_related('sender').first()
    if row is None:
        return None
    return {'type': 'chat.general_group.message', 'message': general_group_message_data(row)}


def publish_event(workspace_id, message_id):
    try:
        async_to_sync(get_channel_layer().group_send)(group_name(workspace_id),
            {'type': 'chat.general_group.message', 'workspace_id': str(workspace_id),
             'message_id': str(message_id)})
    except (RedisConnectionError, RedisTimeoutError):
        logger.warning('GeneralGroupMessage realtime delivery unavailable for %s', message_id)


def schedule_message(row):
    workspace_id, message_id = row.workspace_id, row.pk
    def deliver():
        publish_event(workspace_id, message_id)
    # Unexpected callback errors are logged by Django without failing committed publication.
    transaction.on_commit(deliver, robust=True)


@transaction.atomic
def send_command(claims, text):
    actor = authorized_actor(claims)
    return send_general_group_message(actor=actor, text=text).pk
