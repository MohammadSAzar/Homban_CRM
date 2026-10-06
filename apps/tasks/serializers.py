from datetime import timedelta
from django.core.exceptions import ValidationError as DomainError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from apps.accounts.user_serializers import StrictInputSerializer
from apps.matching.live_serializers import StrictBooleanField
from common import jalali
from .models import ManualTask
from .services import is_overdue


class JalaliDateField(serializers.CharField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            jalali.parse_date(value)
        except DomainError as exc:
            raise serializers.ValidationError(exc.messages) from None
        return value.translate(jalali.TO_ASCII)


class ClockField(serializers.CharField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            jalali.parse_time(value)
        except DomainError as exc:
            raise serializers.ValidationError(exc.messages) from None
        return value.translate(jalali.TO_ASCII)


class TaskWriteSerializer(StrictInputSerializer):
    title = serializers.CharField(max_length=200, label=_('عنوان'))
    jalali_date = JalaliDateField(label=_('تاریخ شمسی'))
    time = ClockField(required=False, label=_('ساعت'))
    is_all_day = StrictBooleanField(required=False, label=_('تمام‌روز'))


class TaskStatusSerializer(StrictInputSerializer):
    status = serializers.ChoiceField(choices=ManualTask.Status.choices, label=_('وضعیت'))


class TaskFilterSerializer(StrictInputSerializer):
    status = serializers.ChoiceField(choices=('pending','done','cancelled','all'), default='pending', label=_('وضعیت'))
    page = serializers.IntegerField(min_value=1, default=1, label=_('صفحه'))

    def get_fields(self):
        fields = super().get_fields()
        required = self.context.get('calendar', False)
        fields['from'] = JalaliDateField(required=required, label=_('از تاریخ شمسی'))
        fields['to'] = JalaliDateField(required=required, label=_('تا تاریخ شمسی'))
        if required:
            fields['status'].default = 'all'
        return fields

    def validate(self, attrs):
        try:
            if 'from' in attrs and 'to' in attrs:
                attrs['start'], attrs['end'] = jalali.range_bounds(attrs['from'],attrs['to'], max_days=366 if self.context.get('calendar') else None)
            elif 'from' in attrs:
                attrs['start'] = jalali.day_start(jalali.parse_date(attrs['from']).togregorian())
            elif 'to' in attrs:
                attrs['end'] = jalali.day_start(jalali.parse_date(attrs['to']).togregorian()+timedelta(days=1))
        except DomainError as exc:
            raise serializers.ValidationError(exc.messages) from None
        except OverflowError:
            raise serializers.ValidationError(_('بازه تاریخ شمسی خارج از محدوده قابل پشتیبانی است.')) from None
        return attrs


def task_data(row, *, now=None):
    return {'id':str(row.pk), 'title':row.title, 'status':row.status,
            **jalali.format_datetime(row.scheduled_for, all_day=row.is_all_day),
            'is_all_day':row.is_all_day, 'is_overdue':is_overdue(row, now or timezone.now()),
            'created_at_display':jalali.timestamp_display(row.created_at),
            'updated_at_display':jalali.timestamp_display(row.updated_at),
            'completed_at_display':jalali.timestamp_display(row.completed_at)}


def task_card(row, *, now):
    return {'item_type':'manual_task', **task_data(row, now=now),
            'url':f'/api/v1/daily-tasks/manual/{row.pk}/'}
