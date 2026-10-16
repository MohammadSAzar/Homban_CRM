import importlib
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.core import signing
from django.db import transaction
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.chat.announcement_realtime import TICKET_SALT, create_ticket, group_name, publish_event
from apps.chat.announcement_services import publish_announcement
from apps.chat.models import Announcement, AnnouncementPublisher, Message
from apps.chat.tests.test_chat import client
from apps.chat.tests.test_announcements import manager, URL
from apps.chat.tests.fixtures import people
from apps.notifications.models import Notification
from common.jalali import schedule

pytestmark = pytest.mark.django_db(transaction=True)
TICKET_URL = URL + 'realtime-ticket/'


@pytest.fixture
def app(settings):
    settings.CHAT_WEBSOCKET_ORIGINS = ['https://homban.test']
    import config.asgi
    return importlib.reload(config.asgi).application


@asynccontextmanager
async def socket(app, value, origin=b'https://homban.test', query=None):
    ws = ApplicationCommunicator(app, {'type': 'websocket', 'path': '/ws/chat/announcements/',
        'query_string': query if query is not None else f'ticket={value}'.encode(),
        'headers': [(b'origin', origin)] if origin else [], 'subprotocols': []})
    await ws.send_input({'type': 'websocket.connect'})
    try:
        yield ws, await ws.receive_output(timeout=3)
    finally:
        await ws.send_input({'type': 'websocket.disconnect', 'code': 1000})
        await ws.wait(timeout=3)


@pytest.mark.parametrize('role', User.Role.values)
def test_all_roles_ticket_and_connect(people, app, role):
    actor = people.user(role, role=role)
    response = client(actor).post(TICKET_URL, {}, format='json')
    assert response.status_code == 200 and response['Cache-Control'] == 'no-store'
    assert signing.loads(response.data['ticket'], salt=TICKET_SALT) == {
        'user': str(actor.pk), 'workspace': str(people.workspace.pk), 'purpose': 'announcement'}
    async def run():
        async with socket(app, response.data['ticket']) as (_, accepted):
            assert accepted['type'] == 'websocket.accept'
    async_to_sync(run)()
    assert not get_channel_layer().groups
    assert not Announcement.objects.exists() and not Notification.objects.exists()


@pytest.mark.parametrize('state', ['inactive', 'workspace', 'internal'])
def test_ticket_current_state_no_flag_bypass(people, state):
    actor = people.owner
    actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
    actor.save()
    if state == 'inactive': User.objects.filter(pk=actor.pk).update(is_active=False)
    elif state == 'internal': User.objects.filter(pk=actor.pk).update(workspace=None)
    else: Workspace.objects.filter(pk=people.workspace.pk).update(is_active=False)
    assert client(actor).post(TICKET_URL, {}, format='json').status_code == 403


def test_ticket_strict_authentication(people):
    assert APIClient().post(TICKET_URL, {}, format='json').status_code == 401
    assert client(people.owner).get(TICKET_URL).status_code == 405
    assert client(people.owner).post(TICKET_URL, {'workspace': str(people.workspace.pk)}).status_code == 400
    assert client(people.owner).post(TICKET_URL + '?user=bad', {}).status_code == 400


def test_ticket_shape_purpose_expiry_and_private_ticket_separation(people, app):
    from apps.chat.realtime import TICKET_SALT as PRIVATE_SALT, parse_ticket as parse_private
    value = create_ticket(actor=people.owner)
    claims = signing.loads(value, salt=TICKET_SALT)
    assert parse_private(value, uuid.uuid4()) is None
    with patch('django.core.signing.time.time', return_value=1):
        expired = create_ticket(actor=people.owner)
    invalid = [value + 'x', expired, signing.dumps(claims, salt=PRIVATE_SALT)]
    for data in ([], dict(claims, purpose='conversation'), dict(claims, extra='bad'),
                 dict(claims, user='invalid'), dict(claims, workspace=str(people.foreign_workspace.pk)),
                 dict(claims, user=str(people.foreign.pk))):
        invalid.append(signing.dumps(data, salt=TICKET_SALT))
    async def run():
        for bad in invalid:
            async with socket(app, bad) as (_, response):
                assert response == {'type': 'websocket.close', 'code': 4403}
        for query in (b'\xff', b'broken', b'token=jwt', b'ticket=a&ticket=b', b'ticket=', b'ticket=' + b'x'*2049):
            async with socket(app, '', query=query) as (_, response):
                assert response['code'] == 4403
    async_to_sync(run)()


