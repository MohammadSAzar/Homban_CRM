from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, IntegrityError, models, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.announcement_services import (delegate_publisher, publish_announcement,
                                            _notify_announcement, remove_publisher)
from apps.chat.models import Announcement, AnnouncementPublisher
from apps.chat.tests.test_chat import client
from apps.notifications.models import Notification
from apps.notifications.services import publish_notification
from apps.chat.tests.fixtures import people

pytestmark = pytest.mark.django_db
URL = '/api/v1/chat/announcements/'
PUBLISHERS = '/api/v1/chat/announcement-publishers/'


@pytest.fixture
def manager(people):
    return people.manager


@pytest.mark.parametrize('role', User.Role.values)
def test_all_roles_read_same_workspace(people, manager, role):
    row = publish_announcement(actor=manager, text='اطلاعیه')
    actor = people.user(role, role=role)
    response = client(actor).get(URL)
    assert response.status_code == 200
    assert [item['id'] for item in response.data['results']] == [str(row.pk)]
    assert Notification.objects.filter(read_at__isnull=False).count() == 0


@pytest.mark.parametrize('state', ['inactive', 'workspace', 'internal', 'anonymous'])
def test_ineligible_reader_denied(people, state):
    actor = people.owner
    if state == 'inactive':
        User.objects.filter(pk=actor.pk).update(is_active=False)
    elif state == 'workspace':
        people.workspace.is_active = False
        people.workspace.save()
    elif state == 'internal':
        actor = people.user('internal', ws=None)
    else:
        actor = None
    assert client(actor).get(URL).status_code in (401, 403)


@pytest.mark.parametrize('role', ['range_manager', 'secretary', 'consultant', 'admin'])
def test_no_implicit_publisher_or_privilege_bypass(people, role):
    actor = people.user(role, role=role)
    actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
    actor.save()
    assert client(actor).post(URL, {'text': 'ممنوع'}).status_code == 403
    assert not Announcement.objects.exists() and not Notification.objects.exists()


@pytest.mark.parametrize('role', ['range_manager', 'secretary'])
def test_delegate_idempotence_publish_and_revoke(people, manager, role):
    target = people.user(role, role=role)
    response = client(manager).post(PUBLISHERS, {'user_id': str(target.pk)})
    assert response.status_code == 201
    repeat = client(manager).post(PUBLISHERS, {'user_id': str(target.pk)})
    assert repeat.status_code == 200 and repeat.data == response.data
    assert AnnouncementPublisher.objects.count() == 1
    assert client(manager).get(PUBLISHERS).data['count'] == 1
    assert client(target).post(URL, {'text': 'اطلاعیه'}).status_code == 201
    assert client(manager).delete(f"{PUBLISHERS}{response.data['id']}/").status_code == 204
    assert client(target).post(URL, {'text': 'ممنوع'}).status_code == 403
    assert Announcement.objects.count() == 1


@pytest.mark.parametrize('target_kind', ['consultant', 'admin', 'agency_manager', 'foreign', 'inactive'])
def test_invalid_delegation(people, manager, target_kind):
    target = people.user('target', role=target_kind if target_kind in User.Role.values else 'secretary',
                         ws=people.foreign_workspace if target_kind == 'foreign' else people.workspace)
    if target_kind == 'inactive':
        target.is_active = False
        target.save()
    assert client(manager).post(PUBLISHERS, {'user_id': str(target.pk)}).status_code == 404
    assert not AnnouncementPublisher.objects.exists()


def test_only_manager_may_manage_delegation(people, manager):
    target = people.user('target', role='secretary')
    row, _ = delegate_publisher(actor=manager, user_id=target.pk)
    for role in ('range_manager', 'secretary', 'consultant', 'admin'):
        actor = people.user(role, role=role)
        actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
        actor.save()
        if role in ('range_manager', 'secretary'):
            delegate_publisher(actor=manager, user_id=actor.pk)
        api = client(actor)
        assert api.get(PUBLISHERS).status_code == 403
        assert api.post(PUBLISHERS, {'user_id': str(target.pk)}).status_code == 403
        assert api.delete(f'{PUBLISHERS}{row.pk}/').status_code == 403


