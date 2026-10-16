from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError as APIValidationError

from apps.accounts.models import User
from apps.chat.general_group_services import set_general_group_enabled, send_general_group_message
from apps.chat.models import GeneralGroupSettings, GeneralGroupMessage, _CHAT_WRITE
from apps.chat.tests.fixtures import people
from apps.chat.tests.test_chat import client
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db
URL = '/api/v1/chat/general-group/'
SETTINGS = URL + 'settings/'


def enable(people):
    return set_general_group_enabled(actor=people.manager, is_enabled=True)


def test_default_settings_read_does_not_create(people):
    for user in (people.owner, people.manager):
        assert client(user).get(SETTINGS).data == {'is_enabled': False}
    assert not GeneralGroupSettings.objects.exists()
    assert client(people.owner).get(URL).status_code == 403
    assert client(people.owner).post(URL, {'text': 'ممنوع'}).status_code == 403
    assert not GeneralGroupMessage.objects.exists()


def test_enable_disable_retains_history_and_is_idempotent(people):
    api = client(people.manager)
    assert api.patch(SETTINGS, {'is_enabled': True}, format='json').data == {'is_enabled': True}
    row = GeneralGroupSettings.objects.get()
    stamp = row.updated_at
    assert api.patch(SETTINGS, {'is_enabled': True}, format='json').status_code == 200
    row.refresh_from_db()
    assert row.updated_at == stamp and GeneralGroupSettings.objects.count() == 1
    message = send_general_group_message(actor=people.owner, text='تاریخچه')
    assert api.patch(SETTINGS, {'is_enabled': False}, format='json').status_code == 200
    assert client(people.owner).get(URL).status_code == 403
    assert client(people.owner).post(URL, {'text': 'ممنوع'}).status_code == 403
    assert GeneralGroupMessage.objects.get().pk == message.pk
    enable(people)
    assert client(people.owner).get(URL).data['results'][0]['id'] == str(message.pk)


@pytest.mark.parametrize('role', User.Role.values)
def test_all_roles_participate_safe_identity_no_side_effects(people, role):
    enable(people)
    actor = people.user('actor', role=role)
    api = client(actor)
    assert api.get(SETTINGS).data == {'is_enabled': True}
    with patch('channels.layers.get_channel_layer') as layer:
        response = api.post(URL, {'text': '  سلام 🙂  '}, format='json')
        assert response.status_code == 201 and response.data['text'] == 'سلام 🙂'
        assert response.data['sender']['id'] == str(actor.pk)
        assert set(response.data['sender']) == {'id', 'username', 'first_name', 'last_name', 'role'}
        assert set(response.data) == {'id', 'text', 'sender', 'created_at_display'}
        assert api.get(URL).data['results'] == [response.data]
        layer.assert_not_called()
    assert GeneralGroupMessage.objects.get().sender_id == actor.pk
    assert not Notification.objects.exists()


@pytest.mark.parametrize('role', ['range_manager', 'secretary', 'consultant', 'admin'])
def test_non_authorities_no_flag_bypass(people, role):
    actor = people.user('actor', role=role)
    actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
    actor.save()
    assert client(actor).patch(SETTINGS, {'is_enabled': True}, format='json').status_code == 403
    assert not GeneralGroupSettings.objects.exists()


def test_dynamic_authority_transition_keeps_participation(people):
    User.objects.filter(pk=people.manager.pk).update(is_active=False)
    manager = people.user('range', role='range_manager')
    api = client(manager)
    assert api.patch(SETTINGS, {'is_enabled': True}, format='json').status_code == 200
    second = people.user('second-range', role='range_manager')
    assert client(second).patch(SETTINGS, {'is_enabled': True}, format='json').status_code == 200
    User.objects.filter(pk=people.manager.pk).update(is_active=True)
    assert api.patch(SETTINGS, {'is_enabled': False}, format='json').status_code == 403
    assert api.post(URL, {'text': 'عضو'}).status_code == 201
    assert client(people.manager).patch(SETTINGS, {'is_enabled': False}, format='json').status_code == 200
    User.objects.filter(pk=people.manager.pk).update(workspace=people.foreign_workspace)
    assert api.patch(SETTINGS, {'is_enabled': True}, format='json').status_code == 200
    assert api.get(URL).data['count'] == 1


