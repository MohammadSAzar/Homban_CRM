import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


WEIGHT_FIELDS = ("area", "bedrooms", "building_age", "region", "parking", "elevator", "storage", "balcony")
SETTING_FIELDS = WEIGHT_FIELDS + ("minimum_score", "sale_budget_lower_ratio", "sale_budget_upper_ratio", "rent_per_100m_deposit")
NONNEGATIVE = MinValueValidator(Decimal("0"), message=_("مقدار نمی‌تواند منفی باشد."))


class MatchingProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="matching_profile", verbose_name=_("کاربر"))
    area = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("25"), validators=[NONNEGATIVE], verbose_name=_("وزن مساحت"))
    bedrooms = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("15"), validators=[NONNEGATIVE], verbose_name=_("وزن اتاق خواب"))
    building_age = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("10"), validators=[NONNEGATIVE], verbose_name=_("وزن سن بنا"))
    region = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("10"), validators=[NONNEGATIVE], verbose_name=_("وزن منطقه"))
    parking = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("4"), validators=[NONNEGATIVE], verbose_name=_("وزن پارکینگ"))
    elevator = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("3"), validators=[NONNEGATIVE], verbose_name=_("وزن آسانسور"))
    storage = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("2"), validators=[NONNEGATIVE], verbose_name=_("وزن انباری"))
    balcony = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("1"), validators=[NONNEGATIVE], verbose_name=_("وزن بالکن"))
    minimum_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("50"), validators=[NONNEGATIVE, MaxValueValidator(100, message=_("امتیاز نباید بیشتر از ۱۰۰ باشد."))], verbose_name=_("حداقل امتیاز"))
    sale_budget_lower_ratio = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0.80"), validators=[MinValueValidator(Decimal("0.0001")), MaxValueValidator(1)], verbose_name=_("نسبت پایین بودجه خرید"))
    sale_budget_upper_ratio = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("1.20"), validators=[MinValueValidator(1)], verbose_name=_("نسبت بالای بودجه خرید"))
    rent_per_100m_deposit = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("3000000"), validators=[MinValueValidator(Decimal("0.01"))], verbose_name=_("اجاره ماهانه معادل صد میلیون تومان ودیعه"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("تاریخ ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("آخرین بروزرسانی"))

    class Meta:
        verbose_name = _("تنظیمات پایه تطبیق")
        verbose_name_plural = _("تنظیمات پایه تطبیق")
        constraints = [
            models.CheckConstraint(condition=models.Q(**{f"{name}__gte": 0}), name=f"matching_{name}_nonneg", violation_error_message=_("وزن نمی‌تواند منفی باشد."))
            for name in WEIGHT_FIELDS
        ] + [
            models.CheckConstraint(condition=models.Q(area__gt=0) | models.Q(bedrooms__gt=0) | models.Q(building_age__gt=0) | models.Q(region__gt=0) | models.Q(parking__gt=0) | models.Q(elevator__gt=0) | models.Q(storage__gt=0) | models.Q(balcony__gt=0), name="matching_positive_weight", violation_error_message=_("حداقل یک وزن باید بیشتر از صفر باشد.")),
            models.CheckConstraint(condition=models.Q(minimum_score__gte=0, minimum_score__lte=100), name="matching_score_range", violation_error_message=_("حداقل امتیاز باید بین صفر و صد باشد.")),
            models.CheckConstraint(condition=models.Q(sale_budget_lower_ratio__gt=0, sale_budget_lower_ratio__lte=1, sale_budget_upper_ratio__gte=1) & models.Q(sale_budget_lower_ratio__lte=models.F("sale_budget_upper_ratio")), name="matching_sale_ratios", violation_error_message=_("نسبت‌های بودجه معتبر نیستند.")),
            models.CheckConstraint(condition=models.Q(rent_per_100m_deposit__gt=0), name="matching_rent_positive", violation_error_message=_("معادل اجاره باید بیشتر از صفر باشد.")),
        ]

    def clean(self):
        super().clean()
        if self.user_id:
            user_model = self._meta.get_field("user").remote_field.model
            if not user_model.objects.filter(pk=self.user_id, workspace__isnull=False).exists():
                raise ValidationError({"user": _("کاربر باید عضو یک مجموعه باشد.")})
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values_list("user_id", flat=True).first()
            if original and original != self.user_id:
                raise ValidationError({"user": _("مالک تنظیمات قابل تغییر نیست.")})

    def save(self, *args, **kwargs):
        effective = self
        if kwargs.get("update_fields") is not None and not self._state.adding:
            kwargs["update_fields"] = frozenset(kwargs["update_fields"])
            if not kwargs["update_fields"]:
                return
            effective = type(self).objects.get(pk=self.pk)
            for field in self._meta.concrete_fields:
                if {field.name, field.attname} & kwargs["update_fields"]:
                    setattr(effective, field.attname, getattr(self, field.attname))
        effective.full_clean()
        return super().save(*args, **kwargs)
