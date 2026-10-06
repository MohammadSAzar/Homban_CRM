"""Database-ordered heterogeneous feed; only a page of domain rows is materialized."""
from django.db import transaction
from django.utils import timezone
from common.jalali import today_bounds, timestamp_display
from apps.tasks.models import ManualTask
from apps.tasks.services import lock_actor
from apps.tasks.serializers import task_card
from django.db.models import Case, DateTimeField, DecimalField, F, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from rest_framework.pagination import PageNumberPagination
from .models import MatchRecommendation, CollaborationRequest
from .collaboration_services import _actor, open_request
from .daily_serializers import recommendation_card, collaboration_card
from .live_serializers import FileCandidateSerializer, CustomerCandidateSerializer, matching_result_data
from .recommendation_services import open_recommendation


class DailyPagination(PageNumberPagination):
    page_size = 50


def feed_index(actor, *, type, status, now=None):
    now = now or timezone.now()
    today, tomorrow = today_bounds(now)
    matches = MatchRecommendation.objects.for_viewer(actor)
    collaborations = CollaborationRequest.objects.for_participant(actor).with_validity()
    manual = ManualTask.objects.for_owner(actor)
    if actor.role != 'consultant':
        matches, collaborations = matches.none(), collaborations.none()
    valid = Q(is_viewer_valid=True, is_source_valid=True, property_file__status='active', customer__status='active')
    if status in ('active', 'new', 'seen'):
        matches = matches.filter(valid, is_currently_recommended=True,
                                 user_status__in=('new', 'seen') if status == 'active' else (status,))
        collaborations = collaborations.filter(manual_status__in=('new', 'seen') if status == 'active' else (status,))
        if status == 'active':
            collaborations = collaborations.filter(current_validity=True)
    elif status == 'expired':
        matches = matches.exclude(valid)
        collaborations = collaborations.filter(current_validity=False)
    elif status != 'all':
        matches = matches.filter(user_status=status)
        collaborations = collaborations.filter(manual_status=status)
    if type not in ('all','matching') or status in ('accepted','pending','cancelled'):
        matches = matches.none()
    if type not in ('all','collaboration') or status in ('done','pending','cancelled'):
        collaborations = collaborations.none()
    if type not in ('all','manual') or status not in ('active','pending','done','cancelled','all'):
        manual = manual.none()
    elif status == 'active':
        manual = manual.filter(status='pending', scheduled_for__lt=tomorrow)
    elif status != 'all':
        manual = manual.filter(status=status)
    overdue = Q(status='pending') & (Q(is_all_day=True, scheduled_for__lt=today) |
                                      Q(is_all_day=False, scheduled_for__lt=now))
    score = DecimalField(max_digits=33, decimal_places=30)
    fields = ('id', '_kind', '_priority', '_score', '_asc_time', '_desc_time')
    matches = matches.order_by().annotate(
        _kind=Value('match_recommendation'),
        _priority=Case(When(user_status='new', then=Value(3)), When(user_status='seen', then=Value(4)), default=Value(7), output_field=IntegerField()),
        _score=Coalesce('current_score', Value(0), output_field=score),
        _asc_time=Value(None, output_field=DateTimeField()),
        _desc_time=Value(None, output_field=DateTimeField()),
    ).values(*fields)
    collaborations = collaborations.order_by().annotate(
        _kind=Value('collaboration_request'),
        _priority=Case(When(recipient_id=actor.pk, manual_status='new', then=Value(0)),
                       When(recipient_id=actor.pk, manual_status='seen', then=Value(5)),
                       When(requester_id=actor.pk, then=Value(6)), default=Value(8), output_field=IntegerField()),
        _score=Value(0, output_field=score),
        _asc_time=Value(None, output_field=DateTimeField()), _desc_time=F('created_at'),
    ).values(*fields)
    manual = manual.order_by().annotate(
        _kind=Value('manual_task'),
        _priority=Case(When(overdue, then=Value(1)),
                       When(status='pending', scheduled_for__gte=today, scheduled_for__lt=tomorrow, then=Value(2)),
                       default=Value(9), output_field=IntegerField()),
        _score=Value(0, output_field=score), _asc_time=F('scheduled_for'),
        _desc_time=Value(None, output_field=DateTimeField()),
    ).values(*fields)
    return matches.union(collaborations, manual, all=True).order_by('_priority', '-_score', '_asc_time', '-_desc_time', 'id', '_kind')


@transaction.atomic
def feed_page(*, actor, filters, request):
    actor = lock_actor(actor)
    now = timezone.now()
    pagination = DailyPagination()
    index = feed_index(actor, type=filters['type'], status=filters['status'], now=now)
    page = pagination.paginate_queryset(index, request)
    match_ids = [item['id'] for item in page if item['_kind'] == 'match_recommendation']
    collaboration_ids = [item['id'] for item in page if item['_kind'] == 'collaboration_request']
    manual_ids = [item['id'] for item in page if item['_kind'] == 'manual_task']
    matches = MatchRecommendation.objects.for_viewer(actor).filter(pk__in=match_ids).select_related('property_file', 'customer')
    collaborations = CollaborationRequest.objects.for_participant(actor).filter(pk__in=collaboration_ids).with_validity().select_related('property_file', 'customer', 'requester', 'recipient')
    cards = {('match_recommendation', row.pk): recommendation_card(row) for row in matches}
    cards.update({('collaboration_request', row.pk): collaboration_card(row, actor) for row in collaborations})
    cards.update({('manual_task', row.pk): task_card(row, now=now) for row in ManualTask.objects.for_owner(actor).filter(pk__in=manual_ids)})
    return pagination.get_paginated_response([cards[(item['_kind'], item['id'])] for item in page])


@transaction.atomic
def matching_detail(*, actor, pk):
    actor = _actor(actor)
    row, file, customer, result = open_recommendation(actor=actor, recommendation_id=pk)
    return {
        'item_type': 'match_recommendation', 'id': str(row.pk), 'status': row.user_status,
        'is_source_valid': row.is_source_valid, 'is_currently_recommended': row.is_currently_recommended,
        'has_improved_score': row.has_improved_score,
        'created_at_display': timestamp_display(row.created_at),
        'updated_at_display': timestamp_display(row.updated_at),
        'last_evaluated_at_display': timestamp_display(row.last_evaluated_at),
        'property_file': FileCandidateSerializer(file).data,
        'customer': CustomerCandidateSerializer(customer).data,
        'matching': matching_result_data(result),
    }


def collaboration_detail(*, actor, pk, status=None):
    result = open_request(actor=actor, request_id=pk, status=status)
    for field in ('created_at', 'updated_at'):
        result[field + '_display'] = timestamp_display(result.pop(field))
    result.update(item_type='collaboration_request', direction='incoming' if result['recipient']['id'] == str(actor.pk) else 'sent')
    return result
