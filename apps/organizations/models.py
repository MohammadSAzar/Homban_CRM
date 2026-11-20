import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from apps.matching.generation_events import track_save
from .custom_admin_catalog import AdminPermission


class Workspace(models.Model):

    class WorkspaceMode(models.TextChoices):
        PERSONAL = "personal", _("شخصی")
        TEAM = "team", _("تیمی")

    class CustomerType(models.TextChoices):
        AGENCY_MANAGER = "agency_manager", _("مدیر املاک")
        RANGE_MANAGER = "range_manager", _("مدیر رنج")
        CONSULTANT = "consultant", _("مشاور")

    class RegionMode(models.TextChoices):
        CUSTOM = "custom", _("منطقه‌بندی اختصاصی")
        DIVAR = "divar", _("منطقه‌بندی دیوار")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("شناسه"),
    )

    name = models.CharField(
        max_length=150,
        verbose_name=_("نام مجموعه"),
    )

    slug = models.SlugField(
        max_length=100,
        unique=True,
        verbose_name=_("شناسه آدرس"),
        help_text=_("برای ساخت آدرس اختصاصی مجموعه استفاده می‌شود."),
    )

    # Purchaser/commercial metadata, independent of runtime product classification.
    customer_type = models.CharField(
        max_length=30,
        choices=CustomerType.choices,
        verbose_name=_("نوع خریدار"),
    )

    # NULL is unresolved legacy classification, never a third product mode.
    # New application provisioning must use organizations.services.create_workspace.
    workspace_mode = models.CharField(
        max_length=8,
        choices=WorkspaceMode.choices,
        null=True,
        blank=True,
        verbose_name=_("حالت محصول مجموعه"),
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name=_("فعال"),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("تاریخ ایجاد"),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("آخرین بروزرسانی"),
    )

    region_mode = models.CharField(
        max_length=20,
        choices=RegionMode.choices,
        default=None,
        null=True,
        blank=True,
        verbose_name=_("نوع منطقه‌بندی"),
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(workspace_mode__isnull=True) | models.Q(workspace_mode__in=["personal", "team"]),
                name="workspace_valid_product_mode",
                violation_error_message=_("حالت محصول مجموعه معتبر نیست."),
            ),
            models.CheckConstraint(
                condition=models.Q(region_mode__isnull=True) | models.Q(region_mode__in=["custom", "divar"]),
                name="workspace_valid_region_mode",
                violation_error_message=_("نوع منطقه‌بندی معتبر نیست."),
            ),
        ]
        verbose_name = _("مجموعه")
        verbose_name_plural = _("مجموعه‌ها")
        ordering = ["name"]

    @track_save("workspace", ("is_active",), creation=False, material=False)
    def save(self, *args, **kwargs):
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CustomAdminGrant(models.Model):
    """Current delegation state, not an audit history. Revoke by deletion."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, verbose_name=_("مجموعه"))
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="custom_admin_grants", verbose_name=_("کاربر"))
    permission = models.CharField(max_length=32, choices=AdminPermission.choices, verbose_name=_("مجوز"))
    granted_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="issued_custom_admin_grants", verbose_name=_("اعطاکننده"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان اعطا"))

    class Meta:
        verbose_name = _("مجوز مدیریت سفارشی")
        verbose_name_plural = _("مجوزهای مدیریت سفارشی")
        constraints = [
            models.UniqueConstraint(fields=("workspace", "user", "permission"), name="unique_custom_admin_grant"),
            models.CheckConstraint(condition=models.Q(permission__in=AdminPermission.values), name="valid_custom_admin_permission"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        for field in ("user", "granted_by"):
            if getattr(self, field + "_id") and getattr(self, field).workspace_id != self.workspace_id:
                raise ValidationError({field: _("کاربر باید متعلق به همین مجموعه باشد.")})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