@pytest.mark.parametrize('state', ['personal', 'inactive', 'workspace', 'internal'])
def test_all_surfaces_fail_closed_current_state(people, state):
    enable(people)
    message = send_general_group_message(actor=people.owner, text='محفوظ')
    actor = people.owner
    if state == 'personal':
        User.objects.filter(pk=people.manager.pk).update(is_active=False)
    elif state == 'inactive':
        User.objects.filter(pk=actor.pk).update(is_active=False)
    elif state == 'workspace':
        people.workspace.is_active = False
        people.workspace.save()
    else:
        User.objects.filter(pk=actor.pk).update(workspace=None)
    User.objects.filter(pk=actor.pk).update(is_staff=True, is_superuser=True, is_workspace_owner=True)
    api = client(actor)
    assert api.get(URL).status_code == 403
    assert api.get(SETTINGS).status_code == 403
    assert api.post(URL, {'text': 'ممنوع'}).status_code == 403
    assert api.patch(SETTINGS, {'is_enabled': False}, format='json').status_code == 403
    assert GeneralGroupMessage.objects.filter(pk=message.pk).exists()
    assert GeneralGroupSettings.objects.get().is_enabled
    if state == 'personal':
        people.user('new-range', role='range_manager')
        assert api.get(URL).data['count'] == 1


@pytest.mark.parametrize('text', ['', '  ', 'x' * 4001, None])
def test_invalid_text(people, text):
    enable(people)
    assert client(people.owner).post(URL, {'text': text}, format='json').status_code == 400
    assert not GeneralGroupMessage.objects.exists()


def test_strict_message_input(people):
    enable(people)
    api = client(people.owner)
    assert api.post(URL, {}, format='json').status_code == 400
    for field in ('workspace', 'sender', 'role', 'created_at', 'group_id', 'attachment', 'reply'):
        assert api.post(URL, {'text': 'ok', field: 'forged'}, format='json').status_code == 400
    assert api.post(URL, {'text': 'x' * 4000}).status_code == 201


def test_strict_settings_input(people):
    api = client(people.manager)
    for body in ({}, {'is_enabled': True, 'workspace': str(people.foreign_workspace.pk)},
                 *({'is_enabled': value} for value in ('true', 1, None, [], {}))):
        assert api.patch(SETTINGS, body, format='json').status_code == 400
    assert not GeneralGroupSettings.objects.exists()
    assert api.patch(SETTINGS, {'is_enabled': False}, format='json').data == {'is_enabled': False}
    assert GeneralGroupSettings.objects.count() == 1


def test_foreign_workspace_cannot_read_or_modify_original(people):
    enable(people)
    row = send_general_group_message(actor=people.owner, text='محرمانه')
    foreign_manager = User.objects.get(workspace=people.foreign_workspace, role='agency_manager')
    api = client(foreign_manager)
    assert api.get(URL).status_code == 403
    assert api.get(SETTINGS).data == {'is_enabled': False}
    set_general_group_enabled(actor=foreign_manager, is_enabled=True)
    assert api.get(URL).data['count'] == 0
    assert api.get(URL, {'workspace': str(people.workspace.pk)}).status_code == 400
    assert api.get(f'{URL}{row.pk}/').status_code == 404
    assert api.patch(SETTINGS, {'is_enabled': False}, format='json').status_code == 200
    assert GeneralGroupSettings.objects.get(workspace=people.workspace).is_enabled


def test_moved_sender_identity_is_hidden_and_history_retained(people):
    enable(people)
    row = send_general_group_message(actor=people.owner, text='تاریخچه')
    User.objects.filter(pk=people.owner.pk).update(workspace=people.foreign_workspace)
    response = client(people.other).get(URL)
    assert response.data['results'][0]['sender'] is None
    assert response.data['results'][0]['id'] == str(row.pk)
    assert GeneralGroupMessage.objects.count() == 1


