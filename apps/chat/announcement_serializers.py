from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.user_serializers import StrictInputSerializer
from apps.matching.collaboration_serializers import identity
from common.jalali import timestamp_display
from .models import MAX_TEXT_LENGTH


class AnnouncementInput(StrictInputSerializer):
    text = serializers.CharField(max_length=MAX_TEXT_LENGTH, label=_('متن اطلاعیه'))


class PublisherInput(StrictInputSerializer):
    user_id = serializers.UUIDField(label=_('ناشر'))


def announcement_data(row):
    # Retain history after a move without exposing identity changes from another Workspace.
    return {'id': str(row.pk), 'text': row.text,
            'author': identity(row.author) if row.author.workspace_id == row.workspace_id else None,
            'created_at_display': timestamp_display(row.created_at)}


def publisher_data(row):
    return {'id': str(row.pk),
            'user': identity(row.user) if row.user.workspace_id == row.workspace_id else None,
            'created_at_display': timestamp_display(row.created_at)}
