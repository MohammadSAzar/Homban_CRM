import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core import signing
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, close_old_connections, models, transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.properties.models import PropertyFile
from apps.customers.models import Customer
from apps.matching.models import CollaborationRequest as Request, CollaborationEvent as Event, MatchRecommendation
from apps.matching.collaboration_models import _COLLABORATION_WRITE
from apps.matching.collaboration_references import issue_reference, SALT
from apps.matching.collaboration_services import create_from_live, create_from_recommendation, open_request
from apps.matching.recommendation_services import refresh_recommendation
from apps.matching.tests.test_live import world, client_for, endpoint

pytestmark = pytest.mark.django_db


@pytest.fixture
def pair(world):
    return world.own, world.other, world.own_file, world.other_customer


def submit(pair, reverse=False):
    actor, other, file, customer = pair
    actor = other if reverse else actor
    return create_from_live(actor=actor, reference=issue_reference(actor, file, customer))


def detail(pair, row, recipient=False, **kwargs):
    return open_request(actor=pair[1] if recipient else pair[0], request_id=row["id"], **kwargs)


def test_live_real_endpoint_reference_no_recommendation_or_score_storage(world, pair):
    response = client_for(world.own).post(endpoint(world.own_file), {}, format="json")
    candidate = next(row for row in response.data["results"] if str(row["candidate"]["id"]) == str(world.other_customer.pk))
    assert "collaboration_reference" in candidate
    own = next(row for row in response.data["results"] if str(row["candidate"]["id"]) == str(world.own_customer.pk))
    assert "collaboration_reference" not in own
    result = client_for(world.own).post('/api/v1/collaboration-requests/from-live-match/', {"reference": candidate["collaboration_reference"]}, format="json")
    assert result.status_code == 201, result.data
    assert not MatchRecommendation.objects.exists()
    assert Request.objects.count() == Event.objects.count() == 1
    assert Event.objects.get().request.recipient_id == world.other.pk
    assert "score" not in str(Request.objects.values().get())
    assert "SECRET" not in str(result.data)
    payload = signing.loads(candidate["collaboration_reference"], salt=SALT)
    assert set(payload) == {"actor", "workspace", "file", "customer", "ownership"}
    assert str(world.other.pk) not in str(payload)
    assert "SECRET" not in str(payload)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("status", ["new", "seen", "accepted", "rejected"])
def test_unordered_repeat_all_statuses(pair, reverse, status):
    first = submit(pair)
    if status != "new":
        detail(pair, first, recipient=True, status=status)
    second = submit(pair, reverse)
    assert second["id"] == first["id"] and not second["created"]
    assert second["manual_status"] == status
    assert second["other_consultant"]["id"] == str(pair[0 if reverse else 1].pk)
    assert second["url"] == f'/api/v1/collaboration-requests/{first["id"]}/'
    assert Request.objects.count() == Event.objects.count() == 1
    assert second["requester"]["id"] == str(pair[0].pk)


def test_saved_recommendation_and_reverse_paths_share_row(pair):
    actor, other, file, customer = pair
    rec = refresh_recommendation(viewer=actor, property_file=file, customer=customer)
    response = client_for(actor).post(f'/api/v1/match-recommendations/{rec.pk}/collaboration-request/', {}, format='json')
    assert response.status_code == 201, response.data
    assert submit(pair, True)["id"] == response.data["id"]
    rec.refresh_from_db()
    assert rec.user_status == "new"  # Does not touch viewer status/baseline/score.


@pytest.mark.parametrize("kind", ["own_own", "other_viewer", "expired", "reassigned", "missing"])
def test_saved_invalid(pair, world, kind):
    actor, other, file, customer = pair
    rec = refresh_recommendation(viewer=actor, property_file=file, customer=customer)
    if kind == 'own_own':
        customer.assigned_to = actor; customer.save()
    elif kind == 'other_viewer':
        actor = other
    elif kind == 'expired':
        file.status = 'inactive'; file.save()
        refresh_recommendation(viewer=actor, property_file=file, customer=customer)
    elif kind == 'reassigned':
        file.assigned_to = other; file.save()
    else:
        rec.pk = uuid.uuid4()
    with pytest.raises((ValidationError, NotFound)):
        create_from_recommendation(actor=actor, recommendation_id=rec.pk)
    assert not Request.objects.exists() and not Event.objects.exists()


