from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.user_serializers import StrictInputSerializer
from apps.matching.collaboration_serializers import identity
from common.jalali import timestamp_display
from .models import MAX_TEXT_LENGTH


class OpenSerializer(StrictInputSerializer):
    participant_id = serializers.UUIDField(label=_('طرف گفتگو'))


class SendSerializer(StrictInputSerializer):
    text = serializers.CharField(max_length=MAX_TEXT_LENGTH, required=False, allow_blank=True, label=_('متن پیام'))
    attachment = serializers.FileField(required=False, label=_('پیوست'))

    def to_internal_value(self, data):
        if hasattr(data, 'getlist') and any(len(data.getlist(key)) != 1 for key in data):
            raise serializers.ValidationError({'non_field_errors': [_('ارسال تکراری فیلد یا چند پیوست مجاز نیست.')]})
        return super().to_internal_value(data)


class EmptySerializer(StrictInputSerializer):
    pass


class PageSerializer(StrictInputSerializer):
    page = serializers.IntegerField(min_value=1, required=False, label=_('صفحه'))


def conversation_data(row, actor):
    other = row.participant_b if actor.pk == row.participant_a_id else row.participant_a
    return {'id': str(row.pk), 'other_participant': identity(other),
            'last_message_preview': row.last_preview, 'unread_count': row.unread_count,
            'last_message_at_display': timestamp_display(row.last_message_at),
            'created_at_display': timestamp_display(row.created_at),
            'updated_at_display': timestamp_display(row.updated_at)}


def message_data(row, actor):
    data = {'id': str(row.pk), 'text': row.text, 'is_own': row.sender_id == actor.pk,
            'created_at_display': timestamp_display(row.created_at)}
    if row.attachment:
        data['attachment'] = {
            'kind': 'image' if row.attachment_content_type.startswith('image/') else 'file',
            'filename': row.attachment_filename, 'content_type': row.attachment_content_type,
            'size': row.attachment_size,
            'download_url': f'/api/v1/chat/messages/{row.pk}/attachment/',
        }
    return data
