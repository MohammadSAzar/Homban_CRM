"""Internal DS1 boundary, not a customer API or reviewer authorization policy.

Trusted callers provide explicit Workspace IDs. Actor mode derives Workspace from
current actor context. DS2 uses the same review scope for actor writes.
"""
from copy import deepcopy
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied
from apps.accounts.models import User
from apps.organizations.models import Workspace
from apps.organizations.capabilities import resolve_workspace_capability
from .models import StagedRecord


def _context(*, workspace_id, actor):
    if actor is not None:
        if not getattr(actor, "is_authenticated", False):
            raise PermissionDenied(_("دسترسی مجاز نیست."))
        if workspace_id is not None:
            raise PermissionDenied(_("مجموعه از کاربر تعیین می‌شود."))
        workspace_id = actor.workspace_id
    workspace = Workspace.objects.select_for_update().filter(pk=workspace_id, is_active=True).first()
    if workspace is None or not resolve_workspace_capability(workspace, "staging").allowed:
        raise PermissionDenied(_("دسترسی به پیش‌نویس فعال نیست."))
    current = None
    if actor is not None:
        current = User.objects.select_for_update().filter(pk=actor.pk, workspace=workspace, is_active=True).first()
        if current is None:
            raise PermissionDenied(_("دسترسی مجاز نیست."))
    return workspace, current


@transaction.atomic
def create_staged_record(*, entity_type, source_type, raw_payload, normalized_data=None,
                         validation_issues=None, actor=None, workspace_id=None, created_by_id=None):
    workspace, current = _context(workspace_id=workspace_id, actor=actor)
    if actor is not None and created_by_id is not None:
        raise PermissionDenied(_("ایجادکننده از کاربر تعیین می‌شود."))
    if created_by_id is not None:
        current = User.objects.select_for_update().filter(pk=created_by_id, workspace=workspace, is_active=True).first()
        if current is None:
            raise ValidationError({"created_by": _("ایجادکننده معتبر نیست.")})
    return StagedRecord.objects.create(workspace=workspace, created_by=current,
        entity_type=entity_type, source_type=source_type, raw_payload=deepcopy(raw_payload),
        normalized_data=deepcopy({} if normalized_data is None else normalized_data),
        validation_issues=deepcopy([] if validation_issues is None else validation_issues))


def _record(*, record_id, workspace_id, actor):
    workspace, current = _context(workspace_id=workspace_id, actor=actor)
    rows = StagedRecord.objects.select_for_update().filter(pk=record_id, workspace=workspace)
    if actor is not None:
        from .policies import review_scope
        current.workspace = workspace
        rows = review_scope(current, rows)
    row = rows.first()
    if row is None:
        raise NotFound(_("پیش‌نویس یافت نشد."))
    if row.status in (StagedRecord.Status.REJECTED, StagedRecord.Status.CONVERTED):
        raise ValidationError({"status": _("پیش‌نویس ردشده قابل تغییر نیست.")})
    return row


@transaction.atomic
def update_staged_record(*, record_id, data, actor=None, workspace_id=None):
    row = _record(record_id=record_id, workspace_id=workspace_id, actor=actor)
    if set(data) - {"normalized_data", "validation_issues"}:
        raise ValidationError(_("فقط داده آماده‌سازی‌شده و موارد بررسی قابل تغییر هستند."))
    for key, value in data.items():
        setattr(row, key, deepcopy(value))
    # Every edit returns a ready record to review; raw provenance is immutable here.
    row.status = StagedRecord.Status.DRAFT
    row.save()
    return row


def validate_staging_readiness(row):
    """Foundation checks only; DS2 must perform canonical conversion validation."""
    row.full_clean()
    if row.validation_issues:
        raise ValidationError({"validation_issues": _("موارد بررسی هنوز برطرف نشده‌اند.")})


@transaction.atomic
def mark_staged_record_ready(*, record_id, actor=None, workspace_id=None):
    row = _record(record_id=record_id, workspace_id=workspace_id, actor=actor)
    if row.status != StagedRecord.Status.DRAFT:
        raise ValidationError({"status": _("فقط پیش‌نویس قابل آماده‌سازی است.")})
    validate_staging_readiness(row)
    row.status = StagedRecord.Status.READY
    row.save()
    return row


@transaction.atomic
def reject_staged_record(*, record_id, actor=None, workspace_id=None):
    row = _record(record_id=record_id, workspace_id=workspace_id, actor=actor)
    row.status = StagedRecord.Status.REJECTED
    row.save()
    return row
