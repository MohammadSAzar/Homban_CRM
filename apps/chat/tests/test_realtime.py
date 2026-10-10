import importlib
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.core import signing
from django.db import models, transaction
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from apps.accounts.models import User
from apps.chat.models import Conversation, Message
from apps.chat.realtime import TICKET_SALT, create_ticket, group_name
from apps.chat.services import open_conversation, send_message
from apps.chat.tests.test_chat import client, URL
from apps.notifications.models import Notification
from apps.tasks.tests.conftest import people
from common.jalali import schedule

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def setup(people, settings):
    settings.CHAT_WEBSOCKET_ORIGINS = ['https://homban.test']
    import config.asgi
    app = importlib.reload(config.asgi).application
    row, _ = open_conversation(actor=people.owner, participant_id=people.other.pk)
    return people, row, app


def ticket(user, row):
    return create_ticket(actor=user, pk=row.pk)


@asynccontextmanager
async def socket(app, row, value, origin=b'https://homban.test', query=None):
    communicator = ApplicationCommunicator(app, {'type': 'websocket',
        'path': f'/ws/chat/conversations/{row.pk}/',
        'query_string': query if query is not None else f'ticket={value}'.encode(),
        'headers': [(b'origin', origin)] if origin else [], 'subprotocols': []})
    await communicator.send_input({'type': 'websocket.connect'})
    try:
        yield communicator, await communicator.receive_output(timeout=3)
    finally:
        await communicator.send_input({'type': 'websocket.disconnect', 'code': 1000})
        await communicator.wait(timeout=3)


def test_ticket_safe_bound_and_nonmutating(setup):
    people, row, _ = setup
    response = client(people.owner).post(f'{URL}{row.pk}/realtime-ticket/', {}, format='json')
    assert response.status_code == 200 and response['Cache-Control'] == 'no-store'
    claims = signing.loads(response.data['ticket'], salt=TICKET_SALT)
    assert claims == {'user': str(people.owner.pk), 'workspace': str(people.workspace.pk), 'conversation': str(row.pk)}
    row.refresh_from_db()
    assert row.participant_a_read_at is row.participant_b_read_at is None
    assert not Message.objects.exists() and not Notification.objects.exists()


@pytest.mark.parametrize('state', ['outsider', 'foreign', 'inactive', 'workspace', 'internal'])
def test_ticket_denied(setup, state):
    people, row, _ = setup
    actor = people.owner
    if state == 'outsider':
        actor = people.user('outsider', role='agency_manager')
        actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
        actor.save()
    elif state == 'foreign': actor = people.foreign
    elif state == 'internal': actor = people.user('internal', ws=None)
    elif state == 'inactive':
        actor.is_active = False
        actor.save()
    else:
        people.workspace.is_active = False
        people.workspace.save()
    response = client(actor).post(f'{URL}{row.pk}/realtime-ticket/', {}, format='json')
    assert response.status_code == (404 if state in ('outsider','foreign') else 403)


def test_ticket_strict_and_authenticated(setup):
    from rest_framework.test import APIClient
    people, row, _ = setup
    url = f'{URL}{row.pk}/realtime-ticket/'
    assert APIClient().post(url, {}, format='json').status_code == 401
    assert client(people.owner).post(url, {'user': str(people.other.pk)}, format='json').status_code == 400
    assert client(people.owner).get(url).status_code == 405


@pytest.mark.parametrize('kind', ['tampered', 'expired', 'conversation', 'user', 'workspace', 'outsider', 'malformed', 'extra'])
def test_socket_ticket_rejected(setup, kind):
    people, row, app = setup
    value = ticket(people.owner, row)
    claims = signing.loads(value, salt=TICKET_SALT)
    if kind == 'tampered': value += 'x'
    elif kind == 'expired':
        with patch('django.core.signing.time.time', return_value=1): value = ticket(people.owner, row)
    else:
        if kind in ('conversation','user','workspace'): claims[kind] = str(uuid.uuid4())
        elif kind == 'outsider':
            outsider = people.user('boss', role='agency_manager')
            outsider.is_staff = outsider.is_superuser = outsider.is_workspace_owner = True
            outsider.save()
            claims['user'] = str(outsider.pk)
        elif kind == 'malformed': claims['user'] = 'not-a-uuid'
        else: claims['secret'] = 'bad'
        value = signing.dumps(claims, salt=TICKET_SALT)
    async def run():
        async with socket(app, row, value) as (_, response):
            assert response == {'type': 'websocket.close', 'code': 4403}
    async_to_sync(run)()


@pytest.mark.parametrize('state', ['inactive_user', 'inactive_workspace', 'moved_participant'])
def test_current_state_rechecked_at_connect(setup, state):
    people, row, app = setup
    value = ticket(people.owner, row)
    if state == 'inactive_user':
        people.owner.is_active = False
        people.owner.save()
    elif state == 'inactive_workspace':
        people.workspace.is_active = False
        people.workspace.save()
    else:
        models.QuerySet(model=User).filter(pk=people.other.pk).update(workspace=people.foreign_workspace)
    async def run():
        async with socket(app, row, value) as (_, response):
            assert response['type'] == 'websocket.close'
    async_to_sync(run)()


@pytest.mark.parametrize('origin', [b'https://evil.test', None])
def test_origin_rejected(setup, origin):
    people, row, app = setup
    value = ticket(people.owner, row)
    async def run():
        async with socket(app, row, value, origin=origin) as (_, response):
            assert response['type'] == 'websocket.close'
    async_to_sync(run)()


