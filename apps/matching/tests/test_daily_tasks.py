from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext

from apps.matching.models import MatchRecommendation, CollaborationRequest, CollaborationEvent, MatchingProfile
from apps.matching.recommendation_services import refresh_recommendation, change_manual_status
from apps.matching.collaboration_services import create_from_live, open_request
from apps.matching.collaboration_references import issue_reference
from apps.matching.generation_events import suppress_events
from apps.matching.tests.test_live import world, client_for, endpoint

pytestmark = pytest.mark.django_db
FEED = '/api/v1/daily-tasks/'


def rec(w, own=False):
    return refresh_recommendation(viewer=w.own, property_file=w.own_file,
                                  customer=w.own_customer if own else w.other_customer)


def collab(w, incoming=False):
    return create_from_live(actor=w.other if incoming else w.own,
                           reference=issue_reference(w.other if incoming else w.own, w.own_file, w.other_customer))


def url(row, kind='matching', action=False):
    return f'{FEED}{kind}/{row.pk if hasattr(row, "pk") else row["id"]}/' + ('status/' if action else '')


def results(response):
    assert response.status_code == 200, response.data
    return response.data['results']


def test_mixed_feed_priority_and_read_only(world):
    r = rec(world)
    c = collab(world, incoming=True)
    before = list(MatchRecommendation.objects.values())
    with patch('apps.matching.recommendation_services.evaluate_match', side_effect=AssertionError('feed scoring')):
        rows = results(client_for(world.own).get(FEED))
    assert [x['id'] for x in rows] == [c['id'], str(r.pk)]
    assert rows[0]['direction'] == 'incoming'
    assert 'score' not in str(rows[0])
    assert 'SECRET' not in str(rows)
    assert list(MatchRecommendation.objects.values()) == before


@pytest.mark.parametrize('kind,status', [('all','active'),('all','all'),('matching','new'),('collaboration','new'),
    ('all','seen'),('all','rejected'),('all','done'),('all','accepted'),('all','expired')])
def test_filter_empty_and_populated_sql(world, kind, status):
    rec(world); collab(world)
    rows = results(client_for(world.own).get(FEED, {'type':kind,'status':status}))
    expected = 2 if kind == 'all' and status in ('active','all') else 1 if status == 'new' else 0
    assert len(rows) == expected


@pytest.mark.parametrize('query', [{'type':'bad'},{'status':'bad'},{'page':0},{'page':'no'},
    {'type':'matching','status':'accepted'},{'type':'collaboration','status':'done'}, {'workspace':'x'}])
def test_strict_feed_filters(world, query):
    assert client_for(world.own).get(FEED, query).status_code == 400


@pytest.mark.parametrize('role', ['agency_manager','range_manager','secretary','admin'])
def test_nonconsultants_have_no_matching_flag_bypass(world, role):
    actor = world.user('denied', role)
    actor.is_staff = actor.is_superuser = actor.is_workspace_owner = True
    actor.save()
    # Daily Tasks now permits personal manual tasks; matching authority is unchanged.
    assert results(client_for(actor).get(FEED)) == []
    row = rec(world)
    assert client_for(actor).get(url(row)).status_code == 403


def test_viewer_isolation_and_foreign_detail(world):
    r = rec(world)
    collab(world)
    outsider = world.user('outsider')
    for actor in (outsider, world.foreign_user):
        assert results(client_for(actor).get(FEED, {'status':'all'})) == []
        assert client_for(actor).get(url(r)).status_code == 404
        assert client_for(actor).post(url(r, action=True), {'status':'seen'}, format='json').status_code == 404
    assert results(client_for(world.other).get(FEED, {'type':'matching','status':'all'})) == []


