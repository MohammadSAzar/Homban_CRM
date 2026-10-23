import importlib
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.core import signing
from django.db import transaction
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.chat import general_group_realtime as rt, announcement_realtime, realtime
from apps.chat.general_group_services import set_general_group_enabled, send_general_group_message
from apps.chat.models import GeneralGroupMessage, GeneralGroupSettings
from apps.chat.services import open_conversation
from apps.chat.tests.fixtures import authority_people as people, people as base_people, single_team
from apps.chat.tests.test_chat import client
from apps.chat.tests.test_realtime import socket as private_socket
from apps.chat.tests.test_announcement_realtime import socket as announcement_socket
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db(transaction=True)
URL = '/api/v1/chat/general-group/'


@pytest.fixture
def setup(people, settings):
    settings.CHAT_WEBSOCKET_ORIGINS = ['https://homban.test']
    import config.asgi
    app = importlib.reload(config.asgi).application
    set_general_group_enabled(actor=people.manager, is_enabled=True)
    return people, app


@asynccontextmanager
async def socket(app, token, origin=b'https://homban.test', query=None):
    ws = ApplicationCommunicator(app, {'type': 'websocket', 'path': '/ws/chat/general-group/',
        'query_string': query if query is not None else f'ticket={token}'.encode(),
        'headers': [(b'origin', origin)] if origin else [], 'subprotocols': []})
    await ws.send_input({'type': 'websocket.connect'})
    try:
        yield ws, await ws.receive_output(timeout=3)
    finally:
        await ws.send_input({'type': 'websocket.disconnect', 'code': 1000})
        await ws.wait(timeout=3)


@pytest.mark.parametrize('role', User.Role.values)
def test_ticket_all_roles_strict_safe_and_connect(setup, role):
    people, app = setup
    actor = people.user('actor', role=role)
    api = client(actor)
    response = api.post(URL + 'realtime-ticket/', {}, format='json')
    assert response.status_code == 200 and response['Cache-Control'] == 'no-store'
    token = response.data['ticket']
    assert signing.loads(token, salt=rt.TICKET_SALT) == {
        'user': str(actor.pk), 'workspace': str(people.workspace.pk), 'purpose': 'general_group'}
    assert api.post(URL + 'realtime-ticket/', {'text': 'forged'}, format='json').status_code == 400
    assert api.post(URL + 'realtime-ticket/?workspace=forged', {}, format='json').status_code == 400
    async def run():
        async with socket(app, token) as (_, accepted):
            assert accepted['type'] == 'websocket.accept'
    async_to_sync(run)()
    assert not get_channel_layer().groups
    assert not GeneralGroupMessage.objects.exists() and not Notification.objects.exists()


@pytest.mark.parametrize('state', ['disabled', 'personal', 'inactive', 'moved', 'workspace', 'role'])
def test_current_state_revocation_for_ticket_connect_send_delivery(setup, state):
    people, app = setup
    token = rt.create_ticket(actor=people.owner)
    message = send_general_group_message(actor=people.other, text='محفوظ')
    def revoke():
        if state == 'disabled': set_general_group_enabled(actor=people.manager, is_enabled=False)
        elif state == 'personal': User.objects.filter(pk=people.manager.pk).update(is_active=False)
        elif state == 'inactive': User.objects.filter(pk=people.owner.pk).update(is_active=False)
        elif state == 'moved': User.objects.filter(pk=people.owner.pk).update(workspace=people.foreign_workspace)
        elif state == 'workspace': Workspace.objects.filter(pk=people.workspace.pk).update(is_active=False)
        else: User.objects.filter(pk=people.owner.pk).update(role='invalid')
    async def run():
        async with socket(app, token) as (sender, a), socket(app, token) as (receiver, b):
            assert a['type'] == b['type'] == 'websocket.accept'
            await sync_to_async(revoke)()
            await sender.send_input({'type': 'websocket.receive', 'text': json.dumps({
                'type': 'chat.general_group.send', 'text': 'ممنوع'})})
            assert (await sender.receive_output(timeout=3))['code'] == 4403
            await sync_to_async(rt.publish_event)(people.workspace.pk, message.pk)
            assert (await receiver.receive_output(timeout=3))['code'] == 4403
        async with socket(app, token) as (_, denied):
            assert denied['type'] == 'websocket.close' and denied['code'] == 4403
    async_to_sync(run)()
    assert client(people.owner).post(URL + 'realtime-ticket/', {}, format='json').status_code == 403
    assert GeneralGroupMessage.objects.count() == 1 and not Notification.objects.exists()
    if state in ('disabled', 'personal'):
        if state == 'disabled': set_general_group_enabled(actor=people.manager, is_enabled=True)
        else: people.user('restored-manager', role='range_manager')
        fresh = rt.create_ticket(actor=people.owner)
        async def reconnect():
            async with socket(app, fresh) as (_, accepted):
                assert accepted['type'] == 'websocket.accept'
        async_to_sync(reconnect)()