@pytest.mark.parametrize('state', ['inactive', 'role', 'moved'])
def test_stale_delegation_and_actor_rechecked(people, manager, state):
    actor = people.user('delegate', role='secretary')
    delegation, _ = delegate_publisher(actor=manager, user_id=actor.pk)
    changes = {'is_active': False} if state == 'inactive' else (
        {'role': 'consultant'} if state == 'role' else {'workspace': people.foreign_workspace})
    User.objects.filter(pk=actor.pk).update(**changes)
    assert client(actor).post(URL, {'text': 'ممنوع'}).status_code == 403
    actor.refresh_from_db()
    assert client(actor).post(URL, {'text': 'ممنوع'}).status_code == 403
    assert AnnouncementPublisher.objects.filter(pk=delegation.pk).exists()
    assert not Announcement.objects.exists()


def test_content_and_strict_inputs(people, manager):
    api = client(manager)
    for data in ({}, {'text': ''}, {'text': '  '}, {'text': 'x' * 4001}, {'text': None},
                 *({'text': 'ok', key: 'forged'} for key in ('author', 'workspace', 'created_at', 'attachment'))):
        assert api.post(URL, data, format='json').status_code == 400
    assert not Announcement.objects.exists()
    result = api.post(URL, {'text': '  اطلاعیه  '})
    assert result.status_code == 201 and result.data['text'] == 'اطلاعیه'
    assert result.data['author']['id'] == str(manager.pk)
    assert api.post(URL, {'text': 'x' * 4000}).status_code == 201
    for key in ('workspace', 'role', 'permission', 'scope'):
        assert api.post(PUBLISHERS, {'user_id': str(people.owner.pk), key: 'bad'}).status_code == 400
    assert api.get(URL, {'workspace': str(people.foreign_workspace.pk)}).status_code == 400


def test_notification_batching_deduplication_and_privacy(people, manager):
    extra = [people.user(role, role=role) for role in User.Role.values]
    inactive = people.user('inactive')
    inactive.is_active = False
    inactive.save()
    people.user('internal', ws=None)
    with patch('apps.chat.announcement_services.RECIPIENT_BATCH_SIZE', 2):
        row = publish_announcement(actor=manager, text='متن کامل محرمانه اطلاعیه')
        _notify_announcement(row)  # Trusted retry under this test's transaction.
    assert set(Notification.objects.values_list('recipient_id', flat=True)) == {
        people.owner.pk, people.other.pk, *(user.pk for user in extra)}
    for notification in Notification.objects.all():
        assert notification.kind == 'announcement'
        assert notification.event_key == f'announcement:{row.pk}:created'
        assert notification.source_id == row.pk and notification.source_type == 'announcement'
        assert notification.action_url == URL
        assert row.text not in notification.message and notification.read_at is None
    assert Notification.objects.count() == len(extra) + 2