@pytest.mark.parametrize('status', ['new','seen','rejected','done'])
def test_detail_refresh_preserves_decisions_and_baseline(world, status):
    r = rec(world, own=True)
    if status != 'new':
        change_manual_status(actor=world.own, recommendation_id=r.pk, status=status)
    with patch('apps.matching.recommendation_services.evaluate_match', wraps=__import__('apps.matching.engine', fromlist=['evaluate_match']).evaluate_match) as evaluate:
        response = client_for(world.own).get(url(r))
    assert response.status_code == 200, response.data
    assert evaluate.call_count == 1
    r.refresh_from_db()
    assert r.user_status == ('seen' if status == 'new' else status)
    assert r.score_at_last_view == r.current_score
    assert not r.has_improved_score
    assert 'SECRET' not in str(response.data)


def test_detail_recalculates_current_pair_only(world):
    r = rec(world)
    world.own_file.total_price = 5000
    world.own_file.save()
    response = client_for(world.own).get(url(r))
    assert response.status_code == 200
    r.refresh_from_db()
    assert r.is_source_valid and not r.is_currently_recommended
    assert not results(client_for(world.own).get(FEED, {'status':'expired'}))


@pytest.mark.parametrize('status', ['seen','rejected','done'])
def test_manual_actions_no_scoring_reversible(world, status):
    r = rec(world, own=True)
    with patch('apps.matching.recommendation_services.evaluate_match', side_effect=AssertionError('manual scoring')):
        response = client_for(world.own).post(url(r, action=True), {'status':status}, format='json')
        assert response.status_code == 200, response.data
        assert client_for(world.own).post(url(r, action=True), {'status':'seen'}, format='json').status_code == 200


def test_cross_owner_done_rejected_and_strict_action(world):
    r = rec(world)
    for payload in ({'status':'done'}, {'status':'new'}, {'status':'seen','current_score':100}):
        assert client_for(world.own).post(url(r, action=True), payload, format='json').status_code == 400


def test_reassignment_hides_recommendation_and_invalidates_collaboration(world):
    r = rec(world)
    c = collab(world)
    world.own_file.assigned_to = world.other
    world.own_file.save()
    client = client_for(world.own)
    assert client.get(url(r)).status_code == 404
    rows = results(client.get(FEED, {'status':'all'}))
    assert len(rows) == 1 and rows[0]['id'] == c['id'] and not rows[0]['is_valid']
    assert rows[0]['payload']['property_file_code'] is None
    assert rows[0]['payload']['customer_code'] is None
    assert len(results(client.get(FEED, {'type':'collaboration','status':'new'}))) == 1
    assert len(results(client.get(FEED, {'status':'expired'}))) == 1
    assert results(client.get(FEED)) == []


def test_collaboration_open_recipient_only_status_and_no_score(world):
    c = collab(world)
    sender, recipient = client_for(world.own), client_for(world.other)
    response = sender.get(url(c,'collaboration'))
    assert response.status_code == 200 and response.data['manual_status'] == 'new'
    response = recipient.get(url(c,'collaboration'))
    assert response.status_code == 200 and response.data['manual_status'] == 'seen'
    assert 'score' not in str(response.data) and 'SECRET' not in str(response.data)
    assert sender.post(url(c,'collaboration',True), {'status':'accepted'}, format='json').status_code == 403
    for status in ('accepted','rejected','seen'):
        response = recipient.post(url(c,'collaboration',True), {'status':status}, format='json')
        assert response.status_code == 200, response.data
    assert CollaborationEvent.objects.count() == 1


@pytest.mark.parametrize('mutation', ['none','inactive_file','inactive_customer','assignment','inactive_user','role'])
def test_sql_collaboration_validity_matches_domain(world, mutation):
    c = collab(world)
    if mutation == 'inactive_file':
        world.own_file.status='inactive'; world.own_file.save()
    elif mutation == 'inactive_customer':
        world.other_customer.status='inactive'; world.other_customer.save()
    elif mutation == 'assignment':
        world.own_file.assigned_to=world.other; world.own_file.save()
    elif mutation == 'inactive_user':
        world.other.is_active=False; world.other.save()
    elif mutation == 'role':
        models.QuerySet(model=type(world.other)).filter(pk=world.other.pk).update(role='admin')
    row = CollaborationRequest.objects.with_validity().get(pk=c['id'])
    assert row.current_validity == row.is_valid


