import re
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction, IntegrityError
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.tasks.tests.conftest import people
from apps.matching.tests.test_live import client_for
from apps.notifications.models import Notification, _NOTIFICATION_WRITE
from apps.notifications.services import publish_notification, set_read, read_all
from common.jalali import schedule

pytestmark = pytest.mark.django_db
URL = '/api/v1/notifications/'


def publish(user, key='event:1', **changes):
    return publish_notification(**dict(recipient=user, event_key=key,
        kind='match_recommendation', title='تطبیق جدید', message='پیشنهاد جدید آماده است.',
        action_url='/api/v1/daily-tasks/matching/test/', **changes))


def rows(response):
    assert response.status_code == 200, response.data
    return response.data['results']


def test_model_defaults_and_indexes(people):
    row, created = publish(people.owner)
    assert created and isinstance(row.pk, uuid.UUID)
    assert row.workspace_id == people.workspace.pk and row.recipient_id == people.owner.pk
    assert row.read_at is None and not row.is_read and timezone.is_aware(row.created_at)
    assert len(Notification._meta.indexes) == 2
    assert not {'is_read','status','updated_at','payload'} & {f.name for f in Notification._meta.fields}
    again, created = publish(people.owner)
    assert not created and again.pk == row.pk
    other, created = publish(people.other)
    assert created and other.pk != row.pk


@pytest.mark.parametrize('kind', Notification.Kind.values)
def test_shared_service_supported_kind(people,kind):
    # Future kinds are a service contract only; no future producers are implemented.
    args=dict(recipient=people.owner,event_key='future',kind=kind,title='اعلان',message='پیام',action_url='/review/')
    assert publish_notification(**args)[0].kind == kind


@pytest.mark.parametrize('field,value', [('kind','bad'),('title','x'*121),('message','x'*401),
    ('event_key','x'*201),('source_type','x'*41),('title',' '),('message',' '),('event_key',' '),
    ('action_url','https://example.com'),('action_url','//evil.test'),('action_url','javascript:alert(1)'),
    ('action_url','/\\evil'),('action_url','/%2f%2fevil'),('action_url','/a/../b'),('action_url','/x?next=https://evil')])
def test_invalid_publication(people,field,value):
    args=dict(recipient=people.owner,event_key='event',kind='match_recommendation',title='عنوان',message='پیام',action_url='/detail/')
    args[field]=value
    with pytest.raises(ValidationError): publish_notification(**args)
    assert not Notification.objects.exists()


@pytest.mark.parametrize('state',['inactive_user','inactive_workspace','internal','moved'])
def test_authoritative_recipient_noop(people,state):
    stale=people.owner
    from apps.accounts.models import User
    if state=='inactive_user': models.QuerySet(model=User).filter(pk=stale.pk).update(is_active=False)
    elif state=='inactive_workspace': people.workspace.is_active=False; people.workspace.save()
    elif state=='internal': stale=people.user('internal',ws=None)
    else: models.QuerySet(model=User).filter(pk=stale.pk).update(workspace=people.foreign_workspace)
    assert publish(stale)==(None,False)
    assert not Notification.objects.exists()


@pytest.mark.parametrize('change',['workspace','recipient','title','read_at'])
def test_model_validation_and_immutable_event(people,change):
    row,_=publish(people.owner)
    if change=='workspace': row.workspace=people.foreign_workspace
    elif change=='recipient': row.recipient=people.other
    elif change=='title': row.title='تغییر'
    else: row.read_at=datetime(2026,1,1)
    with pytest.raises(ValidationError): row.save(_token=_NOTIFICATION_WRITE)


@pytest.mark.parametrize('operation',['save','partial','update','bulk_create','bulk_update','delete','row_delete'])
def test_write_bypasses_guarded(people,operation):
    row,_=publish(people.owner)
    with pytest.raises(ValidationError):
        if operation=='save': row.save()
        elif operation=='partial': row.save(_token=_NOTIFICATION_WRITE,update_fields=['read_at'])
        elif operation=='update': Notification.objects.update(read_at=timezone.now())
        elif operation=='bulk_create': Notification.objects.bulk_create([row])
        elif operation=='bulk_update': Notification.objects.bulk_update([row],['read_at'])
        elif operation=='delete': Notification.objects.all().delete()
        else: row.delete()


def test_database_constraints_and_history(people):
    row,_=publish(people.owner)
    for values in ({'kind':'bad'},):
        with pytest.raises(IntegrityError), transaction.atomic():
            models.QuerySet(model=Notification).filter(pk=row.pk).update(**values)
    duplicate=Notification(workspace=people.workspace,recipient=people.owner,kind=row.kind,title=row.title,
        message=row.message,action_url=row.action_url,event_key=row.event_key)
    with pytest.raises(IntegrityError),transaction.atomic():
        models.QuerySet(model=Notification).bulk_create([duplicate])
    with pytest.raises(ProtectedError): people.owner.delete()
    with pytest.raises(ProtectedError): people.workspace.delete()


@pytest.mark.parametrize('role',['consultant','agency_manager','range_manager','secretary','admin'])
def test_all_roles_private_no_flag_bypass(people,role):
    actor=people.user(role,role)
    actor.is_staff=actor.is_superuser=actor.is_workspace_owner=True; actor.save()
    mine,_=publish(actor); other,_=publish(people.other); foreign,_=publish(people.foreign)
    client=client_for(actor)
    assert [r['id'] for r in rows(client.get(URL))]==[str(mine.pk)]
    for target in (other,foreign):
        assert client.get(f'{URL}{target.pk}/').status_code==404
        for action in ('read','unread'):
            assert client.post(f'{URL}{target.pk}/{action}/',{},format='json').status_code==404
    assert client.post(URL+'read-all/',{},format='json').data=={'affected_count':1}
    other.refresh_from_db(); foreign.refresh_from_db()
    assert not other.is_read and not foreign.is_read
    assert client.get(URL+'unread-count/').data=={'unread_count':0}


