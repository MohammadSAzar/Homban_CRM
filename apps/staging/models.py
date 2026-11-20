"""Incomplete source data, deliberately separate from canonical CRM records."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
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
        CONVERTED = "converted", _("تبدیل شده")

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

    converted_property_file = models.ForeignKey("properties.PropertyFile", null=True, blank=True, on_delete=models.PROTECT, related_name="staging_origins", verbose_name=_("فایل تبدیل‌شده"))
    converted_customer = models.ForeignKey("customers.Customer", null=True, blank=True, on_delete=models.PROTECT, related_name="staging_origins", verbose_name=_("متقاضی تبدیل‌شده"))
    converted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="staging_conversions", verbose_name=_("تبدیل‌کننده"))
    converted_at = models.DateTimeField(null=True, blank=True, verbose_name=_("زمان تبدیل"))

    class Meta:
        verbose_name = _("رکورد پیش‌نویس")
        verbose_name_plural = _("رکوردهای پیش‌نویس")
        indexes = [models.Index(fields=["workspace", "status"], name="staging_ws_status"),
                   models.Index(fields=["workspace", "entity_type"], name="staging_ws_entity")]
        constraints = [
            models.CheckConstraint(condition=models.Q(entity_type__in=["property_file", "customer"]), name="staging_valid_entity"),
            models.CheckConstraint(condition=models.Q(source_type__in=["crawler", "api", "import", "voice"]), name="staging_valid_source"),
            models.CheckConstraint(condition=models.Q(status__in=["draft", "ready", "rejected", "converted"]), name="staging_valid_status"),
            models.CheckConstraint(condition=(
                (~models.Q(status="converted") & models.Q(converted_property_file__isnull=True, converted_customer__isnull=True, converted_by__isnull=True, converted_at__isnull=True))
                | (models.Q(status="converted", converted_by__isnull=False, converted_at__isnull=False) & (
                    models.Q(entity_type="property_file", converted_property_file__isnull=False, converted_customer__isnull=True)
                    | models.Q(entity_type="customer", converted_customer__isnull=False, converted_property_file__isnull=True)))
            ), name="staging_conversion_consistent"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.created_by_id and self.created_by.workspace_id != self.workspace_id:
            errors["created_by"] = _("ایجادکننده باید عضو همین مجموعه باشد.")
        for field in ("converted_property_file", "converted_customer", "converted_by"):
            related_id = getattr(self, field + "_id")
            if related_id:
                model = self._meta.get_field(field).remote_field.model
                if not model.objects.filter(pk=related_id, workspace_id=self.workspace_id).exists():
                    errors[field] = _("رکورد انتخاب‌شده باید متعلق به همین مجموعه باشد.")
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
        with transaction.atomic():
            original = type(self).objects.select_for_update().filter(pk=self.pk).first()
            if original and original.status in (self.Status.REJECTED, self.Status.CONVERTED):
                raise ValidationError({"status": _("رکورد نهایی قابل تغییر نیست.")})
            if self.status == self.Status.CONVERTED and not getattr(self, "_conversion_write", False):
                raise ValidationError({"status": _("تبدیل فقط از مسیر اختصاصی مجاز است.")})
            if original:
                for field in ("workspace_id", "entity_type", "source_type", "raw_payload", "created_by_id"):
                    if getattr(original, field) != getattr(self, field):
                        raise ValidationError({field: _("اطلاعات منبع قابل تغییر نیست.")})
            self.full_clean()
            return super().save(*args, **kwargs)