@pytest.mark.parametrize("kind", ["tampered", "expired", "wrong_actor", "wrong_workspace", "reassigned", "file_inactive", "customer_inactive", "type", "malformed"])
def test_token_rechecked(pair, world, kind):
    actor, other, file, customer = pair
    if kind == 'expired':
        with patch('django.core.signing.time.time', return_value=1):
            token = issue_reference(actor, file, customer)
    else:
        token = issue_reference(actor, file, customer)
    if kind == 'tampered': token += 'x'
    elif kind == 'wrong_actor': actor = other
    elif kind == 'wrong_workspace': actor = world.foreign_user
    elif kind == 'reassigned':
        customer.assigned_to = world.user('new'); customer.save()
    elif kind == 'file_inactive':
        file.status = 'archived'; file.save()
    elif kind == 'customer_inactive':
        customer.status = 'completed'; customer.save()
    elif kind == 'type':
        Customer.objects.filter(pk=customer.pk).update(customer_type='tenant', budget=None, deposit_budget=1000, monthly_rent_budget=0)
    elif kind == 'malformed': token = signing.dumps({"bad": "payload"}, salt=SALT)
    with pytest.raises((ValidationError, NotFound)):
        create_from_live(actor=actor, reference=token)
    assert not Request.objects.exists()


@pytest.mark.parametrize("role", ['agency_manager', 'range_manager', 'secretary', 'admin'])
def test_role_and_flags_no_bypass(world, pair, role):
    actor = world.user('privileged', role)
    actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True; actor.save()
    row = submit(pair)
    with pytest.raises(PermissionDenied): open_request(actor=actor, request_id=row['id'])
    assert issue_reference(actor, pair[2], pair[3]) is None


def test_unrelated_foreign_guessed_and_flags_denied(world, pair):
    row = submit(pair)
    unrelated = world.user('unrelated')
    unrelated.is_staff = unrelated.is_superuser = unrelated.is_workspace_owner = True; unrelated.save()
    for actor in [unrelated, world.foreign_user]:
        for action in ({}, {'status':'accepted'}):
            with pytest.raises(NotFound): open_request(actor=actor, request_id=row['id'], **action)
        assert issue_reference(actor, pair[2], pair[3]) is None
    with pytest.raises(NotFound): open_request(actor=pair[0], request_id=uuid.uuid4())
    with pytest.raises(PermissionDenied): detail(pair, row, status='accepted')


def test_status_open_reversibility_idempotence_no_scoring(pair):
    row = submit(pair)
    assert detail(pair, row)['manual_status'] == 'new'
    assert detail(pair, row, recipient=True)['manual_status'] == 'seen'
    with patch('apps.matching.engine.evaluate_match', side_effect=AssertionError('no scoring')):
        for status in ('accepted','rejected','seen','rejected','accepted'):
            value = detail(pair, row, recipient=True, status=status)
            again = detail(pair, row, recipient=True, status=status)
            assert value['manual_status'] == again['manual_status'] == status
            assert value['updated_at'] == again['updated_at']
            assert detail(pair, row, recipient=True)['manual_status'] == status
    assert Event.objects.count() == 1
    with pytest.raises(ValidationError): detail(pair, row, recipient=True, status='new')


@pytest.mark.parametrize('which', ['file','customer','user','workspace'])
def test_expiry_restoration_preserves_status_and_event(pair, which):
    actor, other, file, customer = pair
    row = submit(pair)
    detail(pair, row, recipient=True, status='rejected')
    source = {'file':file, 'customer':customer, 'user':other, 'workspace':actor.workspace}[which]
    field = 'status' if which in ('file','customer') else 'is_active'
    setattr(source, field, 'inactive' if field == 'status' else False); source.save()
    if which == 'workspace':
        with pytest.raises(PermissionDenied): detail(pair,row)
    else:
        expired = detail(pair,row)
        assert not expired['is_valid'] and expired['property_file'] is None and expired['customer'] is None
        assert expired['manual_status'] == 'rejected'
    setattr(source, field, 'active' if field == 'status' else True); source.save()
    assert detail(pair,row,recipient=True)['manual_status'] == 'rejected'
    assert detail(pair,row)['is_valid']
    assert not submit(pair)['created'] and Event.objects.count() == 1


def test_reassignment_history_new_pair_then_restoration(world,pair):
    actor,other,file,customer=pair
    row=submit(pair)
    detail(pair,row,recipient=True,status='accepted')
    new=world.user('newowner')
    file.assigned_to=new; file.save()
    historical=detail(pair,row)
    assert not historical['is_valid'] and historical['property_file'] is None and historical['customer'] is None
    with pytest.raises(ValidationError): detail(pair,row,recipient=True,status='rejected')
    new_row=submit((new,other,file,customer))
    assert new_row['id'] != row['id']
    file.assigned_to=actor; file.save()
    assert submit(pair)['id']==row['id']
    assert detail(pair,row)['manual_status']=='accepted'
    assert Request.objects.count()==Event.objects.count()==2


