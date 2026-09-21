import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError


class City(models.Model):

    class Source(models.TextChoices):
        MANUAL = "manual", _("دستی")
        DIVAR = "divar", _("دیوار")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("شناسه"),
    )

    workspace = models.ForeignKey(
        "organizations.Workspace",
        on_delete=models.CASCADE,
        related_name="cities",
        verbose_name=_("مجموعه"),
    )

    name = models.CharField(
        max_length=100,
        verbose_name=_("نام شهر"),
    )

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        verbose_name=_("منبع"),
    )

    external_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("شناسه خارجی"),
    )

    external_slug = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("شناسه متنی خارجی"),
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
        verbose_name = _("شهر")
        verbose_name_plural = _("شهرها")
        ordering = ["name"]

        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                name="unique_city_name_per_workspace",
            ),
        ]

        indexes = [
            models.Index(
                fields=["workspace", "is_active"],
                name="city_workspace_active_idx",
            ),
        ]

    def __str__(self):
        return self.name


class Region(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", _("دستی")
        DIVAR = "divar", _("دیوار")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("شناسه"),
    )

    workspace = models.ForeignKey(
        "organizations.Workspace",
        on_delete=models.CASCADE,
        related_name="regions",
        verbose_name=_("مجموعه"),
    )

    city = models.ForeignKey(
        City,
        on_delete=models.CASCADE,
        related_name="regions",
        verbose_name=_("شهر"),
    )

    name = models.CharField(
        max_length=100,
        verbose_name=_("نام منطقه"),
    )

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        verbose_name=_("منبع"),
    )

    external_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("شناسه خارجی"),
    )

    external_slug = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("شناسه متنی خارجی"),
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
        verbose_name = _("منطقه")
        verbose_name_plural = _("مناطق")
        ordering = ["city__name", "name"]

        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "city", "name"],
                name="unique_region_per_city_workspace",
            ),
        ]

        indexes = [
            models.Index(
                fields=["workspace", "city", "is_active"],
                name="region_workspace_city_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.pk and self.property_files.exclude(city_id=self.city_id).exists():
            raise ValidationError({"city": _("شهر منطقه با فایل‌های ملکی مرتبط سازگار نیست.")})

        if self.city_id and self.workspace_id:
            if self.city.workspace_id != self.workspace_id:
                raise ValidationError(
                    {
                        "city": _(
                            "شهر انتخاب‌شده متعلق به این مجموعه نیست."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.city.name} - {self.name}"

