import json
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.db import models
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.accounts.models import User
from apps.chat.models import Message
from apps.chat.services import send_message
from apps.chat.tests.test_realtime import setup, socket, ticket
from apps.notifications.models import Notification
from apps.tasks.tests.conftest import people

pytestmark = pytest.mark.django_db(transaction=True)


async def command(ws, payload):
    await ws.send_input({'type': 'websocket.receive', 'text': payload})
    return json.loads((await ws.receive_output(timeout=3))['text'])


def test_send_shared_service_ack_and_single_delivery(setup):
    people, row, app = setup
    a, b = ticket(people.owner, row), ticket(people.other, row)
    async def run():
        async with socket(app, row, a) as (sender, _), socket(app, row, b) as (recipient, _):
            with patch('apps.chat.realtime.send_message', wraps=send_message) as shared:
                ack = await command(sender, json.dumps({'type': 'chat.send', 'text': ' سلام '}))
                shared.assert_called_once()
            assert set(ack) == {'type', 'message_id'} and ack['type'] == 'chat.send.ack'
            sent = json.loads((await sender.receive_output(timeout=3))['text'])
            received = json.loads((await recipient.receive_output(timeout=3))['text'])
            assert sent['message']['id'] == received['message']['id'] == ack['message_id']
            assert received['message']['text'] == 'سلام'
            assert sent['message']['is_own'] and not received['message']['is_own']
            assert set(received['message']) == {'id', 'text', 'is_own', 'created_at_display'}
            assert 'ساعت' in received['message']['created_at_display']
            assert await sender.receive_nothing() and await recipient.receive_nothing()
    async_to_sync(run)()
    assert Message.objects.count() == Notification.objects.count() == 1
    assert Message.objects.get().sender_id == people.owner.pk
    notification = Notification.objects.get()
    assert notification.recipient_id == people.other.pk and notification.read_at is None
    row.refresh_from_db()
    assert row.participant_a_read_at is row.participant_b_read_at is None


@pytest.mark.parametrize('payload', [
    '{', '[]', 'null',
    json.dumps({'type': 'chat.send'}),
    json.dumps({'type': 'chat.send', 'text': 123}),
    json.dumps({'type': 'chat.send', 'text': ''}),
    json.dumps({'type': 'chat.send', 'text': '   '}),
    json.dumps({'type': 'chat.send', 'text': 'x' * 4001}),
    json.dumps({'type': 'chat.send', 'text': 'hi', 'sender': 'forged'}),
    json.dumps({'type': 'chat.read', 'text': 'hi'}),
])
def test_invalid_payload_is_safe_and_connection_survives(setup, payload):
    people, row, app = setup
    value = ticket(people.owner, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            result = await command(ws, payload)
            assert set(result) == {'type', 'code', 'message'}
            assert result['type'] == 'chat.error'
            assert result['code'] in ('invalid_message', 'unsupported_command')
            assert not await sync_to_async(Message.objects.exists)()
            assert not await sync_to_async(Notification.objects.exists)()
            ack = await command(ws, '{"type":"chat.send","text":"valid"}')
            assert ack['type'] == 'chat.send.ack'
            assert json.loads((await ws.receive_output(timeout=3))['text'])['type'] == 'chat.message'
    async_to_sync(run)()


@pytest.mark.parametrize('state', ['user', 'workspace', 'participant'])
def test_send_rechecks_current_authorization(setup, state):
    people, row, app = setup
    value = ticket(people.owner, row)
    def revoke():
        if state == 'workspace':
            people.workspace.is_active = False
            people.workspace.save()
        else:
            changes = {'is_active': False} if state == 'user' else {'workspace': people.foreign_workspace}
            models.QuerySet(model=User).filter(pk=people.owner.pk).update(**changes)
    async def run():
        async with socket(app, row, value) as (ws, _):
            await sync_to_async(revoke)()
            await ws.send_input({'type': 'websocket.receive', 'text': '{"type":"chat.send","text":"denied"}'})
            assert (await ws.receive_output(timeout=3))['code'] == 4403
    async_to_sync(run)()
    assert not Message.objects.exists() and not Notification.objects.exists()


def test_service_failure_rolls_back_and_has_no_ack(setup, caplog):
    people, row, app = setup
    value = ticket(people.owner, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            with patch('apps.chat.services.notify_message', side_effect=RuntimeError('internal failure')):
                result = await command(ws, '{"type":"chat.send","text":"rollback"}')
            assert result['code'] == 'send_failed'
            assert 'internal failure' not in json.dumps(result)
            assert await ws.receive_nothing()
    async_to_sync(run)()
    assert not Message.objects.exists() and not Notification.objects.exists()
    assert 'Chat send command failed' in caplog.text


def test_realtime_failure_does_not_prevent_durable_ack(setup):
    people, row, app = setup
    value = ticket(people.owner, row)
    async def run():
        async with socket(app, row, value) as (ws, _):
            with patch('apps.chat.realtime.get_channel_layer', side_effect=RedisConnectionError('unavailable')):
                result = await command(ws, '{"type":"chat.send","text":"durable"}')
            assert result['type'] == 'chat.send.ack'
            assert await ws.receive_nothing()
    async_to_sync(run)()
    assert Message.objects.count() == Notification.objects.count() == 1
