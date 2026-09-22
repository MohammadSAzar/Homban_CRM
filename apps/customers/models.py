import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


NONNEGATIVE = MinValueValidator(0, message=_("مقدار نمی‌تواند منفی باشد."))


class Customer(models.Model):
    class CustomerType(models.TextChoices):
        BUYER = "buyer", _("خریدار")
        TENANT = "tenant", _("مستأجر")

    class Status(models.TextChoices):
        ACTIVE = "active", _("فعال")
        INACTIVE = "inactive", _("غیرفعال")
        COMPLETED = "completed", _("تکمیل‌شده")
        ARCHIVED = "archived", _("بایگانی‌شده")

    class BudgetStatus(models.TextChoices):
        CASH = "cash", _("کاملاً نقد")
        CASH_PLUS_PROPERTY = "cash_plus_property", _("بخشی نقد + آپارتمان")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.PROTECT, related_name="customers", verbose_name=_("مجموعه"))
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="customers", verbose_name=_("مشاور مسئول"))
    code = models.CharField(max_length=35, unique=True, editable=False, blank=True, verbose_name=_("کد مشتری"))
    customer_type = models.CharField(max_length=10, choices=CustomerType.choices, verbose_name=_("نوع مشتری"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, verbose_name=_("وضعیت"))
    name = models.CharField(max_length=150, verbose_name=_("نام مشتری"))
    mobile = models.CharField(max_length=20, blank=True, verbose_name=_("شماره موبایل"))
    description = models.TextField(blank=True, verbose_name=_("توضیحات"))
    is_valuable = models.BooleanField(default=False, verbose_name=_("ارزشمند"))
    preferred_regions = models.ManyToManyField("locations.Region", through="CustomerRegionPreference", related_name="interested_customers", blank=True, verbose_name=_("مناطق مورد نظر"))
    min_area = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("حداقل مساحت (متر مربع)"))
    max_area = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("حداکثر مساحت (متر مربع)"))
    min_building_age = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("حداقل سن بنا"))
    max_building_age = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("حداکثر سن بنا"))
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("تعداد اتاق خواب"))
    budget = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("بودجه خرید (تومان)"))
    budget_status = models.CharField(max_length=20, choices=BudgetStatus.choices, null=True, blank=True, default=None, verbose_name=_("وضعیت بودجه"))
    deposit_budget = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("بودجه ودیعه (تومان)"))
    monthly_rent_budget = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("بودجه اجاره ماهانه (تومان)"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("تاریخ ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("آخرین بروزرسانی"))

    class Meta:
        verbose_name = _("مشتری")
        verbose_name_plural = _("مشتریان")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(**{f"{field}__gte": 0}) | models.Q(**{f"{field}__isnull": True}),
                name=f"customer_{field}_nonneg", violation_error_message=_("مقدار نمی‌تواند منفی باشد."),
            ) for field in ("min_area", "max_area", "min_building_age", "max_building_age", "bedrooms", "budget", "deposit_budget", "monthly_rent_budget")
        ] + [
            models.CheckConstraint(condition=models.Q(min_area__isnull=True) | models.Q(max_area__isnull=True) | models.Q(min_area__lte=models.F("max_area")), name="customer_area_bounds", violation_error_message=_("حداقل مساحت نباید بیشتر از حداکثر باشد.")),
            models.CheckConstraint(condition=models.Q(min_building_age__isnull=True) | models.Q(max_building_age__isnull=True) | models.Q(min_building_age__lte=models.F("max_building_age")), name="customer_age_bounds", violation_error_message=_("حداقل سن بنا نباید بیشتر از حداکثر باشد.")),
            models.CheckConstraint(condition=models.Q(customer_type="buyer", deposit_budget__isnull=True, monthly_rent_budget__isnull=True) | models.Q(customer_type="tenant", budget__isnull=True, budget_status__isnull=True), name="customer_type_financials", violation_error_message=_("اطلاعات مالی با نوع مشتری سازگار نیست.")),
            models.CheckConstraint(condition=models.Q(budget_status__isnull=True) | models.Q(budget_status__in=["cash", "cash_plus_property"]), name="customer_budget_status", violation_error_message=_("وضعیت بودجه معتبر نیست.")),
            models.CheckConstraint(condition=models.Q(status__in=["active", "inactive", "completed", "archived"]), name="customer_valid_status", violation_error_message=_("وضعیت مشتری معتبر نیست.")),
            models.CheckConstraint(condition=~models.Q(code=""), name="customer_code_not_empty", violation_error_message=_("کد مشتری الزامی است.")),
        ]
        indexes = [
            models.Index(fields=["workspace", "status"], name="customer_ws_status_idx"),
            models.Index(fields=["workspace", "customer_type"], name="customer_ws_type_idx"),
            models.Index(fields=["workspace", "assigned_to"], name="customer_ws_assignee_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if isinstance(self.id, uuid.UUID):
            expected = f"CU-{self.id.hex.upper()}"
            if self.code and self.code != expected:
                errors["code"] = _("کد مشتری توسط سامانه تعیین می‌شود و قابل تغییر نیست.")
            else:
                self.code = expected
        if self.budget_status == "":
            self.budget_status = None
        if self.assigned_to_id:
            user = self._meta.get_field("assigned_to").remote_field.model.objects.filter(pk=self.assigned_to_id).first()
            if user is None or user.workspace_id != self.workspace_id:
                errors["assigned_to"] = _("مشاور باید متعلق به همین مجموعه باشد.")
            elif user.role != "consultant":
                errors["assigned_to"] = _("مشتری باید به یک مشاور اختصاص یابد.")
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values("workspace_id").first()
            if original and original["workspace_id"] != self.workspace_id:
                errors["workspace"] = _("مجموعه مشتری قابل تغییر نیست.")
        for field in {"buyer": ("deposit_budget", "monthly_rent_budget"), "tenant": ("budget", "budget_status")}.get(self.customer_type, ()):
            if getattr(self, field) is not None:
                errors[field] = _("این مقدار برای نوع مشتری انتخاب‌شده باید خالی باشد.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class CustomerValuableReason(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="valuable_reasons", verbose_name=_("مشتری"))
    reason = models.CharField(max_length=150, verbose_name=_("دلیل ارزشمندی"))

    class Meta:
        verbose_name = _("دلیل ارزشمندی مشتری")
        verbose_name_plural = _("دلایل ارزشمندی مشتری")
        constraints = [models.UniqueConstraint(fields=["customer", "reason"], name="customer_unique_reason")]

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class CustomerRegionPreference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="region_preferences", verbose_name=_("مشتری"))
    region = models.ForeignKey("locations.Region", on_delete=models.PROTECT, related_name="customer_preferences", verbose_name=_("منطقه"))

    class Meta:
        verbose_name = _("منطقه مورد نظر مشتری")
        verbose_name_plural = _("مناطق مورد نظر مشتری")
        constraints = [models.UniqueConstraint(fields=["customer", "region"], name="customer_unique_region")]

    def clean(self):
        super().clean()
        from .validators import validate_preferences
        if self.customer_id and self.region_id:
            validate_preferences(self.customer_id, [self.region_id])

    def save(self, *args, **kwargs):
        self.full_clean()
        if kwargs.get("update_fields") is not None and not self._state.adding:
            kwargs["update_fields"] = frozenset(kwargs["update_fields"])
            if kwargs["update_fields"]:
                # Validate the actual partial write, not excluded in-memory changes.
                stored = type(self).objects.using(kwargs.get("using") or self._state.db).get(pk=self.pk)
                for field in self._meta.concrete_fields:
                    if {field.name, field.attname} & kwargs["update_fields"]:
                        setattr(stored, field.attname, getattr(self, field.attname))
                stored.full_clean()
        return super().save(*args, **kwargs)