def test_pagination_order_jalali_and_bounded_queries(people):
    enable(people)
    stamp = timezone.now()
    rows = [GeneralGroupMessage(workspace=people.workspace, sender=people.owner,
                               text='پیام', created_at=stamp) for _ in range(52)]
    models.QuerySet(model=GeneralGroupMessage).bulk_create(rows)
    api = client(people.other)
    with CaptureQueriesContext(connection) as many:
        first = api.get(URL)
    with CaptureQueriesContext(connection) as few:
        second = api.get(URL, {'page': 2})
    assert len(many) == len(few) <= 9
    assert first.data['count'] == 52 and len(first.data['results']) == 50
    assert len(second.data['results']) == 2
    data = first.data['results'] + second.data['results']
    assert [item['id'] for item in data] == sorted(str(row.pk) for row in rows)
    assert str(stamp.year) not in data[0]['created_at_display']
    assert 'ساعت' in data[0]['created_at_display']
    new = send_general_group_message(actor=people.owner, text='آخر')
    assert api.get(URL, {'page': 2}).data['results'][-1]['id'] == str(new.pk)


def test_write_guards_consistency_constraints_and_protected_history(people):
    enable(people)
    row = send_general_group_message(actor=people.owner, text='پیام')
    settings = GeneralGroupSettings.objects.get()
    for obj in (row, settings):
        with pytest.raises(ValidationError): obj.save()
        with pytest.raises(ValidationError): obj.delete()
        with pytest.raises(ValidationError): type(obj).objects.filter(pk=obj.pk).update(workspace=people.foreign_workspace)
        with pytest.raises(ValidationError): type(obj).objects.bulk_create([obj])
    with pytest.raises(ValidationError): row.save(_token=_CHAT_WRITE)
    settings.workspace = people.foreign_workspace
    with pytest.raises(ValidationError): settings.save(_token=_CHAT_WRITE)
    with pytest.raises(ValidationError):
        GeneralGroupMessage(workspace=people.workspace, sender=people.foreign, text='bad').full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=GeneralGroupSettings).bulk_create([GeneralGroupSettings(workspace=people.workspace)])
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=GeneralGroupMessage).bulk_create([
            GeneralGroupMessage(workspace=people.workspace, sender=people.owner, text='')])
    with pytest.raises(ProtectedError): people.owner.delete()


def test_service_validation_and_rollback(people):
    enable(people)
    for text in (' ', 'x' * 4001, 123):
        with pytest.raises(APIValidationError): send_general_group_message(actor=people.owner, text=text)
    with pytest.raises(APIValidationError): set_general_group_enabled(actor=people.manager, is_enabled='true')
    with pytest.raises(RuntimeError), transaction.atomic():
        send_general_group_message(actor=people.owner, text='برگشت')
        set_general_group_enabled(actor=people.manager, is_enabled=False)
        raise RuntimeError()
    assert not GeneralGroupMessage.objects.exists()
    assert GeneralGroupSettings.objects.get().is_enabled


def test_no_extra_endpoints_or_anonymous_access(people):
    from rest_framework.test import APIClient
    assert APIClient().get(URL).status_code == 401
    api = client(people.manager)
    for method in ('put', 'delete'):
        assert getattr(api, method)(SETTINGS).status_code == 405
    for method in ('patch', 'put', 'delete'):
        assert getattr(api, method)(URL).status_code == 405
    assert api.post(URL + 'realtime-ticket/', {}, format='json').status_code == 404


def test_disabled_group_does_not_change_private_chat_or_announcements(people):
    from apps.chat.services import open_conversation, send_message
    from apps.chat.announcement_services import publish_announcement
    enable(people)
    set_general_group_enabled(actor=people.manager, is_enabled=False)
    conversation, _ = open_conversation(actor=people.owner, participant_id=people.other.pk)
    message = send_message(actor=people.owner, pk=conversation.pk, text='خصوصی')
    announcement = publish_announcement(actor=people.manager, text='رسمی')
    assert message.pk and announcement.pk
    assert client(people.other).get(f'/api/v1/chat/conversations/{conversation.pk}/').status_code == 200
    assert client(people.other).get('/api/v1/chat/announcements/').data['count'] == 1
    assert not GeneralGroupMessage.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_settings_creation_is_one_row(people):
    barrier = Barrier(2)
    def work():
        close_old_connections()
        try:
            actor = User.objects.get(pk=people.manager.pk)
            barrier.wait(timeout=10)
            return set_general_group_enabled(actor=actor, is_enabled=True)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work) for _ in range(2)]
        assert all(future.result(timeout=20) == {'is_enabled': True} for future in futures)
    assert GeneralGroupSettings.objects.count() == 1
