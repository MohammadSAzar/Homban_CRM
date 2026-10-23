import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.chat import announcement_realtime, realtime
from apps.chat.announcement_services import delegate_publisher, publish_announcement
from apps.chat.models import Announcement, AnnouncementPublisher, Conversation, Message
from apps.chat.policies import has_workspace_chat_authority
from apps.chat.tests.fixtures import single_team
from apps.chat.services import open_conversation, send_message
from apps.chat.tests.test_announcement_realtime import app, socket as announcement_socket
from apps.chat.tests.test_attachments import private_storage
from apps.chat.tests.test_chat import client, URL
from apps.chat.tests.test_realtime import socket as private_socket
from apps.notifications.models import Notification
from apps.tasks.tests.conftest import people

pytestmark = pytest.mark.django_db(transaction=True)
ANNOUNCEMENTS = '/api/v1/chat/announcements/'
PUBLISHERS = '/api/v1/chat/announcement-publishers/'


@pytest.fixture
def history(people, private_storage):
    manager = people.user('manager', role='range_manager')
    single_team(manager)
    row, _ = open_conversation(actor=people.owner, participant_id=people.other.pk)
    message = send_message(actor=people.owner, pk=row.pk,
        attachment=SimpleUploadedFile('note.txt', b'private', content_type='text/plain'))
    announcement = publish_announcement(actor=manager, text='تاریخچه')
    secretary = people.user('secretary', role='secretary')
    delegation, _ = delegate_publisher(actor=manager, user_id=secretary.pk)
    return manager, row, message, announcement, delegation


@pytest.mark.parametrize('action', [
    'list', 'open', 'detail', 'messages', 'send', 'read', 'private_ticket',
    'attachment', 'announcements', 'publish', 'announcement_ticket',
    'publishers', 'delegate', 'revoke',
])
def test_personal_denies_all_surfaces_preserves_history(people, history, action):
    manager, row, message, announcement, delegation = history
    User.objects.filter(pk=manager.pk).update(is_active=False)
    User.objects.filter(pk=people.owner.pk).update(is_staff=True, is_superuser=True, is_workspace_owner=True)
    people.owner.refresh_from_db()
    api = client(people.owner)
    endpoints = {
        'list': ('get', URL, {}),
        'open': ('post', URL, {'participant_id': str(people.other.pk)}),
        'detail': ('get', f'{URL}{row.pk}/', {}),
        'messages': ('get', f'{URL}{row.pk}/messages/', {}),
        'send': ('post', f'{URL}{row.pk}/messages/', {'text': 'ممنوع'}),
        'read': ('post', f'{URL}{row.pk}/read/', {}),
        'private_ticket': ('post', f'{URL}{row.pk}/realtime-ticket/', {}),
        'attachment': ('get', f'/api/v1/chat/messages/{message.pk}/attachment/', {}),
        'announcements': ('get', ANNOUNCEMENTS, {}),
        'publish': ('post', ANNOUNCEMENTS, {'text': 'ممنوع'}),
        'announcement_ticket': ('post', ANNOUNCEMENTS + 'realtime-ticket/', {}),
        'publishers': ('get', PUBLISHERS, {}),
        'delegate': ('post', PUBLISHERS, {'user_id': str(delegation.user_id)}),
        'revoke': ('delete', f'{PUBLISHERS}{delegation.pk}/', {}),
    }
    before = Notification.objects.count()
    method, url, data = endpoints[action]
    response = getattr(api, method)(url, data, format='json')
    assert response.status_code == 403
    assert Conversation.objects.count() == Message.objects.count() == Announcement.objects.count() == 1
    assert AnnouncementPublisher.objects.filter(pk=delegation.pk).exists()
    assert Notification.objects.count() == before
    row.refresh_from_db()
    assert row.participant_a_read_at is row.participant_b_read_at is None


@pytest.mark.parametrize('role', User.Role.values)
def test_range_only_authority_and_private_access(people, role):
    manager = people.user('manager', role='range_manager')
    single_team(manager)
    actor = people.user('actor', role=role)
    actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
    actor.save()
    target = people.user('delegate', role='secretary')
    api = client(actor)
    assert api.post(URL, {'participant_id': str(people.other.pk)}).status_code == 201
    authority = False  # Actor is not manager of the single operational Range.
    assert api.post(ANNOUNCEMENTS, {'text': 'اطلاعیه'}).status_code == (201 if authority else 403)
    assert api.post(PUBLISHERS, {'user_id': str(target.pk)}).status_code == (201 if authority else 403)
    assert api.get(PUBLISHERS).status_code == (200 if authority else 403)
    assert has_workspace_chat_authority(manager)
    assert not has_workspace_chat_authority(actor)


