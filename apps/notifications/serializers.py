from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from apps.accounts.user_serializers import StrictInputSerializer
from common.jalali import format_datetime, timestamp_display
from .models import Notification


class EmptySerializer(StrictInputSerializer):
    pass


class NotificationFilterSerializer(StrictInputSerializer):
    status = serializers.ChoiceField(choices=('all','unread','read'), default='unread', label=_('وضعیت'))
    kind = serializers.ChoiceField(choices=Notification.Kind.choices, required=False, label=_('نوع'))
    page = serializers.IntegerField(min_value=1, default=1, label=_('صفحه'))


def notification_data(row):
    day = format_datetime(row.created_at)
    return {'id': str(row.pk), 'kind': row.kind, 'title': row.title, 'message': row.message,
            'action_url': row.action_url, 'is_read': row.is_read,
            'created_at_display': timestamp_display(row.created_at),
            'created_date': day['jalali_date'], 'created_time': day['time'],
            'read_at_display': timestamp_display(row.read_at)}