@pytest.mark.parametrize('kind', ['tampered', 'expired', 'purpose', 'workspace', 'shape'])
def test_invalid_tickets(setup, kind):
    people, app = setup
    token = rt.create_ticket(actor=people.owner)
    claims = signing.loads(token, salt=rt.TICKET_SALT)
    if kind == 'tampered': token += 'x'
    elif kind == 'expired':
        with patch('django.core.signing.time.time', return_value=1):
            token = signing.dumps(claims, salt=rt.TICKET_SALT)
    else:
        if kind == 'purpose': claims['purpose'] = 'announcement'
        elif kind == 'workspace': claims['workspace'] = str(people.foreign_workspace.pk)
        else: claims['role'] = 'agency_manager'
        token = signing.dumps(claims, salt=rt.TICKET_SALT)
    async def run():
        async with socket(app, token) as (_, denied):
            assert denied['code'] == 4403
    async_to_sync(run)()


def test_ticket_isolation_both_directions(setup):
    people, app = setup
    row, _ = open_conversation(actor=people.owner, participant_id=people.other.pk)
    private = realtime.create_ticket(actor=people.owner, pk=row.pk)
    announcement = announcement_realtime.create_ticket(actor=people.owner)
    group = rt.create_ticket(actor=people.owner)
    async def run():
        for token in (private, announcement):
            async with socket(app, token) as (_, denied): assert denied['code'] == 4403
        async with private_socket(app, row, group) as (_, denied): assert denied['code'] == 4403
        async with announcement_socket(app, group) as (_, denied): assert denied['code'] == 4403
    async_to_sync(run)()


def test_origin_and_query_fail_closed(setup, settings):
    people, app = setup
    token = rt.create_ticket(actor=people.owner)
    async def run():
        for origin in (b'https://foreign.test', None):
            async with socket(app, token, origin=origin) as (_, denied):
                assert denied['type'] == 'websocket.close'
        for query in (b'', b'ticket=x&ticket=y', b'\xff', b'ticket=' + b'x' * 2049):
            async with socket(app, token, query=query) as (_, denied): assert denied['code'] == 4403
    async_to_sync(run)()
    settings.CHAT_WEBSOCKET_ORIGINS = []
    import config.asgi
    empty_app = importlib.reload(config.asgi).application
    async def empty():
        async with socket(empty_app, token) as (_, denied): assert denied['type'] == 'websocket.close'
    async_to_sync(empty)()


def test_ws_send_ack_one_shared_write_rest_payload_and_workspace_isolation(setup):
    people, app = setup
    foreign_manager = User.objects.get(workspace=people.foreign_workspace, role='range_manager')
    set_general_group_enabled(actor=foreign_manager, is_enabled=True)
    a, b, c = [rt.create_ticket(actor=user) for user in (people.owner, people.other, people.foreign)]
    async def run():
        async with socket(app, a) as (sender, _), socket(app, b) as (recipient, _), socket(app, c) as (foreign, _):
            with patch('apps.chat.general_group_realtime.send_general_group_message', wraps=send_general_group_message) as shared:
                await sender.send_input({'type': 'websocket.receive', 'text': json.dumps({
                    'type': 'chat.general_group.send', 'text': '  سلام  '})})
                ack = json.loads((await sender.receive_output(timeout=3))['text'])
                assert set(ack) == {'type', 'message_id'} and ack['type'] == 'chat.general_group.send.ack'
                shared.assert_called_once()
            sent = json.loads((await sender.receive_output(timeout=3))['text'])
            received = json.loads((await recipient.receive_output(timeout=3))['text'])
            assert sent == received and sent['message']['id'] == ack['message_id']
            history = await sync_to_async(lambda: client(people.owner).get(URL).data['results'])()
            assert sent == {'type': 'chat.general_group.message', 'message': history[0]}
            assert sent['message']['text'] == 'سلام' and 'ساعت' in sent['message']['created_at_display']
            assert set(sent['message']['sender']) == {'id', 'username', 'first_name', 'last_name', 'role'}
            assert await sender.receive_nothing() and await recipient.receive_nothing() and await foreign.receive_nothing()
    async_to_sync(run)()
    assert GeneralGroupMessage.objects.count() == 1 and not Notification.objects.exists()