def test_confidentiality_and_identity_all_participants(pair):
    row=submit(pair)
    for recipient in [False,True]:
        result=detail(pair,row,recipient=recipient)
        assert set(result['requester'])=={'id','username','first_name','last_name','role'}
        assert set(result['recipient'])==set(result['requester'])
        assert result['property_file']['code']==pair[2].code and result['customer']['code']==pair[3].code
        banned={'current_score','final_score','normalized_score','minimum_score','weights','profile','owner_name','owner_phone','visit_contact_phone','mobile','name','address','description','images','valuable_reasons','assigned_to','hard_constraints','overrides'}
        def walk(value):
            if isinstance(value,dict):
                assert not banned & set(value)
                for nested in value.values(): walk(nested)
            elif isinstance(value,list):
                for nested in value: walk(nested)
        walk(result)
        assert 'SECRET' not in str(result)


@pytest.mark.parametrize('data',[{'status':'new'},{'status':'done'},{'status':'accepted','recipient':'anything'},{'manual_status':'accepted'},[]])
def test_strict_status_api(pair,data):
    row=submit(pair)
    response=client_for(pair[1]).post(row['url']+'status/',data,format='json')
    assert response.status_code==400


def test_api_no_list_delete_patch_anonymous_or_arbitrary_ids(pair):
    row=submit(pair)
    client=client_for(pair[0])
    assert client.get('/api/v1/collaboration-requests/').status_code==404
    assert client.delete(row['url']).status_code==405
    assert client.patch(row['url'],{'manual_status':'accepted'},format='json').status_code==405
    assert APIClient().get(row['url']).status_code==401
    assert client.post('/api/v1/collaboration-requests/from-live-match/',{'file':str(pair[2].pk),'customer':str(pair[3].pk)},format='json').status_code==400
    response=client_for(pair[1]).post(row['url']+'status/',{'status':'accepted'},format='json')
    assert response.status_code==200 and response.data['manual_status']=='accepted'


def test_history_and_bulk_guards(pair):
    row=submit(pair); obj=Request.objects.get(pk=row['id'])
    for action in [lambda:obj.save(),lambda:obj.delete(),lambda:Request.objects.all().delete(),lambda:Request.objects.all().update(manual_status='seen'),lambda:Request.objects.bulk_update([obj],['manual_status']),lambda:Request.objects.bulk_create([obj]),lambda:obj.save(_token=_COLLABORATION_WRITE,update_fields=['manual_status']),lambda:Event.objects.get().save(),lambda:Event.objects.get().delete()]:
        with pytest.raises(ModelValidationError): action()
    obj.requester_id=uuid.uuid4()
    with pytest.raises(ModelValidationError): obj.save(_token=_COLLABORATION_WRITE)
    from django.db.models.deletion import ProtectedError
    with pytest.raises(ProtectedError): pair[2].delete()


def test_database_constraints(pair):
    row=submit(pair); obj=Request.objects.get(pk=row['id'])
    for values in [dict(manual_status='bad'),dict(participant_a_id=obj.participant_b_id),dict(requester_id=obj.recipient_id)]:
        with pytest.raises(IntegrityError), transaction.atomic():
            models.QuerySet(model=Request).filter(pk=obj.pk).update(**values)
    obj.pk=uuid.uuid4(); obj._state.adding=True
    with pytest.raises(IntegrityError), transaction.atomic(): models.QuerySet(model=Request).bulk_create([obj])


def test_event_failure_rolls_back_request(pair):
    with patch.object(Event,'save',side_effect=RuntimeError('failure')):
        with pytest.raises(RuntimeError): submit(pair)
    assert not Request.objects.exists()
    assert submit(pair)['created']


@pytest.mark.django_db(transaction=True)
def test_opposite_concurrent_creation(pair):
    barrier=Barrier(2)
    def create(reverse):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return submit(pair,reverse)['id']
        finally: close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(create,False); b=pool.submit(create,True)
        assert a.result(timeout=30)==b.result(timeout=30)
    assert Request.objects.count()==Event.objects.count()==1