def test_read_lifecycle_and_get_purity(people):
    row,_=publish(people.owner)
    client=client_for(people.owner); detail=f'{URL}{row.pk}/'
    before=Notification.objects.values().get(pk=row.pk)
    assert client.get(detail).status_code==200
    assert Notification.objects.values().get(pk=row.pk)==before
    first=client.post(detail+'read/',{},format='json'); assert first.data['is_read']
    row.refresh_from_db(); read_at=row.read_at; assert timezone.is_aware(read_at)
    client.post(detail+'read/',{},format='json'); row.refresh_from_db(); assert row.read_at==read_at
    assert client.post(URL+'read-all/',{},format='json').data['affected_count']==0
    row.refresh_from_db(); assert row.read_at==read_at
    for _ in range(2):
        assert not client.post(detail+'unread/',{},format='json').data['is_read']
    row.refresh_from_db(); assert row.read_at is None


@pytest.mark.parametrize('query',[{'status':'bad'},{'kind':'bad'},{'recipient':'x'},{'workspace':'x'},{'page':0}])
def test_strict_filters(people,query):
    assert client_for(people.owner).get(URL,query).status_code==400


def test_filter_count_and_strict_actions(people):
    one,_=publish(people.owner); two,_=publish(people.owner,'event:2')
    publish_notification(recipient=people.owner,kind='collaboration_request',event_key='collab',
        title='همکاری',message='درخواست',action_url='/detail/')
    set_read(actor=people.owner,pk=one.pk,read=True)
    client=client_for(people.owner)
    assert len(rows(client.get(URL)))==2
    assert len(rows(client.get(URL,{'status':'read'})))==1
    assert len(rows(client.get(URL,{'status':'all','kind':'match_recommendation'})))==2
    assert client.get(URL+'unread-count/').data['unread_count']==2
    for path in (URL+'read-all/',f'{URL}{two.pk}/read/',f'{URL}{two.pk}/unread/'):
        assert client.post(path,{'read_at':'x'},format='json').status_code==400
        assert client.post(path,[],format='json').status_code==400
    assert client.post(URL,{},format='json').status_code==405
    assert client.patch(f'{URL}{two.pk}/',{},format='json').status_code==405
    assert client.delete(f'{URL}{two.pk}/').status_code==405
    assert client.get(URL+'unread-count/',{'recipient':'x'}).status_code==400


def test_auth_revalidation(people):
    assert APIClient().get(URL).status_code==401
    client=client_for(people.owner)
    people.owner.is_active=False; people.owner.save()
    assert client.get(URL).status_code==401
    people.owner.is_active=True; people.owner.save()
    people.workspace.is_active=False; people.workspace.save()
    assert client.get(URL).status_code==401
    internal=people.user('internal',ws=None); internal.is_staff=internal.is_superuser=True; internal.save()
    assert client_for(internal).get(URL).status_code==401
    from rest_framework.exceptions import PermissionDenied
    with pytest.raises(PermissionDenied): read_all(actor=people.owner)


def test_jalali_output_no_private_metadata(people):
    with patch('django.utils.timezone.now',return_value=schedule('1405/07/15','10:30')):
        row,_=publish(people.owner)
    response=client_for(people.owner).get(f'{URL}{row.pk}/')
    assert response.data['created_at_display']=='چهارشنبه ۱۵ مهر ۱۴۰۵ ساعت ۱۰:۳۰'
    assert response.data['created_date']=='1405/07/15' and response.data['created_time']=='10:30'
    assert not {'event_key','recipient','workspace','created_at','source_id','source_type'} & response.data.keys()
    assert not re.search(r'[12][0-9]{3}-[0-9]{2}-[0-9]{2}',response.content.decode())


def test_bounded_queries_pagination_and_count(people):
    publish(people.owner)
    client=client_for(people.owner)
    with CaptureQueriesContext(connection) as captured: rows(client.get(URL))
    small=len(captured)
    stamp=schedule('1405/07/15','10:30')
    with patch('django.utils.timezone.now',return_value=stamp):
        for n in range(51): publish(people.owner,f'event:{n+2}')
    with CaptureQueriesContext(connection) as captured:
        response=client.get(URL); first=rows(response)
    assert len(captured)==small<=8
    second=rows(client.get(URL,{'page':2}))
    assert response.data['count']==52 and len(first)==50 and len(second)==2
    expected=[str(pk) for pk in Notification.objects.order_by('-created_at','-pk').values_list('pk',flat=True)]
    assert [r['id'] for r in first+second]==expected and len(set(expected))==52
    with CaptureQueriesContext(connection) as captured: count=client.get(URL+'unread-count/')
    assert count.data['unread_count']==52
    assert sum('COUNT(' in q['sql'] for q in captured)==1
    assert not any('matching_' in q['sql'] for q in captured)


def test_parent_rollback_retry(people):
    with pytest.raises(RuntimeError),transaction.atomic():
        publish(people.owner)
        raise RuntimeError()
    assert not Notification.objects.exists()
    assert publish(people.owner)[1]


def test_real_mysql_duplicate_publication(people,transactional_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    barrier=Barrier(2)
    def run(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            row,created=publish(people.owner)
            return str(row.pk),created
        finally: close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(run,range(2)))
    assert len({r[0] for r in results})==1
    assert sorted(r[1] for r in results)==[False,True]
    assert Notification.objects.count()==1
