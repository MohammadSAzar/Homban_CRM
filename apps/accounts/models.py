import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    # Django/internal authentication needs an unambiguous identifier.
    USERNAME_FIELD = "id"
    REQUIRED_FIELDS = ["username", "email"]

    username = models.CharField(
        max_length=150,
        validators=[AbstractUser.username_validator],
        verbose_name=_("نام کاربری"),
        help_text=_("نام کاربری باید در مجموعه یکتا باشد."),
    )

    class Role(models.TextChoices):
        AGENCY_MANAGER = "agency_manager", _("مدیر املاک")
        RANGE_MANAGER = "range_manager", _("مدیر رنج")
        CONSULTANT = "consultant", _("مشاور")
        SECRETARY = "secretary", _("منشی")
        ADMIN = "admin", _("ادمین")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("شناسه"),
    )

    workspace = models.ForeignKey(
        "organizations.Workspace",
        on_delete=models.CASCADE,
        related_name="users",
        verbose_name=_("مجموعه"),
        null=True,
        blank=True,
    )

    role = models.CharField(
        max_length=30,
        choices=Role.choices,
        verbose_name=_("نقش"),
    )

    is_workspace_owner = models.BooleanField(
        default=False,
        verbose_name=_("یوزر اصلی مجموعه"),
        help_text=_("مشخص می‌کند این کاربر خریدار و مالک اصلی فضای کاری است."),
    )

    phone_number = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("شماره موبایل"),
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
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "username"],
                name="unique_username_per_workspace",
            ),
        ]
        verbose_name = _("کاربر")
        verbose_name_plural = _("کاربران")

    def __str__(self):
        return self.get_full_name() or self.username