def test_below_threshold_not_expired_source_inactive_is(world):
    r = rec(world)
    models.QuerySet(model=MatchRecommendation).filter(pk=r.pk).update(is_currently_recommended=False)
    client = client_for(world.own)
    assert results(client.get(FEED)) == []
    assert results(client.get(FEED, {'status':'expired'})) == []
    world.own_file.status='inactive'; world.own_file.save()
    assert len(results(client.get(FEED, {'status':'expired'}))) == 1


def test_feed_query_count_constant_and_paginated(world):
    rec(world); collab(world, incoming=True)
    client = client_for(world.own)
    with CaptureQueriesContext(connection) as small:
        results(client.get(FEED))
    small_queries = list(small.captured_queries)
    with suppress_events():
        for _ in range(51):
            refresh_recommendation(viewer=world.own, property_file=world.own_file, customer=world.customer())
    with CaptureQueriesContext(connection) as large:
        response = client.get(FEED)
        page1 = results(response)
    large_queries = list(large.captured_queries)
    page2 = results(client.get(FEED, {'page':2}))
    assert response.data['count'] == 53
    assert len(page1) == 50 and len(page2) == 3
    assert not {x['id'] for x in page1} & {x['id'] for x in page2}
    assert 0 < len(large_queries) == len(small_queries) <= 15
    assert any('UNION ALL' in q['sql'] and 'LIMIT 50' in q['sql'] for q in large_queries)


@pytest.mark.parametrize('source', ['own_file','own_customer'])
def test_weak_preview_below_threshold_restricted_and_no_writes(world, source):
    with suppress_events():
        MatchingProfile.objects.create(user=world.own)
    before = list(MatchingProfile.objects.values())
    response = client_for(world.own).post(endpoint(getattr(world,source))+'weak/',
        {'overrides':{'minimum_score':100},'hard_constraints':{}}, format='json')
    # Perfect pairs are not weak.
    assert results(response) == []
    world.own_file.parking = False; world.own_file.save()
    world.other_file.parking = False; world.other_file.save()
    response = client_for(world.own).post(endpoint(getattr(world,source))+'weak/',
        {'overrides':{'minimum_score':100}}, format='json')
    rows = results(response)
    assert rows and all(Decimal(x['final_score']) < 100 and not x['recommended'] for x in rows)
    assert 'SECRET' not in str(rows)
    assert list(MatchingProfile.objects.values()) == before
    assert not MatchRecommendation.objects.exists()
    assert not CollaborationRequest.objects.exists()
    assert not CollaborationEvent.objects.exists()


def test_weak_reference_creates_only_collaboration(world):
    world.own_file.parking=False; world.own_file.save()
    client = client_for(world.own)
    rows = results(client.post(endpoint(world.own_file)+'weak/', {'overrides':{'minimum_score':100}}, format='json'))
    cross = next(x for x in rows if x['candidate']['id'] == str(world.other_customer.pk))
    own = next(x for x in rows if x['candidate']['id'] == str(world.own_customer.pk))
    assert 'collaboration_reference' not in own
    response = client.post('/api/v1/collaboration-requests/from-live-match/', {'reference':cross['collaboration_reference']}, format='json')
    assert response.status_code == 201, response.data
    original_id = response.data['id']
    repeated = client.post('/api/v1/collaboration-requests/from-live-match/', {'reference':cross['collaboration_reference']}, format='json')
    assert repeated.status_code == 200 and repeated.data['id'] == original_id
    reverse_client = client_for(world.other)
    reverse_rows = results(reverse_client.post(endpoint(world.other_customer)+'weak/', {'overrides':{'minimum_score':100}}, format='json'))
    reference = next(row['collaboration_reference'] for row in reverse_rows if row['candidate']['id'] == str(world.own_file.pk))
    reverse = reverse_client.post('/api/v1/collaboration-requests/from-live-match/', {'reference':reference}, format='json')
    assert reverse.status_code == 200 and reverse.data['id'] == original_id
    assert 'score' not in str(reverse.data)
    assert not MatchRecommendation.objects.exists()
    assert CollaborationRequest.objects.count() == CollaborationEvent.objects.count() == 1


