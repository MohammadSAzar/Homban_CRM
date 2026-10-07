from unittest.mock import patch
import pytest
from django.db import transaction
from apps.matching.tests.test_live import world, client_for
from apps.matching.models import MatchRecommendation, RecommendationWork
from apps.matching.recommendation_services import refresh_recommendation, change_manual_status, open_recommendation, ChangeContext
from apps.matching.collaboration_services import create_from_live, open_request
from apps.matching.collaboration_references import issue_reference
from apps.matching.collaboration_models import CollaborationRequest, CollaborationEvent
from apps.matching.generation import process_work
from apps.matching.generation_events import record_work
from apps.notifications.models import Notification
from apps.notifications.producers import recommendation_notification, collaboration_notification
from apps.notifications.services import set_read

pytestmark=pytest.mark.django_db


def refresh(world,**kwargs):
    return refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.other_customer,**kwargs)


def test_matching_initial_viewer_only_and_retries(world):
    row=refresh(world)
    note=Notification.objects.get()
    assert note.recipient_id==world.own.pk and str(row.pk) in note.action_url
    assert client_for(world.own).get(note.action_url).status_code==200
    refresh(world)
    recommendation_notification(row,'created')
    assert Notification.objects.count()==1
    note.refresh_from_db(); assert not note.is_read
    sibling=refresh_recommendation(viewer=world.other,property_file=world.own_file,customer=world.other_customer)
    assert Notification.objects.filter(recipient=world.other,source_id=sibling.pk).count()==1
    assert Notification.objects.filter(recipient=world.own).count()==1


@pytest.mark.parametrize('status',['seen','rejected','done'])
def test_manual_transition_has_no_notification(world,status):
    row=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.own_customer)
    change_manual_status(actor=world.own,recommendation_id=row.pk,status=status)
    assert Notification.objects.count()==1
    open_recommendation(actor=world.own,recommendation_id=row.pk)
    assert Notification.objects.count()==1


def test_seen_improvement_and_read_independent(world):
    world.own_file.parking=False; world.own_file.save()
    row=refresh(world)
    change_manual_status(actor=world.own,recommendation_id=row.pk,status='seen')
    world.own_file.parking=True; world.own_file.save()
    row=refresh(world,change=ChangeContext(material_inputs_changed=True))
    assert row.has_improved_score and row.user_status=='seen'
    assert Notification.objects.count()==1
    note=Notification.objects.get()
    set_read(actor=world.own,pk=note.pk,read=True)
    row.refresh_from_db(); assert row.has_improved_score


@pytest.mark.parametrize('status',['rejected','done'])
def test_legitimate_reactivation_once_and_plain_refresh_no_reset(world,status):
    row=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.own_customer)
    change_manual_status(actor=world.own,recommendation_id=row.pk,status=status)
    row=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.own_customer)
    assert row.user_status==status and Notification.objects.count()==1
    row=refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.own_customer,
        change=ChangeContext(material_inputs_changed=True))
    assert row.user_status=='new' and Notification.objects.count()==2
    recommendation_notification(row,'reactivated')
    refresh_recommendation(viewer=world.own,property_file=world.own_file,customer=world.own_customer,
        change=ChangeContext(material_inputs_changed=True))
    assert Notification.objects.count()==2


def test_below_threshold_and_expired_not_notifications(world):
    row=refresh(world)
    change_manual_status(actor=world.own,recommendation_id=row.pk,status='rejected')
    world.own_file.total_price=999999; world.own_file.save()
    refresh(world,change=ChangeContext(material_inputs_changed=True))
    assert Notification.objects.count()==1
    world.own_file.status='inactive'; world.own_file.save(); refresh(world)
    assert Notification.objects.count()==1
    world.own_file.status='active'; world.own_file.total_price=1000; world.own_file.save()
    assert refresh(world).user_status=='new'
    assert Notification.objects.count()==2


def drain(work):
    for _ in range(100):
        work.refresh_from_db()
        if work.completed: return
        process_work(work.pk,work.step)
    raise AssertionError('unfinished work')


