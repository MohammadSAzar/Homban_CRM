import re
from unittest.mock import patch
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from apps.matching.tests.test_live import client_for
from apps.tasks.models import ManualTask
from apps.tasks.services import create_task, change_status
from common import jalali

pytestmark = pytest.mark.django_db
LIST = '/api/v1/manual-tasks/'
CALENDAR = '/api/v1/calendar/tasks/'
PAYLOAD = {'title':'پیگیری قرارداد','jalali_date':'1405/07/15','time':'10:30'}


def create(actor, **changes):
    return create_task(actor=actor,data={**PAYLOAD,**changes})


def detail(row): return f'{LIST}{row.pk}/'


def rows(response):
    assert response.status_code == 200,response.data
    return response.data['results']


def assert_jalali(response):
    text = response.content.decode('utf-8')
    assert not re.search(r'\b[12][0-9]{3}-[0-9]{2}-[0-9]{2}',text)
    for field in ('scheduled_for','created_at','updated_at','completed_at'):
        assert f'"{field}":' not in text
    assert not re.search(r'January|October|Monday|Tuesday|Wednesday|Saturday',text)


@pytest.mark.parametrize('role', ['consultant','agency_manager','range_manager','secretary','admin'])
def test_all_roles_own_tasks_only(people, role):
    actor = people.user(role,role)
    actor.is_staff=actor.is_superuser=actor.is_workspace_owner=True; actor.save()
    other = create(people.other)
    client = client_for(actor)
    response = client.post(LIST,PAYLOAD,format='json')
    assert response.status_code == 201,response.data
    row = ManualTask.objects.get(pk=response.data['id'])
    assert row.owner_id == actor.pk and row.workspace_id == actor.workspace_id
    assert [r['id'] for r in rows(client.get(LIST))] == [str(row.pk)]
    assert client.get(detail(other)).status_code == 404
    assert client.patch(detail(other),{'title':'تغییر'},format='json').status_code == 404
    assert client.post(detail(other)+'status/',{'status':'done'},format='json').status_code == 404


@pytest.mark.parametrize('field', ['workspace','owner','status','scheduled_for','created_at','updated_at','completed_at','date','is_superuser'])
def test_protected_payload_fields(people, field):
    client = client_for(people.owner)
    assert client.post(LIST,{**PAYLOAD,field:'bad'},format='json').status_code == 400
    row = create(people.owner)
    assert client.patch(detail(row),{field:'bad'},format='json').status_code == 400


@pytest.mark.parametrize('payload', [{}, {'title':'','jalali_date':'1405/07/15','time':'10:00'},
    {**PAYLOAD,'title':'x'*201}, {**PAYLOAD,'jalali_date':'2026-10-07'}, {**PAYLOAD,'time':'24:00'},
    {**PAYLOAD,'is_all_day':True}, {'title':'کار','jalali_date':'1405/07/15'}, {**PAYLOAD,'is_all_day':'true'}])
def test_invalid_create(people, payload):
    response = client_for(people.owner).post(LIST,payload,format='json')
    assert response.status_code == 400,response.data
    assert not ManualTask.objects.exists()


def test_output_patch_status_and_daily_aliases(people):
    client = client_for(people.owner)
    response = client.post(LIST,{**PAYLOAD,'jalali_date':'۱۴۰۵/۰۷/۱۵'},format='json')
    assert response.status_code == 201
    assert response.data['jalali_date'] == '1405/07/15'
    assert response.data['date_display'] == 'چهارشنبه ۱۵ مهر ۱۴۰۵'
    assert response.data['weekday'] == 'چهارشنبه' and response.data['month_name'] == 'مهر'
    assert_jalali(response)
    row = ManualTask.objects.get(pk=response.data['id'])
    before = row.updated_at
    alias = f'/api/v1/daily-tasks/manual/{row.pk}/'
    assert_jalali(client.get(alias))
    row.refresh_from_db(); assert row.status == 'pending' and row.updated_at == before
    for status in ('done','cancelled','pending'):
        response = client.post(alias+'status/',{'status':status},format='json')
        assert response.status_code == 200 and response.data['status'] == status
        assert bool(response.data['completed_at_display']) == (status == 'done')
        assert_jalali(response)
    response = client.patch(detail(row),{'title':'اصلاح عنوان'},format='json')
    assert response.status_code == 200 and response.data['time'] == '10:30'
    response = client.patch(detail(row),{'is_all_day':True},format='json')
    assert response.status_code == 200 and response.data['time'] is None
    assert client.patch(detail(row),{'is_all_day':False},format='json').status_code == 400
    assert client.patch(detail(row),{'is_all_day':False,'time':'09:00'},format='json').status_code == 200
    assert client.delete(detail(row)).status_code == 405
    assert client.patch(alias,{'title':'تغییر'},format='json').status_code == 405