@pytest.mark.parametrize('payload', [{'unknown':1},{'overrides':{'workspace':1}},{'hard_constraints':{'parking':'bad'}}])
def test_weak_strict_validation(world, payload):
    assert client_for(world.own).post(endpoint(world.own_file)+'weak/', payload, format='json').status_code == 400


def test_weak_authorization_and_hard_constraints(world):
    client = client_for(world.own)
    assert client.post(endpoint(world.other_file)+'weak/', {}, format='json').status_code == 404
    assert client.post(endpoint(world.foreign_file)+'weak/', {}, format='json').status_code == 404
    world.own_file.parking=False; world.own_file.save()
    assert results(client.post(endpoint(world.own_file)+'weak/', {'overrides':{'minimum_score':100},'hard_constraints':{'parking':True}}, format='json')) == []


def test_weak_lazy_profile_does_not_enqueue_generation(world):
    from apps.matching.models import RecommendationWork
    before = RecommendationWork.objects.count()
    assert not MatchingProfile.objects.filter(user=world.own).exists()
    response = client_for(world.own).post(endpoint(world.own_file)+'weak/', {}, format='json')
    results(response)
    assert MatchingProfile.objects.filter(user=world.own).count() == 1
    assert RecommendationWork.objects.count() == before


def test_full_priority_order_scores_and_ties(world):
    incoming_new = collab(world, incoming=True)
    with suppress_events():
        second_file = world.file()
        incoming_seen = create_from_live(actor=world.other, reference=issue_reference(world.other, second_file, world.other_customer))
        open_request(actor=world.own, request_id=incoming_seen['id'])
        sent = create_from_live(actor=world.own, reference=issue_reference(world.own, second_file, world.customer(world.other)))
        first = rec(world)
        second = refresh_recommendation(viewer=world.own, property_file=second_file, customer=world.own_customer)
        seen = refresh_recommendation(viewer=world.own, property_file=world.own_file, customer=world.own_customer)
        change_manual_status(actor=world.own, recommendation_id=seen.pk, status='seen')
    response = results(client_for(world.own).get(FEED))
    tied = sorted([str(first.pk),str(second.pk)])
    assert [r['id'] for r in response] == [incoming_new['id'], *tied, str(seen.pk), incoming_seen['id'], sent['id']]
    models.QuerySet(model=MatchRecommendation).filter(pk=first.pk).update(current_score=75)
    response = results(client_for(world.own).get(FEED))
    assert [r['id'] for r in response[1:3]] == [str(second.pk),str(first.pk)]


@pytest.mark.parametrize('manual,kind', [('rejected','all'),('done','matching'),('accepted','collaboration')])
def test_historical_status_filters_populated(world, manual, kind):
    r = rec(world, own=True)
    c = collab(world)
    if manual in ('rejected','done'):
        change_manual_status(actor=world.own, recommendation_id=r.pk, status=manual)
    if manual in ('rejected','accepted'):
        open_request(actor=world.other, request_id=c['id'], status=manual)
    rows = results(client_for(world.own).get(FEED, {'type':kind,'status':manual}))
    assert len(rows) == (2 if manual == 'rejected' else 1)
    assert all(row['status'] == manual for row in rows)
    assert results(client_for(world.own).get(FEED, {'status':'expired'})) == []