def test_invalid_commands_safe_errors_no_writes_and_binary_closes(setup):
    people, app = setup
    token = rt.create_ticket(actor=people.owner)
    async def run():
        async with socket(app, token) as (ws, _):
            payloads = ['{bad', '[]', '{}', json.dumps({'type': 'settings', 'text': 'x'})]
            payloads += [json.dumps({'type': 'chat.general_group.send', 'text': text}) for text in ('', ' ', 'x'*4001, 123)]
            payloads += [json.dumps({'type': 'chat.general_group.send', 'text': 'x', field: 'forged'})
                         for field in ('sender', 'workspace', 'attachment', 'is_enabled', 'read_at')]
            for payload in payloads:
                await ws.send_input({'type': 'websocket.receive', 'text': payload})
                error = json.loads((await ws.receive_output(timeout=3))['text'])
                assert error['type'] == 'chat.error' and error['code'] in ('invalid_message', 'unsupported_command')
                assert any('\u0600' <= char <= '\u06ff' for char in error['message'])
            await ws.send_input({'type': 'websocket.receive', 'bytes': b'audio'})
            assert (await ws.receive_output(timeout=3))['code'] == 1008
    async_to_sync(run)()
    assert not GeneralGroupMessage.objects.exists() and not Notification.objects.exists()
    assert GeneralGroupSettings.objects.get().is_enabled


def test_rest_after_outer_commit_rollback_and_ids_only(setup):
    people, _ = setup
    with patch('apps.chat.general_group_realtime.publish_event') as publish:
        with transaction.atomic():
            response = client(people.owner).post(URL, {'text': 'پیام'})
            assert response.status_code == 201
            publish.assert_not_called()
        publish.assert_called_once_with(people.workspace.pk, GeneralGroupMessage.objects.get().pk)
        publish.reset_mock()
        with pytest.raises(RuntimeError), transaction.atomic():
            send_general_group_message(actor=people.owner, text='برگشت')
            raise RuntimeError()
        publish.assert_not_called()
    row = GeneralGroupMessage.objects.get()
    with patch.object(get_channel_layer(), 'group_send', new_callable=AsyncMock) as send:
        rt.publish_event(people.workspace.pk, row.pk)
        send.assert_awaited_once_with(rt.group_name(people.workspace.pk), {
            'type': 'chat.general_group.message', 'workspace_id': str(people.workspace.pk), 'message_id': str(row.pk)})


def test_delivery_reload_scoped_and_moved_sender_private(setup):
    people, app = setup
    row = send_general_group_message(actor=people.other, text='history')
    foreign_manager = User.objects.get(workspace=people.foreign_workspace, role='range_manager')
    set_general_group_enabled(actor=foreign_manager, is_enabled=True)
    foreign = send_general_group_message(actor=foreign_manager, text='foreign')
    User.objects.filter(pk=people.other.pk).update(workspace=people.foreign_workspace)
    token = rt.create_ticket(actor=people.owner)
    async def run():
        async with socket(app, token) as (ws, _):
            await get_channel_layer().group_send(rt.group_name(people.workspace.pk), {
                'type': 'chat.general_group.message', 'workspace_id': str(people.foreign_workspace.pk), 'message_id': str(foreign.pk)})
            assert await ws.receive_nothing()
            await sync_to_async(rt.publish_event)(people.workspace.pk, row.pk)
            data = json.loads((await ws.receive_output(timeout=3))['text'])
            assert data['message']['sender'] is None
            await sync_to_async(rt.publish_event)(people.workspace.pk, foreign.pk)
            assert (await ws.receive_output(timeout=3))['code'] == 4403
    async_to_sync(run)()


def test_redis_failure_preserves_rest_and_ws_ack(setup):
    people, app = setup
    token = rt.create_ticket(actor=people.owner)
    with patch.object(get_channel_layer(), 'group_send', side_effect=RedisConnectionError('offline')):
        assert client(people.owner).post(URL, {'text': 'REST'}).status_code == 201
        async def run():
            async with socket(app, token) as (ws, _):
                await ws.send_input({'type': 'websocket.receive', 'text': json.dumps({
                    'type': 'chat.general_group.send', 'text': 'socket'})})
                ack = json.loads((await ws.receive_output(timeout=3))['text'])
                assert ack['type'] == 'chat.general_group.send.ack'
        async_to_sync(run)()
    with patch('apps.chat.general_group_realtime.publish_event', side_effect=RuntimeError('callback')):
        assert client(people.owner).post(URL, {'text': 'durable'}).status_code == 201
    assert GeneralGroupMessage.objects.count() == 3 and not Notification.objects.exists()


def test_service_failure_safe_no_ack_or_message(setup):
    people, app = setup
    token = rt.create_ticket(actor=people.owner)
    async def run():
        async with socket(app, token) as (ws, _):
            with patch('apps.chat.general_group_realtime.send_general_group_message', side_effect=RuntimeError('private detail')):
                await ws.send_input({'type': 'websocket.receive', 'text': json.dumps({
                    'type': 'chat.general_group.send', 'text': 'x'})})
                error = json.loads((await ws.receive_output(timeout=3))['text'])
                assert error['code'] == 'send_failed' and 'private detail' not in str(error)
                assert await ws.receive_nothing()
    async_to_sync(run)()
    assert not GeneralGroupMessage.objects.exists()