def test_generation_notifications_watermark_duplicate_and_stale(world):
    work=record_work(world.ws.pk,'file',world.own_file.pk)
    drain(work)
    initial=Notification.objects.count(); assert initial==3  # own-own once, cross-owner twice
    row=MatchRecommendation.objects.get(viewer=world.own,property_file=world.own_file,customer=world.own_customer)
    change_manual_status(actor=world.own,recommendation_id=row.pk,status='rejected')
    older=record_work(world.ws.pk,'file',world.own_file.pk)
    newer=record_work(world.ws.pk,'file',world.own_file.pk)
    drain(newer); drain(older); drain(newer)
    assert Notification.objects.count()==initial+1
    note=Notification.objects.filter(source_id=row.pk).latest('created_at')
    assert note.event_key.endswith(f'reactivated:{newer.pk}')


def submit(world,actor=None,file=None,customer=None):
    actor=actor or world.own; file=file or world.own_file; customer=customer or world.other_customer
    return create_from_live(actor=actor,reference=issue_reference(actor,file,customer))


def test_collaboration_event_is_single_authority(world):
    created=submit(world)
    event=CollaborationEvent.objects.get()
    note=Notification.objects.get()
    assert note.recipient_id==world.other.pk and not Notification.objects.filter(recipient=world.own).exists()
    submit(world); submit(world,actor=world.other)
    collaboration_notification(event)
    for status in (None,'accepted','rejected','seen'):
        open_request(actor=world.other,request_id=created['id'],status=status)
    assert Notification.objects.count()==1 and CollaborationEvent.objects.count()==1
    note.refresh_from_db(); assert not note.is_read
    before=CollaborationRequest.objects.get().manual_status
    set_read(actor=world.other,pk=note.pk,read=True)
    assert CollaborationRequest.objects.get().manual_status==before
    assert client_for(world.other).get(note.action_url).status_code==200


def test_collaboration_restoration_and_different_participant_pair(world):
    first=submit(world)
    world.own_file.assigned_to=world.other; world.own_file.save()
    world.own_file.assigned_to=world.own; world.own_file.save()
    assert submit(world)['id']==first['id'] and Notification.objects.count()==1
    third=world.user('third')
    world.own_file.assigned_to=third; world.own_file.save()
    second=submit(world,actor=third)
    assert second['id']!=first['id'] and Notification.objects.count()==2


@pytest.mark.parametrize('producer',['matching','collaboration'])
def test_parent_rollback_and_publication_failure_atomic(world,producer):
    def run(): return refresh(world) if producer=='matching' else submit(world)
    with pytest.raises(RuntimeError),transaction.atomic():
        run(); raise RuntimeError('rollback')
    assert not Notification.objects.exists()
    assert not MatchRecommendation.objects.exists() and not CollaborationRequest.objects.exists()
    with patch('apps.notifications.producers.publish_notification',side_effect=RuntimeError('insert failed')):
        with pytest.raises(RuntimeError): run()
    assert not MatchRecommendation.objects.exists() and not CollaborationRequest.objects.exists()
    assert not CollaborationEvent.objects.exists()
    run(); assert Notification.objects.count()==1


def test_generation_publication_failure_rolls_back_cursor(world):
    work=record_work(world.ws.pk,'file',world.own_file.pk)
    process_work(work.pk,0)
    before=RecommendationWork.objects.values().get(pk=work.pk)
    with patch('apps.notifications.producers.publish_notification',side_effect=RuntimeError('insert failed')):
        with pytest.raises(RuntimeError): process_work(work.pk,1)
    assert RecommendationWork.objects.values().get(pk=work.pk)==before
    assert not MatchRecommendation.objects.exists() and not Notification.objects.exists()
    drain(work); assert Notification.objects.exists()


@pytest.mark.parametrize('producer',['matching','collaboration'])
def test_rendered_secret_fixture_privacy_and_no_source_loading(world,producer):
    if producer=='matching': refresh(world); actor=world.own
    else: submit(world); actor=world.other
    client=client_for(actor)
    note=Notification.objects.get()
    for url in ('/api/v1/notifications/',f'/api/v1/notifications/{note.pk}/'):
        response=client.get(url); assert response.status_code==200
        text=response.content.decode()
        for secret in ('SECRET','score','minimum_score','profile','owner_phone','visit_contact_phone',
                       'mobile','address','description','assigned_to','recipient','event_key','source_id'):
            assert secret not in text
    assert set(Notification.objects.values().get())=={'id','workspace_id','recipient_id','kind','title','message',
        'action_url','source_type','source_id','event_key','read_at','created_at'}