def test_publication_failure_rolls_back_every_recipient(people, manager):
    calls = []
    def fail_second(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError('publication failed')
        return publish_notification(**kwargs)
    with patch('apps.chat.announcement_services.publish_notification', side_effect=fail_second):
        with pytest.raises(RuntimeError):
            publish_announcement(actor=manager, text='اطلاعیه')
    assert len(calls) == 2
    assert not Announcement.objects.exists() and not Notification.objects.exists()


def test_pagination_identity_jalali_and_bounded_queries(people, manager):
    stamp = timezone.now()
    rows = [Announcement(workspace=people.workspace, author=manager, text='اطلاعیه', created_at=stamp)
            for _ in range(52)]
    models.QuerySet(model=Announcement).bulk_create(rows)
    api = client(people.owner)
    with CaptureQueriesContext(connection) as many:
        first = api.get(URL)
    with CaptureQueriesContext(connection) as few:
        second = api.get(URL, {'page': 2})
    assert len(many) == len(few) and len(many) <= 8
    assert first.data['count'] == 52 and len(first.data['results']) == 50
    assert len(second.data['results']) == 2
    result = first.data['results'] + second.data['results']
    assert [r['id'] for r in result] == sorted(str(row.pk) for row in rows)
    assert set(result[0]) == {'id', 'text', 'author', 'created_at_display'}
    assert set(result[0]['author']) == {'id', 'username', 'first_name', 'last_name', 'role'}
    assert str(stamp.year) not in result[0]['created_at_display']
    newer = publish_announcement(actor=manager, text='جدید')
    assert api.get(URL).data['results'][0]['id'] == str(newer.pk)


def test_no_announcement_edit_delete_or_private_chat_access(people, manager):
    row = publish_announcement(actor=manager, text='اطلاعیه')
    api = client(manager)
    for method in ('patch', 'put', 'delete'):
        assert getattr(api, method)(URL).status_code == 405
        assert getattr(api, method)(f'{URL}{row.pk}/').status_code == 404
    from apps.chat.services import open_conversation
    conversation, _ = open_conversation(actor=people.owner, participant_id=people.other.pk)
    assert api.get(f'/api/v1/chat/conversations/{conversation.pk}/').status_code == 404


def test_model_integrity_and_write_guards(people, manager):
    target = people.user('delegate', role='secretary')
    delegation, _ = delegate_publisher(actor=manager, user_id=target.pk)
    row = publish_announcement(actor=manager, text='اطلاعیه')
    for value in (row, delegation):
        with pytest.raises(ValidationError): value.save()
        with pytest.raises(ValidationError): value.save(update_fields=['created_at'])
        with pytest.raises(ValidationError): value.delete()
        with pytest.raises(ValidationError): type(value).objects.filter(pk=value.pk).update(created_at=timezone.now())
        with pytest.raises(ValidationError): type(value).objects.bulk_create([value])
    with pytest.raises(ValidationError):
        Announcement(workspace=people.workspace, author=people.foreign, text='bad').full_clean()
    with pytest.raises(ValidationError):
        AnnouncementPublisher(workspace=people.workspace, user=people.foreign).full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=AnnouncementPublisher).bulk_create([
            AnnouncementPublisher(workspace=people.workspace, user=target)])


def test_foreign_workspace_isolation_and_moved_identity(people, manager):
    target = people.user('delegate', role='secretary')
    row, _ = delegate_publisher(actor=manager, user_id=target.pk)
    publish_announcement(actor=target, text='تاریخچه')
    other_manager = people.user('manager', role='agency_manager', ws=people.foreign_workspace)
    assert client(other_manager).get(URL).data['count'] == 0
    assert client(other_manager).get(PUBLISHERS).data['count'] == 0
    assert client(other_manager).delete(f'{PUBLISHERS}{row.pk}/').status_code == 404
    User.objects.filter(pk=target.pk).update(workspace=people.foreign_workspace)
    assert client(manager).get(URL).data['results'][0]['author'] is None
    assert client(manager).get(PUBLISHERS).data['results'][0]['user'] is None
    remove_publisher(actor=manager, delegation_id=row.pk)
    assert Announcement.objects.count() == 1


def test_multiple_current_managers(people, manager):
    second = people.user('second-manager', role='agency_manager')
    target = people.user('delegate', role='range_manager')
    row, _ = delegate_publisher(actor=manager, user_id=target.pk)
    assert client(second).post(URL, {'text': 'اطلاعیه'}).status_code == 201
    assert client(second).delete(f'{PUBLISHERS}{row.pk}/').status_code == 204
    User.objects.filter(pk=second.pk).update(role='admin')
    assert client(second).post(URL, {'text': 'ممنوع'}).status_code == 403


@pytest.mark.django_db(transaction=True)
def test_concurrent_delegation_is_one_row(people, manager):
    target = people.user('delegate', role='secretary')
    barrier = Barrier(2)
    def work():
        close_old_connections()
        try:
            actor = User.objects.get(pk=manager.pk)
            barrier.wait(timeout=10)
            return delegate_publisher(actor=actor, user_id=target.pk)[0].pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work) for _ in range(2)]
        ids = [future.result(timeout=20) for future in futures]
    assert ids[0] == ids[1] and AnnouncementPublisher.objects.count() == 1
