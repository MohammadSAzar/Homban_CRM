"""Current domain bridges; summary text never interpolates source/private data."""
from django.utils.translation import gettext_lazy as _
from .services import publish_notification


def recommendation_notification(row, event, *, watermark=None):
    if event is None:
        return
    # Generation supplies its durable work ID. The direct lifecycle uses its persisted
    # evaluation timestamp, not a timestamp invented during notification publication.
    suffix = 'created' if event == 'created' else f'reactivated:{watermark or row.last_evaluated_at.isoformat()}'
    return publish_notification(recipient=row.viewer, kind='match_recommendation',
        event_key=f'match-recommendation:{row.pk}:{suffix}',
        title=_('تطبیق جدید'), message=_('یک پیشنهاد تطبیق برای شما آماده است.'),
        action_url=f'/api/v1/daily-tasks/matching/{row.pk}/',
        source_type='match_recommendation', source_id=row.pk)


def collaboration_notification(event):
    row = event.request
    return publish_notification(recipient=row.recipient, kind='collaboration_request',
        event_key=f'collaboration:{row.pk}:created',
        title=_('درخواست همکاری جدید'), message=_('یک مشاور درخواست همکاری برای شما ارسال کرده است.'),
        action_url=f'/api/v1/daily-tasks/collaboration/{row.pk}/',
        source_type='collaboration_request', source_id=row.pk)
