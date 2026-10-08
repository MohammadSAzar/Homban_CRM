import re
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, models, transaction, IntegrityError
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.tasks.tests.conftest import people
from common.jalali import schedule
from apps.chat.models import Conversation, Message, _CHAT_WRITE
from apps.chat.services import open_conversation, send_message, mark_conversation_read, notify_message

pytestmark = pytest.mark.django_db
URL = '/api/v1/chat/conversations/'


def client(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def conversation(people):
    return open_conversation(actor=people.owner, participant_id=people.other.pk)[0]


def test_open_reverse_empty_detail(people):
    response = client(people.owner).post(URL, {'participant_id': str(people.other.pk)})
    assert response.status_code == 201, response.data
    reverse = client(people.other).post(URL, {'participant_id': str(people.owner.pk)})
    assert reverse.status_code == 200 and reverse.data['id'] == response.data['id']
    row = Conversation.objects.get()
    assert row.participant_a_id < row.participant_b_id
    detail = client(people.owner).get(f'{URL}{row.pk}/')
    assert detail.status_code == 200 and detail.data['unread_count'] == 0
    assert detail.data['last_message_preview'] is None
    assert not Notification.objects.exists()


@pytest.mark.parametrize('role', User.Role.values)
def test_all_roles_private_access(people, role):
    actor = people.user(role, role=role)
    response = client(actor).post(URL, {'participant_id': str(people.other.pk)})
    assert response.status_code == 201
    assert client(actor).get(URL).data['count'] == 1


@pytest.mark.parametrize('target', ['self', 'foreign', 'inactive', 'missing'])
def test_invalid_target(people, target):
    who = people.owner if target == 'self' else people.foreign if target == 'foreign' else people.other
    if target == 'inactive':
        who.is_active = False
        who.save()
    pk = uuid.uuid4() if target == 'missing' else who.pk
    response = client(people.owner).post(URL, {'participant_id': str(pk)})
    assert response.status_code == (400 if target == 'self' else 404)
    assert not Conversation.objects.exists()


@pytest.mark.parametrize('state', ['inactive', 'workspace', 'internal', 'anonymous'])
def test_actor_denied(people, state):
    actor = people.owner
    if state == 'inactive':
        actor.is_active = False
        actor.save()
    elif state == 'workspace':
        people.workspace.is_active = False
        people.workspace.save()
    elif state == 'internal':
        actor = people.user('internal', ws=None)
    response = (APIClient() if state == 'anonymous' else client(actor)).get(URL)
    assert response.status_code in (401, 403)


@pytest.mark.parametrize('role', User.Role.values)
def test_outsiders_no_flag_bypass(people, role):
    row = conversation(people)
    outsider = people.user('outsider', role=role)
    outsider.is_staff = outsider.is_superuser = outsider.is_workspace_owner = True
    outsider.save()
    api = client(outsider)
    assert api.get(URL).data['count'] == 0
    for suffix in ('', 'messages/'):
        assert api.get(f'{URL}{row.pk}/{suffix}').status_code == 404
    assert api.post(f'{URL}{row.pk}/messages/', {'text': 'محرمانه'}).status_code == 404
    assert api.post(f'{URL}{row.pk}/read/', {}, format='json').status_code == 404


@pytest.mark.parametrize('state', ['foreign_actor', 'target_moved', 'target_inactive'])
def test_current_membership_fail_closed(people, state):
    row = conversation(people)
    if state == 'target_moved':
        models.QuerySet(model=User).filter(pk=people.other.pk).update(workspace=people.foreign_workspace)
    elif state == 'target_inactive':
        people.other.is_active = False
        people.other.save()
    api = client(people.foreign if state == 'foreign_actor' else people.owner)
    assert api.get(URL).data['count'] == 0
    assert api.get(f'{URL}{row.pk}/').status_code == 404
    assert api.post(f'{URL}{row.pk}/messages/', {'text': 'پیام'}).status_code == 404
    assert not Message.objects.exists()


@pytest.mark.parametrize('payload', [{}, {'text': ''}, {'text': ' \n '}, {'text': 'a'*4001},
                                    {'text': 'x', 'sender': 'x'}, {'text': 'x', 'created_at': 'x'}])
def test_invalid_message(people, payload):
    row = conversation(people)
    assert client(people.owner).post(f'{URL}{row.pk}/messages/', payload, format='json').status_code == 400
    assert not Message.objects.exists() and not Notification.objects.exists()


@pytest.mark.parametrize('field', ['workspace', 'participant_a', 'participant_b', 'participant_a_read_at', 'created_at'])
def test_protected_create_fields(people, field):
    response = client(people.owner).post(URL, {'participant_id': str(people.other.pk), field: 'bad'}, format='json')
    assert response.status_code == 400 and not Conversation.objects.exists()


def test_messages_read_ties_jalali_and_notification_independence(people):
    stamp = schedule('1405/07/15', '10:30')
    with patch('django.utils.timezone.now', return_value=stamp):
        row = conversation(people)
        a, b = client(people.owner), client(people.other)
        url = f'{URL}{row.pk}/'
        sent = a.post(url+'messages/', {'text': '  پیام خصوصی  '})
        assert sent.status_code == 201 and sent.data['text'] == 'پیام خصوصی'
        assert sent.data['created_at_display'] == 'چهارشنبه ۱۵ مهر ۱۴۰۵ ساعت ۱۰:۳۰'
        assert sent.data['is_own']
        assert a.get(url).data['unread_count'] == 0
        assert b.get(url).data['unread_count'] == 1
        assert b.get(url+'messages/').data['results'][0]['is_own'] is False
        row.refresh_from_db()
        assert row.participant_a_read_at is None and row.participant_b_read_at is None
        assert b.post(url+'read/', {}, format='json').data['unread_count'] == 0
        row.refresh_from_db()
        markers = (row.participant_a_read_at, row.participant_b_read_at)
        assert sum(v is not None for v in markers) == 1
        b.post(url+'read/', {}, format='json')
        row.refresh_from_db()
        assert markers == (row.participant_a_read_at, row.participant_b_read_at)
        assert Notification.objects.get().read_at is None
        a.post(url+'messages/', {'text': 'پیام بعدی'})
        assert b.get(url).data['unread_count'] == 1
        times = list(Message.objects.order_by('created_at').values_list('created_at', flat=True))
        assert times[1] > times[0]
        raw = b.get(url+'messages/').content.decode()
        assert not re.search(r'20\d{2}-\d{2}-\d{2}', raw)
        assert 'created_at"' not in raw
        # Reading notifications never advances the conversation watermark.
        b.post('/api/v1/notifications/read-all/', {}, format='json')
        assert b.get(url).data['unread_count'] == 1


def test_notification_dedup_privacy_and_rollback(people):
    row = conversation(people)
    message = send_message(actor=people.owner, pk=row.pk, text='سرّ خصوصی')
    notification = Notification.objects.get()
    assert notification.recipient_id == people.other.pk and notification.kind == 'chat_message'
    assert notification.source_id == message.pk
    assert notification.action_url == f'{URL}{row.pk}/'
    assert 'سرّ خصوصی' not in notification.message
    assert notify_message(message, people.other)[1] is False
    assert Notification.objects.count() == 1
    with patch('apps.chat.services.notify_message', side_effect=RuntimeError), pytest.raises(RuntimeError):
        send_message(actor=people.owner, pk=row.pk, text='rollback')
    assert Message.objects.count() == 1
    row.refresh_from_db()
    assert row.updated_at == message.created_at


def test_safe_identity_and_no_edit_delete(people):
    row = conversation(people)
    api = client(people.owner)
    data = api.get(f'{URL}{row.pk}/').data
    assert set(data['other_participant']) == {'id','username','first_name','last_name','role'}
    for path in (f'{URL}{row.pk}/', f'{URL}{row.pk}/messages/'):
        assert api.patch(path, {}, format='json').status_code == 405
        assert api.delete(path).status_code == 405
    assert api.post(f'{URL}{row.pk}/read/', {'read_at': 'bad'}, format='json').status_code == 400
    assert api.get(URL, {'workspace': str(people.workspace.pk)}).status_code == 400


def test_pagination_queries_order_and_history(people):
    row = conversation(people)
    send_message(actor=people.other, pk=row.pk, text='first')
    api = client(people.owner)
    with CaptureQueriesContext(connection) as queries:
        assert api.get(URL).data['count'] == 1
    small = len(queries)
    for n in range(3):
        target = people.user(f'target{n}')
        other, _ = open_conversation(actor=people.owner, participant_id=target.pk)
        send_message(actor=target, pk=other.pk, text='next')
    with CaptureQueriesContext(connection) as queries:
        response = api.get(URL)
    assert len(queries) == small <= 8
    expected = [str(pk) for pk in Conversation.objects.order_by('-updated_at','pk').values_list('pk', flat=True)]
    assert [r['id'] for r in response.data['results']] == expected
    for n in range(50):
        send_message(actor=people.owner, pk=row.pk, text=str(n))
    first = api.get(f'{URL}{row.pk}/messages/').data
    second = api.get(f'{URL}{row.pk}/messages/', {'page': 2}).data
    assert first['count'] == 51 and len(first['results']) == 50 and len(second['results']) == 1
    assert first['results'][0]['text'] == 'first' and second['results'][0]['text'] == '49'


def test_model_guards_constraints(people):
    row = conversation(people)
    message = send_message(actor=people.owner, pk=row.pk, text='test')
    with pytest.raises(ValidationError): row.save()
    with pytest.raises(ValidationError): row.save(_token=_CHAT_WRITE, update_fields=['updated_at'])
    with pytest.raises(ValidationError): message.save(_token=_CHAT_WRITE)
    with pytest.raises(ValidationError): Message.objects.update(text='changed')
    with pytest.raises(ValidationError): row.delete()
    with pytest.raises(ValidationError): Message(conversation=row, sender=people.foreign, text='bad').save(_token=_CHAT_WRITE)
    with transaction.atomic(), pytest.raises(IntegrityError):
        # Bypass application guards deliberately to exercise database backstops.
        models.QuerySet(model=Conversation).bulk_create([Conversation(workspace=people.workspace,
            participant_a_id=row.participant_a_id, participant_b_id=row.participant_b_id)])


def test_real_mysql_reverse_creation(people, transactional_db):
    barrier = Barrier(2)
    def run(reverse):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            actor, target = (people.other, people.owner) if reverse else (people.owner, people.other)
            row, created = open_conversation(actor=actor, participant_id=target.pk)
            return row.pk, created
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (False, True)))
    assert len({r[0] for r in results}) == 1
    assert sorted(r[1] for r in results) == [False, True]
    assert Conversation.objects.count() == 1


