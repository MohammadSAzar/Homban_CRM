"""Read-only preview and atomic exactly-once conversion; never merge existing data."""
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import StagedRecord
from .services import _record, _context, validate_staging_readiness
from .policies import review_actor, review_record
from .converters import ADAPTERS


def issues_from_error(error):
    if isinstance(error, ModelValidationError):
        detail = error.message_dict if hasattr(error, "message_dict") else error.messages
    else:
        detail = error.detail
    result = []
    def visit(value, field="non_field_errors"):
        if isinstance(value, dict):
            for key in sorted(value):
                visit(value[key], str(key) if field == "non_field_errors" else field + "." + str(key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, field)
        else:
            result.append({"field": field, "code": getattr(value, "code", None) or "invalid", "message": str(value)})
    visit(detail)
    return result


def _preview(row, actor):
    adapter = ADAPTERS.get(row.entity_type)
    data = None
    issues = []
    if adapter is None:
        issues = [{"field": "entity_type", "code": "invalid", "message": str(_("نوع رکورد معتبر نیست."))}]
    else:
        try:
            data = adapter.prepare(actor, row.normalized_data)
        except (ModelValidationError, ValidationError, PermissionDenied) as error:
            issues = issues_from_error(error)
    result = {"valid": not issues and not row.validation_issues, "entity_type": row.entity_type,
              "issues": issues, "staging_issues": row.validation_issues}
    return result, data


def validate_for_conversion(*, record_id, actor):
    current = review_actor(actor)
    row = review_record(current, record_id)
    if row.status not in (StagedRecord.Status.DRAFT, StagedRecord.Status.READY):
        raise ValidationError({"status": _("رکورد نهایی قابل اعتبارسنجی نیست.")})
    return _preview(row, current)[0]


@transaction.atomic
def convert_staged_record(*, record_id, actor):
    # Workspace -> current User -> staged row; all cooperating review writes use this order.
    if actor is None:
        raise PermissionDenied(_("تبدیل نیازمند کاربر فعال است."))
    workspace, current = _context(workspace_id=None, actor=actor)
    current.workspace = workspace
    try:
        row = _record(record_id=record_id, workspace_id=None, actor=current)
    except ModelValidationError as error:
        raise ValidationError({"issues": issues_from_error(error)}) from error
    if row.status != StagedRecord.Status.READY:
        raise ValidationError({"status": _("فقط پیش‌نویس آماده قابل تبدیل است.")})
    try:
        validate_staging_readiness(row)
    except ModelValidationError as error:
        raise ValidationError({"issues": issues_from_error(error)}) from error
    result, data = _preview(row, current)
    if not result["valid"]:
        raise ValidationError(result)
    try:
        # Savepoint ensures database/child errors can be safely translated after rollback.
        with transaction.atomic():
            target = ADAPTERS[row.entity_type].create(current, data)
            if row.entity_type == StagedRecord.EntityType.PROPERTY_FILE:
                row.converted_property_file = target
            else:
                row.converted_customer = target
            row.status = StagedRecord.Status.CONVERTED
            row.converted_by = current
            row.converted_at = timezone.now()
            row._conversion_write = True
            row.save()
    except (ModelValidationError, ValidationError) as error:
        raise ValidationError({"issues": issues_from_error(error)}) from error
    except IntegrityError as error:
        raise ValidationError({"issues": [{"field": "non_field_errors", "code": "canonical_conflict",
                              "message": str(_("ایجاد رکورد با محدودیت‌های اطلاعاتی سازگار نیست."))}]}) from error
    finally:
        row._conversion_write = False
    return row
