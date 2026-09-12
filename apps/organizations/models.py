import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class Workspace(models.Model):
    class CustomerType(models.TextChoices):
        AGENCY_MANAGER = "agency_manager", _("مدیر املاک")
        RANGE_MANAGER = "range_manager", _("مدیر رنج")
        CONSULTANT = "consultant", _("مشاور")

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

    customer_type = models.CharField(
        max_length=30,
        choices=CustomerType.choices,
        verbose_name=_("نوع خریدار"),
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

    class Meta:
        verbose_name = _("مجموعه")
        verbose_name_plural = _("مجموعه‌ها")
        ordering = ["name"]

    def __str__(self):
        return self.name