def test_sibling_score_and_profile_never_exposed(world):
    r = rec(world)
    sibling = refresh_recommendation(viewer=world.other, property_file=world.own_file, customer=world.other_customer)
    models.QuerySet(model=MatchRecommendation).filter(pk=sibling.pk).update(current_score=Decimal('61.234567'))
    client = client_for(world.own)
    assert client.get(url(sibling)).status_code == 404
    rows = results(client.get(FEED))
    assert [x['id'] for x in rows] == [str(r.pk)]
    assert '61.234567' not in str(rows)
    assert 'minimum_score' not in str(rows) and 'criteria' not in str(rows)


def test_improvement_viewing_acknowledges_without_generation(world):
    from apps.matching.models import RecommendationWork
    r = rec(world)
    models.QuerySet(model=MatchRecommendation).filter(pk=r.pk).update(user_status='seen', score_at_last_view=60)
    client = client_for(world.own)
    assert results(client.get(FEED))[0]['payload']['has_improved_score']
    before = RecommendationWork.objects.count()
    assert client.get(url(r)).status_code == 200
    assert not results(client.get(FEED))[0]['payload']['has_improved_score']
    assert RecommendationWork.objects.count() == before


@pytest.mark.parametrize('status', ['accepted','rejected'])
def test_collaboration_decisions_preserved_on_detail_and_invalid_mutation_denied(world, status):
    c = collab(world)
    open_request(actor=world.other, request_id=c['id'], status=status)
    client = client_for(world.other)
    assert client.get(url(c,'collaboration')).data['manual_status'] == status
    world.own_file.status='inactive'; world.own_file.save()
    response = client.get(url(c,'collaboration'))
    assert response.status_code == 200 and response.data['property_file'] is None and response.data['customer'] is None
    assert client.post(url(c,'collaboration',True), {'status':'seen'}, format='json').status_code == 400


def test_unauthenticated_and_inactive_denied(world):
    from rest_framework.test import APIClient
    assert APIClient().get(FEED).status_code == 401
    client = client_for(world.own)
    world.own.is_active=False; world.own.save()
    assert client.get(FEED).status_code == 401


def test_empty_feed_creates_no_profile(world):
    assert results(client_for(world.own).get(FEED)) == []
    assert not MatchingProfile.objects.exists()


@pytest.mark.parametrize('kind', ['matching','collaboration'])
def test_detail_query_count_constant(world, kind):
    row = rec(world) if kind == 'matching' else collab(world)
    client = client_for(world.own)
    client.get(url(row,kind))
    with CaptureQueriesContext(connection) as small:
        assert client.get(url(row,kind)).status_code == 200
    small_count = len(small)
    with suppress_events():
        for _ in range(4):
            file = world.file()
            refresh_recommendation(viewer=world.own, property_file=file, customer=world.other_customer)
            create_from_live(actor=world.own, reference=issue_reference(world.own,file,world.other_customer))
    with CaptureQueriesContext(connection) as large:
        assert client.get(url(row,kind)).status_code == 200
    assert 0 < small_count == len(large) <= 50


@pytest.mark.parametrize('source', ['own_file','own_customer'])
def test_weak_query_count_constant_no_writes_and_ineligible_omitted(world, source):
    with suppress_events():
        MatchingProfile.objects.create(user=world.own)
        world.own_file.parking=False; world.own_file.save()
        world.other_file.parking=False; world.other_file.save()
    client = client_for(world.own)
    weak = endpoint(getattr(world,source))+'weak/'
    payload = {'overrides':{'minimum_score':100}}
    with CaptureQueriesContext(connection) as small:
        results(client.post(weak,payload,format='json'))
    small_count = len(small)
    with suppress_events():
        for _ in range(4):
            world.file(parking=False) if source == 'own_customer' else world.customer()
        excluded = world.file(total_price=5000) if source == 'own_customer' else world.customer(budget=5000)
    with CaptureQueriesContext(connection) as large:
        rows = results(client.post(weak,payload,format='json'))
    assert str(excluded.pk) not in {r['candidate']['id'] for r in rows}
    assert 0 < small_count == len(large) <= 20
    assert not any(q['sql'].lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in large)


