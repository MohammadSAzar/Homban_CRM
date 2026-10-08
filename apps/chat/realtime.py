"""Short-lived bearer tickets and post-commit, best-effort message delivery."""
import logging
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core import signing
from django.db import transaction
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.accounts.models import User
from .models import Message
from .serializers import message_data
from .services import lock_actor, owned_conversation

logger = logging.getLogger(__name__)
TICKET_SALT = 'homban.chat.websocket.v1'
TICKET_AGE = 90


def group_name(conversation_id):
    return f'chat.{UUID(str(conversation_id)).hex}'


@transaction.atomic
def create_ticket(*, actor, pk):
    actor = lock_actor(actor)
    row = owned_conversation(actor, pk)
    return signing.dumps({'user': str(actor.pk), 'workspace': str(actor.workspace_id),
                          'conversation': str(row.pk)}, salt=TICKET_SALT)


def parse_ticket(ticket, conversation_id):
    try:
        claims = signing.loads(ticket, salt=TICKET_SALT, max_age=TICKET_AGE)
        if not isinstance(claims, dict) or set(claims) != {'user', 'workspace', 'conversation'}:
            return None
        claims = {key: UUID(value) for key, value in claims.items()}
        return claims if claims['conversation'] == conversation_id else None
    except (signing.BadSignature, ValueError, TypeError, AttributeError):
        return None


def authorized_actor(claims):
    """Called within a Workspace-first transaction, at connect and for every event."""
    actor = User.objects.filter(pk=claims['user'], workspace_id=claims['workspace']).first()
    if actor is None:
        raise PermissionDenied()
    actor = lock_actor(actor)
    owned_conversation(actor, claims['conversation'])
    return actor


@transaction.atomic
def can_connect(claims):
    try:
        authorized_actor(claims)
    except (PermissionDenied, NotFound):
        return False
    return True


@transaction.atomic
def delivery_data(claims, message_id):
    try:
        actor = authorized_actor(claims)
    except (PermissionDenied, NotFound):
        return None
    row = Message.objects.filter(pk=message_id, conversation_id=claims['conversation']).first()
    if row is None:
        return None
    return {'type': 'chat.message', 'conversation_id': str(claims['conversation']),
            'message': message_data(row, actor)}


def publish_message(conversation_id, message_id):
    try:
        async_to_sync(get_channel_layer().group_send)(group_name(conversation_id),
            {'type': 'chat.message', 'conversation_id': str(conversation_id), 'message_id': str(message_id)})
    except (RedisConnectionError, RedisTimeoutError):
        # No secrets, ticket or message text in logs. REST history is the recovery source.
        logger.warning('Chat realtime delivery unavailable for message %s', message_id)


def schedule_message(message):
    # Unexpected programming/configuration failures are logged by Django's robust callback
    # handling, without making an already committed REST send appear to have failed.
    conversation_id, message_id = message.conversation_id, message.pk
    def deliver():
        publish_message(conversation_id, message_id)
    transaction.on_commit(deliver, robust=True)
