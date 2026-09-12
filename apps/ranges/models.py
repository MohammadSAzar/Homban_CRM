import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class Range(models.Model):
    class ActivityScope(models.TextChoices):
        ALL = "all", _("فروش و اجاره")
        SALE = "sale", _("فقط فروش")
        RENT = "rent", _("فقط اجاره")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("شناسه"),
    )

    workspace = models.ForeignKey(
        "organizations.Workspace",
        on_delete=models.CASCADE,
        related_name="ranges",
        verbose_name=_("مجموعه"),
    )

    name = models.CharField(
        max_length=100,
        verbose_name=_("نام رنج"),
    )

    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_ranges",
        verbose_name=_("مدیر رنج"),
    )

    activity_scope = models.CharField(
        max_length=20,
        choices=ActivityScope.choices,
        default=ActivityScope.ALL,
        verbose_name=_("حوزه فعالیت"),
    )

    regions = models.ManyToManyField(
        "locations.Region",
        blank=True,
        related_name="ranges",
        verbose_name=_("مناطق مجاز"),
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
        verbose_name = _("رنج")
        verbose_name_plural = _("رنج‌ها")
        ordering = ["name"]

        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="unique_range_name_per_workspace",
            ),
        ]

        indexes = [
            models.Index(
                fields=["workspace", "is_active"],
                name="range_workspace_active_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.manager_id and self.workspace_id:
            if self.manager.workspace_id != self.workspace_id:
                raise ValidationError(
                    {
                        "manager": _(
                            "مدیر رنج باید متعلق به همین مجموعه باشد."
                        )
                    }
                )

            if self.manager.role != "range_manager":
                raise ValidationError(
                    {
                        "manager": _(
                            "کاربر انتخاب‌شده باید نقش مدیر رنج داشته باشد."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class RangeMembership(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("شناسه"),
    )

    workspace = models.ForeignKey(
        "organizations.Workspace",
        on_delete=models.CASCADE,
        related_name="range_memberships",
        verbose_name=_("مجموعه"),
    )

    range = models.ForeignKey(
        Range,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name=_("رنج"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="range_membership",
        verbose_name=_("مشاور"),
    )

    joined_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("تاریخ عضویت"),
    )

    class Meta:
        verbose_name = _("عضویت در رنج")
        verbose_name_plural = _("عضویت‌های رنج")

        indexes = [
            models.Index(
                fields=["workspace", "range"],
                name="membership_workspace_range_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.user_id:
            if self.user.role != "consultant":
                raise ValidationError(
                    {
                        "user": _(
                            "فقط کاربران با نقش مشاور می‌توانند عضو رنج باشند."
                        )
                    }
                )

        if self.workspace_id and self.range_id:
            if self.range.workspace_id != self.workspace_id:
                raise ValidationError(
                    {
                        "range": _(
                            "رنج انتخاب‌شده متعلق به این مجموعه نیست."
                        )
                    }
                )

        if self.workspace_id and self.user_id:
            if self.user.workspace_id != self.workspace_id:
                raise ValidationError(
                    {
                        "user": _(
                            "مشاور انتخاب‌شده متعلق به این مجموعه نیست."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.range}"

