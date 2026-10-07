"""Explicit, atomic publication and recipient-only read state; no source loading on reads."""
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from apps.organizations.models import Workspace
from .models import Notification, _NOTIFICATION_WRITE


def lock_recipient(recipient, *, required=True):
    workspace = Workspace.objects.select_for_update().filter(pk=recipient.workspace_id, is_active=True).first()
    user = User.objects.select_for_update().filter(pk=recipient.pk, workspace=workspace).first() if workspace else None
    if user is not None:
        user.workspace = workspace
    if not customer_can_authenticate(user) or user.role not in User.Role.values:
        if required:
            raise PermissionDenied(_('دسترسی به اعلان‌ها مجاز نیست.'))
        return None
    return user


@transaction.atomic
def publish_notification(*, recipient, kind, event_key, title, message, action_url,
                         source_type='', source_id=None):
    """Trusted producer API. Inactive/moved recipients return (None, False), without fallback.

    Workspace -> User locks serialize duplicate publications and deactivation. The unique
    recipient/event key is the DB backstop. A failure rolls back the enclosing domain event.
    """
    recipient = lock_recipient(recipient, required=False)
    if recipient is None:
        return None, False
    row = Notification(workspace=recipient.workspace, recipient=recipient, kind=kind,
        event_key=event_key, title=title, message=message, action_url=action_url,
        source_type=source_type, source_id=source_id)
    row.full_clean(validate_unique=False, validate_constraints=False)
    existing = Notification.objects.filter(recipient=recipient, event_key=event_key).first()
    if existing is not None:
        return existing, False
    row.save(_token=_NOTIFICATION_WRITE)
    return row, True


def owned_notification(actor, pk):
    row = Notification.objects.for_recipient(actor).filter(pk=pk).first()
    if row is None:
        raise NotFound(_('اعلان یافت نشد.'))
    return row


@transaction.atomic
def set_read(*, actor, pk, read):
    actor = lock_recipient(actor)
    row = owned_notification(actor, pk)
    if row.is_read != read:
        row.read_at = timezone.now() if read else None
        row.save(_token=_NOTIFICATION_WRITE)
    return row


@transaction.atomic
def read_all(*, actor):
    actor = lock_recipient(actor)
    # Private bounded SQL write, no row loading; only read_at is mutable.
    # Actor is authoritatively locked in this Workspace; no self-table subquery (MySQL 1093).
    return models.QuerySet(model=Notification).filter(
        recipient_id=actor.pk, workspace_id=actor.workspace_id, read_at__isnull=True,
    ).update(read_at=timezone.now())
