from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .realtime import can_connect, delivery_data, group_name, parse_ticket


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
        # Read-only transport: neither text nor binary client frames are commands.
        await self.close(code=1008)

    async def chat_message(self, event):
        if event['conversation_id'] != str(self.claims['conversation']):
            return
        data = await database_sync_to_async(delivery_data)(self.claims, event['message_id'])
        if data is None:
            await self.channel_layer.group_discard(self.chat_group, self.channel_name)
            await self.close(code=4403)
            return
        await self.send_json(data)
