from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.user_serializers import StrictInputSerializer
from apps.matching.collaboration_serializers import identity
from common.jalali import timestamp_display
from .models import MAX_TEXT_LENGTH


class GeneralGroupInput(StrictInputSerializer):
    text = serializers.CharField(max_length=MAX_TEXT_LENGTH, label=_('متن پیام'))


class EnabledField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail('invalid', input=data)
        return data


class GeneralGroupSettingsInput(StrictInputSerializer):
    is_enabled = EnabledField(label=_('فعال'))


def general_group_message_data(row):
    return {'id': str(row.pk), 'text': row.text,
            'sender': identity(row.sender) if row.sender.workspace_id == row.workspace_id else None,
            'created_at_display': timestamp_display(row.created_at)}
