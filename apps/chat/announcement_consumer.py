from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils.translation import gettext as _

from .announcement_realtime import can_connect, delivery_data, group_name, parse_ticket


class AnnouncementConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.announcement_group = None
        try:
            query = parse_qs(self.scope.get('query_string', b'').decode('ascii'),
                             strict_parsing=True, max_num_fields=1)
        except (UnicodeError, ValueError):
            query = {}
        ticket = query.get('ticket', [])
        self.claims = parse_ticket(ticket[0]) if (
            set(query) == {'ticket'} and len(ticket) == 1 and len(ticket[0]) <= 2048) else None
        if self.claims is None or not await database_sync_to_async(can_connect)(self.claims):
            await self.close(code=4403)
            return
        self.announcement_group = group_name(self.claims['workspace'])
        await self.channel_layer.group_add(self.announcement_group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if self.announcement_group is not None:
            await self.channel_layer.group_discard(self.announcement_group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None:
            await self.close(code=1008)
            return
        await self.send_json({'type': 'chat.error', 'code': 'unsupported_command',
                              'message': _('این کانال فقط برای دریافت اطلاعیه است.')})

    async def chat_announcement(self, event):
        if event['workspace_id'] != str(self.claims['workspace']):
            return
        data = await database_sync_to_async(delivery_data)(self.claims, event['announcement_id'])
        if data is None:
            await self.channel_layer.group_discard(self.announcement_group, self.channel_name)
            await self.close(code=4403)
            return
        await self.send_json(data)