def test_current_role_rechecked_at_connect(people, app):
    value = create_ticket(actor=people.owner)
    User.objects.filter(pk=people.owner.pk).update(role='internal', is_staff=True, is_superuser=True)
    async def run():
        async with socket(app, value) as (_, response):
            assert response['code'] == 4403
    async_to_sync(run)()


def test_rest_delivers_once_publisher_and_members_not_foreign(people, manager, app):
    values = [create_ticket(actor=actor) for actor in (manager, people.owner, people.foreign)]
    async def run():
        async with socket(app, values[0]) as (publisher, _), socket(app, values[1]) as (member, _), socket(app, values[2]) as (foreign, _):
            def post():
                with patch.object(Announcement._meta.get_field('created_at'), 'get_default',
                                  return_value=schedule('1405/07/15', '10:30')):
                    return client(manager).post(URL, {'text': 'اطلاعیه رسمی'})
            response = await sync_to_async(post)()
            assert response.status_code == 201
            for ws in (publisher, member):
                event = json.loads((await ws.receive_output(timeout=3))['text'])
                assert event == {'type': 'chat.announcement', 'announcement': response.data}
                assert event['announcement']['created_at_display'] == 'چهارشنبه ۱۵ مهر ۱۴۰۵ ساعت ۱۰:۳۰'
                assert set(event['announcement']['author']) == {'id', 'username', 'first_name', 'last_name', 'role'}
                assert await ws.receive_nothing()
            assert await foreign.receive_nothing()
    async_to_sync(run)()
    assert Announcement.objects.count() == 1
    assert Notification.objects.count() == 2
    assert not Notification.objects.filter(recipient=manager).exists()
    assert not Notification.objects.filter(read_at__isnull=False).exists()
    assert not get_channel_layer().groups


@pytest.mark.parametrize('state', ['inactive', 'moved', 'internal', 'workspace', 'role'])
def test_delivery_rechecks_current_state(people, manager, app, state):
    value = create_ticket(actor=people.owner)
    row = publish_announcement(actor=manager, text='private')
    async def run():
        async with socket(app, value) as (ws, _):
            def revoke():
                if state == 'workspace':
                    Workspace.objects.filter(pk=people.workspace.pk).update(is_active=False)
                else:
                    changes = {'inactive': {'is_active': False}, 'moved': {'workspace': people.foreign_workspace},
                               'internal': {'workspace': None}, 'role': {'role': 'internal'}}[state]
                    User.objects.filter(pk=people.owner.pk).update(**changes)
            await sync_to_async(revoke)()
            await get_channel_layer().group_send(group_name(people.workspace.pk), {
                'type': 'chat.announcement', 'workspace_id': str(people.workspace.pk), 'announcement_id': str(row.pk)})
            assert (await ws.receive_output(timeout=3)) == {'type': 'websocket.close', 'code': 4403}
    async_to_sync(run)()


