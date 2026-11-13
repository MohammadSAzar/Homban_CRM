"""Cross-consultant entitlement without gating Core own Matching."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from rest_framework.exceptions import NotFound

from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.matching.models import MatchRecommendation
from apps.matching.live_services import live_matches
from apps.matching.services import own_profile
from apps.matching.recommendation_services import refresh_recommendation, open_recommendation, change_manual_status
from apps.matching.tests.test_live import world
from apps.matching.tests.test_generation import world as generation_world, drain, event

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('mode', ['personal', None, 'team'])
@pytest.mark.parametrize('direction', ['file', 'customer'])
@pytest.mark.parametrize('weak', [False, True])
def test_live_candidate_entitlement_and_score_preservation(world, mode, direction, weak):
    actor = world.own
    source = world.own_file if direction == 'file' else world.own_customer
    own = world.own_customer if direction == 'file' else world.own_file
    other = world.other_customer if direction == 'file' else world.other_file
    data = {'overrides': {'minimum_score': 100}} if weak else {}
    if weak:
        world.own_file.parking = world.other_file.parking = False
        world.own_file.save(); world.other_file.save()
    baseline = live_matches(actor=actor, source_id=source.pk, direction=direction, data=data, weak_preview=weak)
    Workspace.objects.filter(pk=world.ws.pk).update(workspace_mode=mode, customer_type='agency_manager')
    User.objects.filter(pk=actor.pk).update(is_workspace_owner=True, is_staff=True, is_superuser=True)
    rows = live_matches(actor=actor, source_id=source.pk, direction=direction, data=data, weak_preview=weak)
    ids = {row['candidate']['id'] for row in rows}
    assert str(own.pk) in ids
    assert (str(other.pk) in ids) == (mode == 'team')
    assert next(r for r in rows if r['candidate']['id'] == str(own.pk)) == next(r for r in baseline if r['candidate']['id'] == str(own.pk))
    assert all(('collaboration_reference' in r) == (r['candidate']['id'] != str(own.pk)) for r in rows)
    assert 'SECRET' not in str(rows)


@pytest.mark.parametrize('mode', ['personal', None])
def test_own_profile_and_recommendation_lifecycle_remain_core(world, mode):
    Workspace.objects.filter(pk=world.ws.pk).update(workspace_mode=mode)
    profile = own_profile(actor=world.own)
    assert profile.user_id == world.own.pk
    with patch('apps.matching.capability_policy.cross_consultant_allowed', side_effect=AssertionError('own pair entitlement')):
        row = refresh_recommendation(viewer=world.own, property_file=world.own_file, customer=world.own_customer)
    assert row.is_currently_recommended
    assert open_recommendation(actor=world.own, recommendation_id=row.pk)[0].user_status == 'seen'
    assert change_manual_status(actor=world.own, recommendation_id=row.pk, status='done').user_status == 'done'


@pytest.mark.parametrize('mode', ['personal', None])
def test_historical_cross_owner_rows_hidden_and_reconciled(world, mode):
    row = refresh_recommendation(viewer=world.own, property_file=world.own_file, customer=world.other_customer)
    Workspace.objects.filter(pk=world.ws.pk).update(workspace_mode=mode)
    assert not MatchRecommendation.objects.for_viewer(world.own).filter(pk=row.pk).exists()
    with pytest.raises(NotFound):
        open_recommendation(actor=world.own, recommendation_id=row.pk)
    with pytest.raises(NotFound):
        change_manual_status(actor=world.own, recommendation_id=row.pk, status='seen')
    reconciled = refresh_recommendation(viewer=world.own, property_file=world.own_file, customer=world.other_customer)
    assert not reconciled.is_currently_recommended
    assert MatchRecommendation.objects.filter(pk=row.pk).exists()
    with pytest.raises(ValidationError):
        refresh_recommendation(viewer=world.other, property_file=world.own_file, customer=world.other_customer)


@pytest.mark.parametrize('mode', ['personal', None, 'team'])
def test_generation_respects_entitlement_and_retains_history(generation_world, mode):
    ws, actor, other, _, file, customer = generation_world
    drain(event(generation_world))
    historical_ids = set(MatchRecommendation.objects.values_list('pk', flat=True))
    assert len(historical_ids) == 2
    Workspace.objects.filter(pk=ws.pk).update(workspace_mode=mode)
    drain(event(generation_world))
    assert set(MatchRecommendation.objects.values_list('pk', flat=True)) == historical_ids
    assert MatchRecommendation.objects.filter(is_currently_recommended=True).count() == (2 if mode == 'team' else 0)
    # A fresh cross-owner pair is never generated when entitlement is denied.
    from apps.customers.services import create_customer
    fresh = create_customer(workspace=ws, assigned_to=other, name='new', mobile='private',
        customer_type='buyer', min_area=100, max_area=100, bedrooms=2, budget=1000, all_regions=True)
    drain(event(generation_world))
    assert MatchRecommendation.objects.filter(customer=fresh).count() == (2 if mode == 'team' else 0)
    customer.assigned_to = actor; customer.save()
    drain(event(generation_world))
    assert MatchRecommendation.objects.active_for_viewer(actor).filter(customer=customer).exists()