@pytest.mark.parametrize('query', [{},{'from':'1405/01/01'},{'from':'1405/01/01','to':'bad'},
    {'from':'1405/02/01','to':'1405/01/01'}, {'from':'1399/01/01','to':'1400/01/01'},
    {'from':'2026-01-01','to':'2026-01-02'}, {'from':'1405/01/01','to':'1405/01/02','owner':'x'}])
def test_calendar_invalid_range(people, query):
    assert client_for(people.owner).get(CALENDAR,query).status_code == 400


@pytest.mark.parametrize('start,end,dates', [
    ('1405/07/15','1405/07/15',['1405/07/15']),
    ('1405/06/31','1405/07/01',['1405/06/31','1405/07/01']),
    ('1403/12/29','1404/01/01',['1403/12/29','1403/12/30','1404/01/01']),
    ('1399/01/01','1399/12/30',['1399/01/01','1399/12/30'])])
def test_calendar_inclusive_jalali_ranges(people,start,end,dates):
    for day in dates: create(people.owner,jalali_date=day)
    create(people.owner,jalali_date='1406/01/01')
    create(people.other,jalali_date=start)
    client = client_for(people.owner)
    response = client.get(CALENDAR,{'from':start,'to':end})
    assert [r['jalali_date'] for r in rows(response)] == dates
    assert_jalali(response)


def test_status_and_optional_list_bounds(people):
    first = create(people.owner,jalali_date='1405/07/14')
    second = create(people.owner,jalali_date='1405/07/16')
    change_status(actor=people.owner,pk=first.pk,status='done')
    client = client_for(people.owner)
    assert [r['id'] for r in rows(client.get(LIST))] == [str(second.pk)]
    assert len(rows(client.get(LIST,{'status':'all'}))) == 2
    assert len(rows(client.get(LIST,{'status':'all','from':'1405/07/15'}))) == 1
    assert len(rows(client.get(LIST,{'status':'all','to':'1405/07/15'}))) == 1
    query = {'from':'1405/07/14','to':'1405/07/16'}
    assert len(rows(client.get(CALENDAR,query))) == 2
    assert len(rows(client.get(CALENDAR,{**query,'status':'done'}))) == 1
    assert client.post(CALENDAR,PAYLOAD,format='json').status_code == 405


def test_auth_workspace_and_guessing(people):
    row = create(people.owner)
    assert APIClient().get(LIST).status_code == 401
    client = client_for(people.foreign)
    assert rows(client.get(LIST)) == []
    assert client.get(detail(row)).status_code == 404
    client = client_for(people.owner)
    people.workspace.is_active=False; people.workspace.save()
    assert client.get(LIST).status_code == 401


def test_inactive_and_internal_user(people):
    client = client_for(people.owner)
    people.owner.is_active=False; people.owner.save()
    assert client.post(LIST,PAYLOAD,format='json').status_code == 401
    internal = people.user('internal',ws=None)
    internal.is_staff=internal.is_superuser=True; internal.save()
    assert client_for(internal).get(LIST).status_code == 401


def test_calendar_query_bound_and_stable_pages(people):
    create(people.owner)
    client = client_for(people.owner)
    query = {'from':'1405/07/15','to':'1405/07/15'}
    with CaptureQueriesContext(connection) as small: rows(client.get(CALENDAR,query))
    count = len(small)
    for _ in range(51): create(people.owner)
    with CaptureQueriesContext(connection) as large:
        response = client.get(CALENDAR,query)
        first = rows(response)
    assert 0 < count == len(large) <= 8
    second = rows(client.get(CALENDAR,{**query,'page':2}))
    assert len(first) == 50 and len(second) == 2 and response.data['count'] == 52
    ids = [r['id'] for r in first+second]
    assert ids == sorted(ids) and len(set(ids)) == 52


def test_list_range_not_limited_to_calendar_window(people):
    create(people.owner,jalali_date='1399/01/01')
    create(people.owner,jalali_date='1400/01/01')
    response=client_for(people.owner).get(LIST,{'from':'1399/01/01','to':'1400/01/01'})
    assert len(rows(response))==2


def test_status_payload_strict_and_calendar_midnight_inclusive(people):
    row=create(people.owner,time='00:00')
    client=client_for(people.owner)
    for payload in ({'status':'new'},{'status':'done','completed_at':'1405/07/15'}):
        assert client.post(detail(row)+'status/',payload,format='json').status_code==400
    response=client.get(CALENDAR,{'from':'1405/07/15','to':'1405/07/15'})
    assert [r['id'] for r in rows(response)]==[str(row.pk)]
    create(people.owner,jalali_date='1405/07/16',time='00:00')
    assert len(rows(client.get(CALENDAR,{'from':'1405/07/15','to':'1405/07/15'})))==1