def test_model_integrity_and_invalid_internal_text(people):
    row = conversation(people)
    for change in ('pair', 'workspace', 'naive', 'immutable'):
        invalid = Conversation.objects.get(pk=row.pk)
        if change == 'pair': invalid.participant_b_id = invalid.participant_a_id
        elif change == 'workspace': invalid.workspace = people.foreign_workspace
        elif change == 'naive': invalid.participant_a_read_at = datetime(2026, 1, 1)
        else:
            third = people.user('third')
            invalid.participant_a, invalid.participant_b = sorted((people.owner, third), key=lambda user: user.pk)
        with pytest.raises(ValidationError): invalid.save(_token=_CHAT_WRITE)
    for text in (' ', None, 'x'*4001):
        with pytest.raises(Exception) as error:
            send_message(actor=people.owner, pk=row.pk, text=text)
        assert getattr(error.value, 'status_code', None) == 400
    with pytest.raises(ValidationError):
        Message(conversation=row, sender=people.owner, text=' ').save(_token=_CHAT_WRITE)
    with pytest.raises(ValidationError):
        Message(conversation=row, sender=people.owner, text='x', created_at=datetime(2026, 1, 1)).save(_token=_CHAT_WRITE)
    with transaction.atomic(), pytest.raises(IntegrityError):
        models.QuerySet(model=Conversation).bulk_create([Conversation(workspace=people.workspace,
            participant_a_id=row.participant_b_id, participant_b_id=row.participant_a_id)])