@pytest.mark.parametrize('direction', ['file', 'customer'])
def test_both_live_directions_issue_only_qualifying_refs(world, direction):
    actor = world.own if direction == 'file' else world.other
    source = world.own_file if direction == 'file' else world.other_customer
    candidate_id = world.other_customer.pk if direction == 'file' else world.own_file.pk
    client = client_for(actor)
    result = client.post(endpoint(source), {}, format='json')
    row = next(row for row in result.data['results'] if row['candidate']['id'] == str(candidate_id))
    assert 'collaboration_reference' in row
    for changes in ({'hard_constraints': {'parking': True}}, {'overrides': {'minimum_score': 100}}):
        world.own_file.parking = False
        world.own_file.save()
        result = client.post(endpoint(source), changes, format='json')
        assert all(row['candidate']['id'] != str(candidate_id) for row in result.data['results'])
    world.own_file.total_price = 1000000
    world.own_file.save()
    result = client.post(endpoint(source), {}, format='json')
    assert all(row['candidate']['id'] != str(candidate_id) for row in result.data['results'])
    # A manager's operational scope is not consultant participation.
    result = client_for(world.agency).post(endpoint(source), {}, format='json')
    assert all('collaboration_reference' not in row for row in result.data['results'])


def test_customer_owner_saved_creation_and_no_recipient_profile(pair):
    actor, other, file, customer = pair
    rec = refresh_recommendation(viewer=other, property_file=file, customer=customer)
    from apps.matching.models import MatchingProfile
    assert not MatchingProfile.objects.filter(user=actor).exists()
    result = create_from_recommendation(actor=other, recommendation_id=rec.pk)
    assert result['requester']['id'] == str(other.pk)
    assert result['recipient']['id'] == str(actor.pk)
    assert not MatchingProfile.objects.filter(user=actor).exists()
    assert MatchRecommendation.objects.count() == 1


def test_stale_flags_and_sources_are_both_checked(pair):
    actor, other, file, customer = pair
    rec = refresh_recommendation(viewer=actor, property_file=file, customer=customer)
    models.QuerySet(model=MatchRecommendation).filter(pk=rec.pk).update(is_viewer_valid=False, is_currently_recommended=False)
    with pytest.raises(ValidationError): create_from_recommendation(actor=actor, recommendation_id=rec.pk)
    models.QuerySet(model=MatchRecommendation).filter(pk=rec.pk).update(is_viewer_valid=True)
    file.status = 'inactive'; file.save()  # Generation has not run: saved flags are stale TRUE.
    with pytest.raises(ValidationError): create_from_recommendation(actor=actor, recommendation_id=rec.pk)
    assert not Request.objects.exists()


@pytest.mark.parametrize('kind', ['foreign_file', 'foreign_customer', 'foreign_region', 'foreign_preference'])
def test_corrupt_cross_workspace_sources_fail_closed(world, pair, kind):
    from apps.customers.models import CustomerRegionPreference
    actor, _, file, customer = pair
    row = submit(pair)
    token = issue_reference(actor, file, customer)
    if kind == 'foreign_file':
        PropertyFile.objects.filter(pk=file.pk).update(workspace=world.foreign_ws)
    elif kind == 'foreign_customer':
        Customer.objects.filter(pk=customer.pk).update(workspace=world.foreign_ws)
    elif kind == 'foreign_region':
        PropertyFile.objects.filter(pk=file.pk).update(region=world.foreign_region)
    else:
        models.QuerySet(model=CustomerRegionPreference).filter(customer=customer).update(region=world.foreign_region)
    with pytest.raises((NotFound, ValidationError)):
        create_from_live(actor=actor, reference=token)
    response = client_for(actor).get(row['url'])
    if kind.startswith('foreign_') and kind in ('foreign_file', 'foreign_customer'):
        assert response.status_code == 404
    else:
        assert response.status_code == 200
        assert response.data['is_valid'] is False
        assert response.data['property_file'] is response.data['customer'] is None
    assert 'SECRET' not in response.content.decode()


@pytest.mark.parametrize('kind', ['inactive', 'workspace_less', 'recipient_role'])
def test_current_account_state_checked_at_submission(pair, kind):
    actor, other, file, customer = pair
    token = issue_reference(actor, file, customer)
    if kind == 'inactive':
        actor.is_active = False; actor.save()
    elif kind == 'workspace_less':
        User.objects.filter(pk=actor.pk).update(workspace=None)
    else:
        other.role = 'admin'; other.save()
    with pytest.raises((PermissionDenied, ValidationError)):
        create_from_live(actor=actor, reference=token)
    assert not Request.objects.exists()