def test_weak_real_cap_pagination_order(world):
    import uuid
    from apps.properties.models import PropertyFile
    def candidate():
        return PropertyFile(workspace=world.ws,assigned_to=world.other,city=world.city,region=world.region,
            transaction_type='sale',area=100,bedrooms=2,total_price=1000,price_per_square_meter=0,
            code=f'PF-{uuid.uuid4().hex.upper()}')
    PropertyFile.objects.bulk_create([candidate() for _ in range(998)])
    client = client_for(world.own)
    weak = endpoint(world.own_customer)+'weak/'
    payload = {'overrides':{'minimum_score':100}}
    response = client.post(weak,payload,format='json')
    page1 = results(response)
    page2 = results(client.post(weak+'?page=2',payload,format='json'))
    assert response.data['count'] == 998 and len(page1) == len(page2) == 50
    ids = [r['candidate']['id'] for r in page1+page2]
    assert ids == sorted(ids) and len(set(ids)) == 100
    assert [r['candidate']['id'] for r in results(client.post(weak,payload,format='json'))] == ids[:50]
    PropertyFile.objects.bulk_create([candidate()])
    with patch('apps.matching.live_services.evaluate_match') as evaluate:
        assert client.post(weak,payload,format='json').status_code == 400
        evaluate.assert_not_called()


def test_collaboration_timestamp_ties_across_pages(world):
    from django.utils import timezone
    with suppress_events():
        for _ in range(51):
            create_from_live(actor=world.own, reference=issue_reference(world.own,world.file(),world.other_customer))
    models.QuerySet(model=CollaborationRequest).update(created_at=timezone.now())
    client = client_for(world.own)
    first = results(client.get(FEED))
    second = results(client.get(FEED,{'page':2}))
    ids = [row['id'] for row in first+second]
    assert ids == sorted(ids) and len(set(ids)) == 51
    assert [row['id'] for row in results(client.get(FEED))] == ids[:50]


def test_sql_validity_rejects_corrupt_cross_workspace_preference(world):
    from apps.customers.models import CustomerRegionPreference
    c = collab(world)
    models.QuerySet(model=CustomerRegionPreference).filter(customer=world.other_customer).update(region=world.foreign_region)
    row = CollaborationRequest.objects.with_validity().get(pk=c['id'])
    assert not row.current_validity and not row.is_valid
    response = results(client_for(world.own).get(FEED, {'status':'expired'}))
    assert response[0]['payload']['customer_code'] is None


def test_recommendation_detail_rejects_corrupt_location(world):
    from apps.properties.models import PropertyFile
    r = rec(world)
    models.QuerySet(model=PropertyFile).filter(pk=world.own_file.pk).update(region=world.foreign_region)
    assert client_for(world.own).get(url(r)).status_code == 404


def test_detail_lazy_profile_does_not_enqueue_generation(world):
    from apps.matching.models import RecommendationWork
    r = rec(world)
    MatchingProfile.objects.filter(user=world.own).delete()
    before = RecommendationWork.objects.count()
    assert client_for(world.own).get(url(r)).status_code == 200
    assert MatchingProfile.objects.filter(user=world.own).count() == 1
    assert RecommendationWork.objects.count() == before


def test_weak_unscorable_and_zero_threshold_excluded(world):
    from apps.matching.models import WEIGHT_FIELDS
    from apps.customers.models import Customer
    models.QuerySet(model=Customer).filter(pk=world.own_customer.pk).update(min_building_age=None,max_building_age=None)
    client = client_for(world.own)
    weights = {name:0 for name in WEIGHT_FIELDS}
    weights['building_age']=1
    assert results(client.post(endpoint(world.own_customer)+'weak/', {'overrides':weights},format='json')) == []
    assert results(client.post(endpoint(world.own_customer)+'weak/', {'overrides':{'minimum_score':0}},format='json')) == []