def test_both_read_markers_and_send_does_not_read_incoming(people):
    row = conversation(people)
    mark_conversation_read(actor=people.owner, pk=row.pk)  # Empty is a no-op.
    send_message(actor=people.other, pk=row.pk, text='incoming')
    send_message(actor=people.owner, pk=row.pk, text='reply')
    url = f'{URL}{row.pk}/'
    assert client(people.owner).get(url).data['unread_count'] == 1
    assert client(people.other).get(url).data['unread_count'] == 1
    mark_conversation_read(actor=people.owner, pk=row.pk)
    assert client(people.other).get(url).data['unread_count'] == 1
    mark_conversation_read(actor=people.other, pk=row.pk)
    assert client(people.owner).get(url).data['unread_count'] == 0
    assert client(people.other).get(url).data['unread_count'] == 0


def test_conversation_pagination_ties_and_preview_limit(people):
    stamp = schedule('1405/07/15', '10:30')
    with patch('django.utils.timezone.now', return_value=stamp):
        for n in range(51):
            target = people.user(f'p{n}')
            row, _ = open_conversation(actor=people.owner, participant_id=target.pk)
            send_message(actor=target, pk=row.pk, text='س'*140)
    api = client(people.owner)
    first, second = api.get(URL).data, api.get(URL, {'page': 2}).data
    assert first['count'] == 51 and len(first['results']) == 50 and len(second['results']) == 1
    rows = first['results'] + second['results']
    assert [r['id'] for r in rows] == sorted(r['id'] for r in rows)
    assert all(len(r['last_message_preview']) == 120 for r in rows)


def test_jwt_chat_access(people):
    from rest_framework_simplejwt.tokens import RefreshToken
    api = APIClient()
    token = RefreshToken.for_user(people.owner).access_token
    api.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    assert api.post(URL, {'participant_id': str(people.other.pk)}).status_code == 201
    people.owner.is_active = False
    people.owner.save()
    assert api.get(URL).status_code == 401