def test_requester_inactive_and_recipient_role_invalidate_history(pair):
    row = submit(pair)
    actor, other, _, _ = pair
    actor.is_active = False; actor.save()
    assert not detail(pair, row, recipient=True)['is_valid']
    with pytest.raises(ValidationError): detail(pair, row, recipient=True, status='accepted')
    actor.is_active = True; actor.save()
    other.role = 'admin'; other.save()
    assert not detail(pair, row)['is_valid']
    with pytest.raises(PermissionDenied): detail(pair, row, recipient=True)


def test_other_pair_separate_and_event_unique(pair, world):
    first = submit(pair)
    second = submit((pair[0], pair[1], world.file(), pair[3]))
    assert first['id'] != second['id']
    event = Event.objects.get(request_id=first['id'])
    event.pk = uuid.uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        models.QuerySet(model=Event).bulk_create([event])
    assert Event.objects.count() == 2


@pytest.mark.parametrize('field', ['property_file_id', 'customer_id', 'requester_id', 'recipient_id', 'participant_a_id', 'participant_b_id'])
def test_every_reference_immutable(pair, field):
    row = Request.objects.get(pk=submit(pair)['id'])
    setattr(row, field, uuid.uuid4())
    with pytest.raises(ModelValidationError): row.full_clean()


def test_invalid_new_model_and_protected_event(pair):
    actor, other, file, customer = pair
    a, b = sorted([actor.pk, other.pk])
    row = Request(property_file=file, customer=customer, requester=actor, recipient=actor, participant_a_id=a, participant_b_id=b)
    with pytest.raises(ModelValidationError): row.save(_token=_COLLABORATION_WRITE)
    result = submit(pair)
    row = Request.objects.get(pk=result['id'])
    assert isinstance(row.pk, uuid.UUID) and row.manual_status == 'new'
    from django.db.models.deletion import ProtectedError
    with pytest.raises(ProtectedError): pair[3].delete()
    with pytest.raises(ProtectedError): models.QuerySet(model=Request).filter(pk=row.pk).delete()
    with pytest.raises(ModelValidationError): Event.objects.all().update(created_at=row.created_at)


def test_query_count_does_not_grow_with_preferences(pair, world):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from apps.locations.models import Region
    from apps.customers.services import set_preferred_regions
    row = submit(pair)
    # Read as requester: no status save/full_clean side effect.
    with CaptureQueriesContext(connection) as one:
        result = detail(pair, row)
    regions = [world.region] + [Region.objects.create(workspace=world.ws, city=world.city, name=f'منطقه {n}') for n in range(5)]
    set_preferred_regions(customer=pair[3], regions=regions)
    with CaptureQueriesContext(connection) as many:
        result = detail(pair, row)
    assert len(result['customer']['preferred_regions']) == 6
    assert len(one) == len(many) and len(many) <= 8


def test_rendered_api_privacy_duplicate_detail_and_status(pair):
    actor, other, file, customer = pair
    actor.phone_number = 'SECRET_PHONE'; actor.save()
    actor.first_name = 'محمد'; actor.last_name = 'مشاور'; actor.save()
    token = issue_reference(actor, file, customer)
    client = client_for(actor)
    url = '/api/v1/collaboration-requests/from-live-match/'
    created = client.post(url, {'reference':token}, format='json')
    duplicate = client.post(url, {'reference':token}, format='json')
    detail_response = client_for(other).get(created.data['url'])
    status_response = client_for(other).post(created.data['url']+'status/', {'status':'accepted'}, format='json')
    for response in (created,duplicate,detail_response,status_response):
        assert response.status_code in (200,201), response.data
        rendered = response.content.decode()
        for secret in ('SECRET', 'current_score', 'normalized_score', 'minimum_score', 'phone_number', 'owner_phone', 'mobile', 'description', 'MatchingProfile', 'hard_constraints', 'overrides'):
            assert secret not in rendered
        assert response.data['requester']['first_name'] == 'محمد'


@pytest.mark.parametrize('data', [[], {'reference':'bad','workspace':'x'}, {'reference':None}])
def test_strict_live_creation_request(pair, data):
    response = client_for(pair[0]).post('/api/v1/collaboration-requests/from-live-match/',data,format='json')
    assert response.status_code == 400


def test_forged_reference_payload_shapes_rejected(pair):
    from apps.matching.collaboration_references import verify_reference
    actor, _, file, customer = pair
    valid = signing.loads(issue_reference(actor,file,customer),salt=SALT)
    for payload in ([], {**valid,'file':'invalid'}, {**valid,'ownership':1}, {**valid,'ownership':'short'}):
        with pytest.raises(ValidationError): verify_reference(signing.dumps(payload,salt=SALT),actor)