@pytest.mark.parametrize('transition', ['inactive', 'moved'])
def test_authority_transitions_and_retained_delegation(people, transition):
    range_manager = people.user('range', role='range_manager')
    first = single_team(range_manager)
    secretary = people.user('secretary', role='secretary')
    delegation, _ = delegate_publisher(actor=range_manager, user_id=secretary.pk)
    from apps.ranges.models import Range
    second = Range.objects.create(workspace=people.workspace, name='second', manager=range_manager)
    agency = people.user('agency', role='agency_manager')
    api = client(range_manager)
    assert api.post(ANNOUNCEMENTS, {'text': 'ممنوع'}).status_code == 403
    assert api.delete(f'{PUBLISHERS}{delegation.pk}/').status_code == 403
    assert client(agency).post(ANNOUNCEMENTS, {'text': 'مجاز'}).status_code == 201
    retained, _ = delegate_publisher(actor=agency, user_id=range_manager.pk)
    assert api.post(ANNOUNCEMENTS, {'text': 'تفویض'}).status_code == 201
    assert api.get(PUBLISHERS).status_code == 403
    User.objects.filter(pk=agency.pk).update(**(
        {'is_active': False} if transition == 'inactive' else {'workspace': people.foreign_workspace}))
    assert api.get(PUBLISHERS).status_code == 403
    Range.objects.filter(pk=second.pk).update(is_active=False)
    assert api.get(PUBLISHERS).status_code == 200
    assert api.delete(f'{PUBLISHERS}{delegation.pk}/').status_code == 204
    assert AnnouncementPublisher.objects.filter(pk=retained.pk).exists()
    User.objects.filter(pk=agency.pk).update(is_active=True, workspace=people.workspace)
    Range.objects.filter(pk=second.pk).update(is_active=True)
    assert api.get(PUBLISHERS).status_code == 403
    assert api.post(ANNOUNCEMENTS, {'text': 'تفویض محفوظ'}).status_code == 201


def test_personal_to_team_restores_history_and_ignores_customer_type(people, history):
    manager, row, *_ = history
    User.objects.filter(pk=manager.pk).update(role='consultant')
    assert client(people.owner).get(URL).status_code == 403
    people.workspace.customer_type = 'consultant'
    people.workspace.save()
    people.user('new-manager', role='range_manager')
    response = client(people.owner).get(URL)
    assert response.status_code == 200 and response.data['results'][0]['id'] == str(row.pk)
    assert client(people.owner).get(ANNOUNCEMENTS).data['count'] == 1


@pytest.mark.parametrize('stream', ['private', 'announcement'])
@pytest.mark.parametrize('change', ['inactive', 'moved', 'role'])
def test_realtime_topology_revocation_connect_delivery_and_send(people, history, app, stream, change):
    manager, row, message, announcement, _ = history
    module = realtime if stream == 'private' else announcement_realtime
    token = module.create_ticket(actor=people.owner, **({'pk': row.pk} if stream == 'private' else {}))
    claims = module.parse_ticket(token, row.pk) if stream == 'private' else module.parse_ticket(token)
    context = lambda: (private_socket(app, row, token) if stream == 'private' else announcement_socket(app, token))
    async def run():
        async with context() as (ws, accepted):
            assert accepted['type'] == 'websocket.accept'
            changes = {'is_active': False} if change == 'inactive' else (
                {'workspace': people.foreign_workspace} if change == 'moved' else {'role': 'consultant'})
            await sync_to_async(lambda: User.objects.filter(pk=manager.pk).update(**changes))()
            assert not await sync_to_async(module.can_connect)(claims)
            if stream == 'private':
                await sync_to_async(realtime.publish_message)(row.pk, message.pk)
            else:
                await sync_to_async(announcement_realtime.publish_event)(people.workspace.pk, announcement.pk)
            assert (await ws.receive_output(timeout=3)) == {'type': 'websocket.close', 'code': 4403}
        async with context() as (_, rejected):
            assert rejected['type'] == 'websocket.close' and rejected['code'] == 4403
    async_to_sync(run)()
    from rest_framework.exceptions import PermissionDenied
    with pytest.raises(PermissionDenied):
        realtime.send_command({'user': people.owner.pk, 'workspace': people.workspace.pk,
                               'conversation': row.pk}, 'ممنوع')
    assert Message.objects.count() == 1
