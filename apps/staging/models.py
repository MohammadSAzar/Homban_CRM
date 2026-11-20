"""Incomplete source data, deliberately separate from canonical CRM records."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class StagedRecord(models.Model):
    class EntityType(models.TextChoices):
        PROPERTY_FILE = "property_file", _("فایل ملک")
        CUSTOMER = "customer", _("متقاضی")

    class SourceType(models.TextChoices):
        CRAWLER = "crawler", _("خزنده")
        API = "api", _("سرویس خارجی")
        IMPORT = "import", _("ورود اطلاعات")
        VOICE = "voice", _("صدا")

    class Status(models.TextChoices):
        DRAFT = "draft", _("پیش‌نویس")
        READY = "ready", _("آماده")
        REJECTED = "rejected", _("رد شده")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.CASCADE, related_name="staged_records", verbose_name=_("مجموعه"))
    entity_type = models.CharField(max_length=20, choices=EntityType.choices, verbose_name=_("نوع رکورد"))
    source_type = models.CharField(max_length=12, choices=SourceType.choices, verbose_name=_("نوع منبع"))
    raw_payload = models.JSONField(default=dict, blank=True, verbose_name=_("داده خام"))
    normalized_data = models.JSONField(default=dict, blank=True, verbose_name=_("داده آماده‌سازی‌شده"))
    validation_issues = models.JSONField(default=list, blank=True, verbose_name=_("موارد نیازمند بررسی"))
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, verbose_name=_("وضعیت"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="staged_records", verbose_name=_("ایجادکننده"))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_("زمان ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("زمان ویرایش"))

    class Meta:
        verbose_name = _("رکورد پیش‌نویس")
        verbose_name_plural = _("رکوردهای پیش‌نویس")
        indexes = [models.Index(fields=["workspace", "status"], name="staging_ws_status"),
                   models.Index(fields=["workspace", "entity_type"], name="staging_ws_entity")]
        constraints = [
            models.CheckConstraint(condition=models.Q(entity_type__in=["property_file", "customer"]), name="staging_valid_entity"),
            models.CheckConstraint(condition=models.Q(source_type__in=["crawler", "api", "import", "voice"]), name="staging_valid_source"),
            models.CheckConstraint(condition=models.Q(status__in=["draft", "ready", "rejected"]), name="staging_valid_status"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.created_by_id and self.created_by.workspace_id != self.workspace_id:
            errors["created_by"] = _("ایجادکننده باید عضو همین مجموعه باشد.")
        if not isinstance(self.normalized_data, dict):
            errors["normalized_data"] = _("داده آماده‌سازی‌شده باید یک شیء باشد.")
        if not isinstance(self.validation_issues, list) or any(
            not isinstance(issue, dict) or set(issue) != {"field", "code", "message"}
            or any(not isinstance(issue[key], str) or not issue[key].strip() for key in issue)
            for issue in self.validation_issues if isinstance(self.validation_issues, list)
        ):
            errors["validation_issues"] = _("موارد بررسی باید شامل فیلد، کد و پیام باشند.")
        if self.status == self.Status.READY and self.validation_issues:
            errors["status"] = _("موارد بررسی باید پیش از آماده شدن برطرف شوند.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
