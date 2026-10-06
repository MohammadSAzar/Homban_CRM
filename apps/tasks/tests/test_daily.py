import re
from unittest.mock import patch
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from common import jalali
from apps.tasks.services import create_task, change_status
from apps.matching.models import MatchRecommendation
from apps.matching.recommendation_services import refresh_recommendation, change_manual_status
from apps.matching.collaboration_services import create_from_live, open_request
from apps.matching.collaboration_references import issue_reference
from apps.matching.tests.test_live import world, client_for

pytestmark = pytest.mark.django_db
FEED='/api/v1/daily-tasks/'
NOW=jalali.schedule('1405/07/15','10:30')


def task(actor, day='1405/07/15', clock='11:00', all_day=False):
    data={'title':'پیگیری','jalali_date':day,'is_all_day':all_day}
    if not all_day: data['time']=clock
    return create_task(actor=actor,data=data)


def rows(response):
    assert response.status_code==200,response.data
    return response.data['results']


@pytest.mark.parametrize('role',['consultant','agency_manager','range_manager','secretary','admin'])
def test_role_aware_three_way_scope(world,role):
    actor=world.own
    mine=task(actor)
    task(world.other)
    rec=refresh_recommendation(viewer=actor,property_file=world.own_file,customer=world.other_customer)
    collab=create_from_live(actor=actor,reference=issue_reference(actor,world.own_file,world.other_customer))
    actor.role=role
    actor.is_staff=actor.is_superuser=actor.is_workspace_owner=True
    actor.save()
    client=client_for(actor)
    with patch('django.utils.timezone.now',return_value=NOW):
        result=rows(client.get(FEED))
        expected={str(mine.pk),str(rec.pk),collab['id']} if role=='consultant' else {str(mine.pk)}
        assert {r['id'] for r in result}==expected
        if role!='consultant':
            assert rows(client.get(FEED,{'type':'matching','status':'all'}))==[]
            assert rows(client.get(FEED,{'type':'collaboration','status':'all'}))==[]
            assert client.get(f'{FEED}matching/{rec.pk}/').status_code==403
            assert client.get(f'{FEED}collaboration/{collab["id"]}/').status_code==403


def test_priority_with_overdue_and_all_day(world):
    incoming=create_from_live(actor=world.other,reference=issue_reference(world.other,world.own_file,world.other_customer))
    rec=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.other_customer)
    yesterday=task(world.own,'1405/07/14',all_day=True)
    timed=task(world.own,clock='10:00')
    today=task(world.own,all_day=True)
    future=task(world.own,'1405/07/16')
    later=task(world.own,clock='18:00')
    client=client_for(world.own)
    with patch('django.utils.timezone.now',return_value=NOW):
        response=client.get(FEED)
    result=rows(response)
    assert [r['id'] for r in result]==[incoming['id'],str(yesterday.pk),str(timed.pk),str(today.pk),str(later.pk),str(rec.pk)]
    assert result[1]['is_overdue'] and result[2]['is_overdue'] and not result[3]['is_overdue']
    assert str(future.pk) not in str(result)
    assert not re.search(r'\b[12][0-9]{3}-[0-9]{2}-[0-9]{2}',response.content.decode())


def test_daily_tehran_midnight(world):
    yesterday=task(world.own,'1405/07/14',all_day=True)
    today=task(world.own,all_day=True)
    now=jalali.schedule('1405/07/15','00:30')  # Still the previous UTC day.
    client=client_for(world.own)
    with patch('django.utils.timezone.now',return_value=now):
        result=rows(client.get(FEED,{'type':'manual'}))
    assert [r['id'] for r in result]==[str(yesterday.pk),str(today.pk)]
    assert result[0]['is_overdue'] and not result[1]['is_overdue']


@pytest.mark.parametrize('status',['new','seen','accepted','rejected','expired'])
def test_manual_incompatible_filters(world,status):
    assert client_for(world.own).get(FEED,{'type':'manual','status':status}).status_code==400


@pytest.mark.parametrize('kind',['matching','collaboration'])
@pytest.mark.parametrize('status',['pending','cancelled'])
def test_other_types_incompatible_filters(world,kind,status):
    assert client_for(world.own).get(FEED,{'type':kind,'status':status}).status_code==400


@pytest.mark.parametrize('status',['pending','done','cancelled','all'])
def test_manual_filters_and_combined_done(world,status):
    row=task(world.own)
    other=task(world.other)
    rec=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.own_customer)
    if status in ('done','cancelled'): change_status(actor=world.own,pk=row.pk,status=status)
    change_manual_status(actor=world.own,recommendation_id=rec.pk,status='done')
    client=client_for(world.own)
    result=rows(client.get(FEED,{'type':'manual','status':status}))
    assert [r['id'] for r in result]==[str(row.pk)]
    combined=rows(client.get(FEED,{'status':status}))
    expected={str(row.pk),str(rec.pk)} if status in ('all','done') else {str(row.pk)}
    assert {r['id'] for r in combined}==expected
    assert str(other.pk) not in str(combined)


def test_daily_detail_timestamp_contract_and_collaboration_unchanged_elsewhere(world):
    rec=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.other_customer)
    collab=create_from_live(actor=world.own,reference=issue_reference(world.own,world.own_file,world.other_customer))
    client=client_for(world.own)
    for endpoint in (FEED,f'{FEED}matching/{rec.pk}/',f'{FEED}collaboration/{collab["id"]}/'):
        response=client.get(endpoint)
        assert response.status_code==200
        text=response.content.decode()
        assert not re.search(r'\b[12][0-9]{3}-[0-9]{2}-[0-9]{2}',text)
        assert 'created_at_display' in text and '"created_at":' not in text and '"updated_at":' not in text
    old=client.get(f'/api/v1/collaboration-requests/{collab["id"]}/')
    assert 'created_at' in old.data  # Unrelated historical API is deliberately unchanged.
    recipient=client_for(world.other)
    response=recipient.post(f'{FEED}collaboration/{collab["id"]}/status/',{'status':'accepted'},format='json')
    assert response.status_code==200 and 'created_at' not in response.data and 'score' not in str(response.data)


def test_mixed_database_pagination_constant_queries(world):
    rec=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.other_customer)
    create_from_live(actor=world.other,reference=issue_reference(world.other,world.own_file,world.other_customer))
    task(world.own)
    client=client_for(world.own)
    with CaptureQueriesContext(connection) as captured:
        rows(client.get(FEED,{'status':'all'}))
    count=len(captured)
    for _ in range(51): task(world.own)
    with CaptureQueriesContext(connection) as captured:
        response=client.get(FEED,{'status':'all'})
        first=rows(response)
    # A page can omit a type entirely: at most the same three hydration queries.
    assert 0 < len(captured) <= count <= 12
    assert any('UNION ALL' in q['sql'] and 'LIMIT 50' in q['sql'] for q in captured)
    second=rows(client.get(FEED,{'status':'all','page':2}))
    assert response.data['count']==54 and len(first)==50 and len(second)==4
    assert len({r['id'] for r in first+second})==54
    assert [r['id'] for r in first]==[r['id'] for r in rows(client.get(FEED,{'status':'all'}))]


def test_manual_daily_alias_does_not_grant_other_owner_access(world):
    row=task(world.other)
    client=client_for(world.own)
    assert client.get(f'{FEED}manual/{row.pk}/').status_code==404
    assert client.post(f'{FEED}manual/{row.pk}/status/',{'status':'done'},format='json').status_code==404