def test_outer_transaction_and_notification_failure(people, manager):
    with patch('apps.chat.announcement_realtime.publish_event') as publish:
        with pytest.raises(RuntimeError), transaction.atomic():
            publish_announcement(actor=manager, text='rollback')
            publish.assert_not_called()
            raise RuntimeError()
        publish.assert_not_called()
        with patch('apps.chat.announcement_services.publish_notification', side_effect=RuntimeError()):
            with pytest.raises(RuntimeError): publish_announcement(actor=manager, text='notification failure')
        publish.assert_not_called()
        assert not Announcement.objects.exists() and not Notification.objects.exists()
        with transaction.atomic():
            row = publish_announcement(actor=manager, text='commit')
            publish.assert_not_called()
        publish.assert_called_once_with(people.workspace.pk, row.pk)


def test_read_only_input(people, app):
    value = create_ticket(actor=people.owner)
    async def run():
        async with socket(app, value) as (ws, _):
            for text in ('{"type":"chat.send","text":"bad"}', '{bad json', '{"type":"delegate"}'):
                await ws.send_input({'type': 'websocket.receive', 'text': text})
                assert json.loads((await ws.receive_output(timeout=3))['text'])['code'] == 'unsupported_command'
            await ws.send_input({'type': 'websocket.receive', 'bytes': b'binary'})
            assert (await ws.receive_output(timeout=3))['code'] == 1008
    async_to_sync(run)()
    assert not Announcement.objects.exists() and not Message.objects.exists()
    assert not AnnouncementPublisher.objects.exists() and not Notification.objects.exists()


def test_origins_fail_closed(people, app, settings):
    value = create_ticket(actor=people.owner)
    async def denied(application, origin):
        async with socket(application, value, origin=origin) as (_, response):
            assert response['type'] == 'websocket.close'
    for origin in (None, b'https://evil.test'):
        async_to_sync(denied)(app, origin)
    settings.CHAT_WEBSOCKET_ORIGINS = []
    import config.asgi
    async_to_sync(denied)(importlib.reload(config.asgi).application, b'https://homban.test')


def test_internal_event_ids_only_and_failures_isolated(people, manager, caplog):
    event_send = AsyncMock()
    with patch('apps.chat.announcement_realtime.get_channel_layer') as layer:
        layer.return_value.group_send = event_send
        publish_event(people.workspace.pk, manager.pk)
    event_send.assert_awaited_once_with(group_name(people.workspace.pk), {
        'type': 'chat.announcement', 'workspace_id': str(people.workspace.pk), 'announcement_id': str(manager.pk)})
    for error in (RedisConnectionError, RedisTimeoutError, RuntimeError):
        with patch('apps.chat.announcement_realtime.get_channel_layer', side_effect=error('unavailable')):
            assert client(manager).post(URL, {'text': 'durable-private-body'}).status_code == 201
    assert Announcement.objects.count() == 3 and Notification.objects.count() == 6
    assert 'durable-private-body' not in caplog.text
    assert 'Announcement realtime delivery unavailable' in caplog.text


def test_event_workspace_check_and_moved_author_privacy(people, manager, app):
    row = publish_announcement(actor=manager, text='history')
    foreign_manager = people.user('foreign-manager', role='agency_manager', ws=people.foreign_workspace)
    foreign_row = publish_announcement(actor=foreign_manager, text='foreign')
    value = create_ticket(actor=people.owner)
    people.user('remaining-manager', role='range_manager')
    User.objects.filter(pk=manager.pk).update(workspace=people.foreign_workspace)
    async def run():
        async with socket(app, value) as (ws, _):
            async def event(workspace, pk):
                await get_channel_layer().group_send(group_name(people.workspace.pk), {
                    'type': 'chat.announcement', 'workspace_id': str(workspace), 'announcement_id': str(pk)})
            await event(people.foreign_workspace.pk, foreign_row.pk)
            assert await ws.receive_nothing()
            await event(people.workspace.pk, row.pk)
            data = json.loads((await ws.receive_output(timeout=3))['text'])
            assert data['announcement']['author'] is None
            await event(people.workspace.pk, foreign_row.pk)
            assert (await ws.receive_output(timeout=3))['code'] == 4403
    async_to_sync(run)()
