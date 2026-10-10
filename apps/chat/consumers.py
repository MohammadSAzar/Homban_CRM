import json
import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import ValidationError as ModelValidationError
from django.utils.translation import gettext as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .realtime import can_connect, delivery_data, group_name, parse_ticket, send_command

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.chat_group = None
        try:
            query = parse_qs(self.scope.get('query_string', b'').decode('ascii'),
                             strict_parsing=True, max_num_fields=1)
        except (UnicodeError, ValueError):
            query = {}
        ticket = query.get('ticket', [])
        self.claims = parse_ticket(ticket[0], self.scope['url_route']['kwargs']['pk']) if (
            set(query) == {'ticket'} and len(ticket) == 1 and len(ticket[0]) <= 2048) else None
        if self.claims is None or not await database_sync_to_async(can_connect)(self.claims):
            await self.close(code=4403)
            return
        self.chat_group = group_name(self.claims['conversation'])
        await self.channel_layer.group_add(self.chat_group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.chat_group is not None:
            await self.channel_layer.group_discard(self.chat_group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None:
            await self.close(code=1008)
            return
        try:
            data = json.loads(text_data)
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict) or set(data) != {'type', 'text'} or not isinstance(data['text'], str):
            await self.send_error('invalid_message', _('ساختار پیام نامعتبر است.'))
            return
        if data['type'] != 'chat.send':
            await self.send_error('unsupported_command', _('این دستور پشتیبانی نمی‌شود.'))
            return
        try:
            message_id = await database_sync_to_async(send_command)(self.claims, data['text'])
        except (PermissionDenied, NotFound):
            await self.close(code=4403)
            return
        except (ValidationError, ModelValidationError):
            await self.send_error('invalid_message', _('متن پیام نامعتبر است.'))
            return
        except Exception:
            logger.exception('Chat send command failed')
            await self.send_error('send_failed', _('ارسال پیام انجام نشد.'))
            return
        await self.send_json({'type': 'chat.send.ack', 'message_id': str(message_id)})

    async def send_error(self, code, message):
        await self.send_json({'type': 'chat.error', 'code': code, 'message': message})

    async def chat_message(self, event):
        if event['conversation_id'] != str(self.claims['conversation']):
            return
        data = await database_sync_to_async(delivery_data)(self.claims, event['message_id'])
        if data is None:
            await self.channel_layer.group_discard(self.chat_group, self.channel_name)
            await self.close(code=4403)
            return
        await self.send_json(data)