def test_rest_commit_delivers_once_both_participants_jalali_and_privacy(setup):
    people, row, app = setup
    a, b = ticket(people.owner, row), ticket(people.other, row)
    async def run():
        async with socket(app, row, a) as (sender, accepted), socket(app, row, b) as (recipient, other):
            assert accepted['type'] == other['type'] == 'websocket.accept'
            def post():
                with patch('django.utils.timezone.now', return_value=schedule('1405/07/15', '10:30')):
                    return client(people.owner).post(f'{URL}{row.pk}/messages/', {'text': 'سلام محرمانه'})
            response = await sync_to_async(post)()
            assert response.status_code == 201
            sent = json.loads((await sender.receive_output(timeout=3))['text'])
            received = json.loads((await recipient.receive_output(timeout=3))['text'])
            assert set(received) == {'type','conversation_id','message'}
            assert received['type'] == 'chat.message' and received['conversation_id'] == str(row.pk)
            assert received['message'] == dict(response.data, is_own=False)
            assert sent['message'] == response.data
            assert received['message']['created_at_display'] == 'چهارشنبه ۱۵ مهر ۱۴۰۵ ساعت ۱۰:۳۰'
            assert await sender.receive_nothing() and await recipient.receive_nothing()
    async_to_sync(run)()
    assert Message.objects.count() == Notification.objects.count() == 1
    assert Notification.objects.get().recipient_id == people.other.pk
    assert 'سلام محرمانه' not in Notification.objects.get().message
    assert Notification.objects.get().read_at is None
    row.refresh_from_db()
    assert row.participant_a_read_at is row.participant_b_read_at is None
    assert not get_channel_layer().groups


def test_rollback_and_outer_commit_boundary(setup):
    people, row, app = setup
    value = ticket(people.other, row)
    async def run():
        async with socket(app, row, value) as (recipient, _):
            def rollback():
                with pytest.raises(RuntimeError), transaction.atomic():
                    send_message(actor=people.owner, pk=row.pk, text='rollback')
                    raise RuntimeError()
            await sync_to_async(rollback)()
            assert await recipient.receive_nothing()
            def commit():
                with patch('apps.chat.realtime.publish_message') as publish:
                    with transaction.atomic():
                        message = send_message(actor=people.owner, pk=row.pk, text='commit')
                        publish.assert_not_called()
                    publish.assert_called_once_with(row.pk, message.pk)
            await sync_to_async(commit)()
    async_to_sync(run)()
    assert Message.objects.count() == Notification.objects.count() == 1


@pytest.mark.parametrize('binary', [False, True])
def test_unsupported_frames_cannot_write(setup, binary):
    people, row, app = setup
    value = ticket(people.owner, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            await ws.send_input({'type': 'websocket.receive', 'bytes' if binary else 'text': b'hi' if binary else '{"text":"hi"}'})
            response = await ws.receive_output()
            if binary:
                assert response['code'] == 1008
            else:
                assert json.loads(response['text'])['type'] == 'chat.error'
    async_to_sync(run)()
    assert not Message.objects.exists() and not Notification.objects.exists()


@pytest.mark.parametrize('state', ['inactive', 'moved'])
def test_connected_socket_rechecks_before_delivery(setup, state):
    people, row, app = setup
    value = ticket(people.owner, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            def revoke():
                changes = {'is_active': False} if state == 'inactive' else {'workspace': people.foreign_workspace}
                models.QuerySet(model=User).filter(pk=people.owner.pk).update(**changes)
            await sync_to_async(revoke)()
            await get_channel_layer().group_send(group_name(row.pk), {'type': 'chat.message',
                'conversation_id': str(row.pk), 'message_id': str(uuid.uuid4())})
            assert (await ws.receive_output(timeout=3))['code'] == 4403
    async_to_sync(run)()


def test_unrelated_conversation_no_event(setup):
    people, row, app = setup
    third = people.user('third')
    different, _ = open_conversation(actor=people.owner, participant_id=third.pk)
    value = ticket(people.other, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            await sync_to_async(send_message)(actor=people.owner, pk=different.pk, text='elsewhere')
            assert await ws.receive_nothing()
            await get_channel_layer().group_send(group_name(row.pk), {'type': 'chat.message',
                'conversation_id': str(different.pk), 'message_id': str(uuid.uuid4())})
            assert await ws.receive_nothing()
    async_to_sync(run)()


@pytest.mark.parametrize('error', [RedisConnectionError, RedisTimeoutError, RuntimeError])
def test_delivery_failure_preserves_rest_success(setup, error, caplog):
    people, row, _ = setup
    with patch('apps.chat.realtime.get_channel_layer', side_effect=error('unavailable')):
        response = client(people.owner).post(f'{URL}{row.pk}/messages/', {'text': 'durable'})
    assert response.status_code == 201
    assert Message.objects.count() == Notification.objects.count() == 1
    assert 'unavailable' in caplog.text or 'Chat realtime delivery unavailable' in caplog.text


def test_asgi_http_preserved_and_no_ticket_query_fallback(setup):
    _, row, app = setup
    async def run():
        http = ApplicationCommunicator(app, {'type': 'http', 'method': 'GET',
            'path': URL, 'headers': [(b'host', b'testserver')], 'query_string': b''})
        await http.send_input({'type': 'http.request', 'body': b''})
        assert (await http.receive_output(timeout=3))['status'] == 401
        await http.wait()
        async with socket(app, row, '', query=b'token=raw-jwt') as (_, response):
            assert response['code'] == 4403
    async_to_sync(run)()


@pytest.mark.parametrize('query', [b'\xff', b'broken', b'ticket=a&ticket=b', b'ticket='])
def test_malformed_query_denied(setup, query):
    _, row, app = setup
    async def run():
        async with socket(app, row, '', query=query) as (_, response):
            assert response['code'] == 4403
    async_to_sync(run)()
