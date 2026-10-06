"""Strict feed inputs and small, separate allowlisted card representations."""
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from apps.accounts.user_serializers import StrictInputSerializer
from .collaboration_serializers import identity
from common.jalali import timestamp_display


class DailyFilterSerializer(StrictInputSerializer):
    type = serializers.ChoiceField(choices=('all', 'matching', 'collaboration', 'manual'), default='all', label=_("نوع"))
    status = serializers.ChoiceField(choices=('active', 'new', 'seen', 'rejected', 'done', 'accepted', 'expired', 'pending', 'cancelled', 'all'), default='active', label=_("وضعیت"))
    page = serializers.IntegerField(min_value=1, default=1, label=_("صفحه"))

    def validate(self, attrs):
        compatible = {
            'matching': {'active','new','seen','rejected','done','expired','all'},
            'collaboration': {'active','new','seen','rejected','accepted','expired','all'},
            'manual': {'active','pending','done','cancelled','all'},
        }
        if attrs['type'] != 'all' and attrs['status'] not in compatible[attrs['type']]:
            raise serializers.ValidationError(_("این وضعیت برای نوع انتخاب‌شده مجاز نیست."))
        return attrs


class RecommendationStatusSerializer(StrictInputSerializer):
    status = serializers.ChoiceField(choices=('seen', 'rejected', 'done'), label=_("وضعیت"))


def recommendation_card(row):
    source_valid = row.is_source_valid and row.property_file.status == row.customer.status == 'active'
    valid = row.is_viewer_valid and source_valid
    return {
        'item_type': 'match_recommendation', 'id': str(row.pk), 'status': row.user_status,
        'is_valid': valid, 'created_at_display': timestamp_display(row.created_at),
        'updated_at_display': timestamp_display(row.updated_at),
        'url': f'/api/v1/daily-tasks/matching/{row.pk}/',
        'payload': {
            'property_file_code': row.property_file.code, 'customer_code': row.customer.code,
            'last_evaluated_at_display': timestamp_display(row.last_evaluated_at),
            'current_score': str(row.current_score) if row.current_score is not None else None,
            'is_source_valid': source_valid,
            'is_currently_recommended': valid and row.is_currently_recommended,
            'has_improved_score': valid and row.has_improved_score,
        },
    }


def collaboration_card(row, actor):
    return {
        'item_type': 'collaboration_request', 'id': str(row.pk), 'status': row.manual_status,
        'direction': 'incoming' if row.recipient_id == actor.pk else 'sent',
        'is_valid': row.current_validity, 'created_at_display': timestamp_display(row.created_at),
        'updated_at_display': timestamp_display(row.updated_at),
        'url': f'/api/v1/daily-tasks/collaboration/{row.pk}/',
        'payload': {
            'requester': identity(row.requester), 'recipient': identity(row.recipient),
            'property_file_code': row.property_file.code if row.current_validity else None,
            'customer_code': row.customer.code if row.current_validity else None,
        },
    }
