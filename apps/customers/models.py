import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router, transaction
from django.utils.translation import gettext_lazy as _


NONNEGATIVE = MinValueValidator(0, message=_("مقدار نمی‌تواند منفی باشد."))
UNSET = object()


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
    all_regions = models.BooleanField(default=False, verbose_name=_("همه مناطق فعال مجموعه"))
    preferred_regions = models.ManyToManyField("locations.Region", through="CustomerRegionPreference", related_name="interested_customers", blank=True, verbose_name=_("مناطق مورد نظر"))
    min_area = models.DecimalField(max_digits=12, decimal_places=2, validators=[NONNEGATIVE], verbose_name=_("حداقل مساحت (متر مربع)"))
    max_area = models.DecimalField(max_digits=12, decimal_places=2, validators=[NONNEGATIVE], verbose_name=_("حداکثر مساحت (متر مربع)"))
    min_building_age = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("حداقل سن بنا"))
    max_building_age = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("حداکثر سن بنا"))
    bedrooms = models.PositiveSmallIntegerField(validators=[NONNEGATIVE], verbose_name=_("تعداد اتاق خواب"))
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
            models.CheckConstraint(condition=models.Q(customer_type="buyer", budget__isnull=False, deposit_budget__isnull=True, monthly_rent_budget__isnull=True) | models.Q(customer_type="tenant", budget__isnull=True, budget_status__isnull=True, deposit_budget__isnull=False, monthly_rent_budget__isnull=False), name="customer_type_financials", violation_error_message=_("اطلاعات مالی با نوع مشتری سازگار نیست.")),
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
        for field in {"buyer": ("budget",), "tenant": ("deposit_budget", "monthly_rent_budget")}.get(self.customer_type, ()):
            if getattr(self, field) is None:
                errors[field] = _("این مقدار برای نوع مشتری انتخاب‌شده الزامی است.")
        ids = getattr(self, "_pending_region_ids", None)
        if ids is None:
            ids = set(self.region_preferences.values_list("region_id", flat=True)) if not self._state.adding else set()
        if (self.all_regions and ids) or (not self.all_regions and not ids):
            errors["preferred_regions"] = _("همه مناطق را انتخاب کنید یا حداقل یک منطقه مشخص کنید؛ این دو حالت هم‌زمان مجاز نیستند.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, preferred_regions=UNSET, **kwargs):
        """Persist the canonical aggregate, including geography, under the workspace lock."""
        from apps.organizations.models import Workspace
        from .validators import validate_region_set

        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = kwargs["update_fields"] = frozenset(update_fields)
            if not update_fields:
                return
        with transaction.atomic(using=using):
            original = type(self).objects.using(using).filter(pk=self.pk).values("workspace_id").first()
            workspace_id = original["workspace_id"] if original else self.workspace_id
            Workspace.objects.using(using).select_for_update().get(pk=workspace_id)
            stored = type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
            effective = self
            if stored and update_fields is not None:
                effective = stored
                for field in self._meta.concrete_fields:
                    if {field.name, field.attname} & update_fields:
                        setattr(effective, field.attname, getattr(self, field.attname))
            existing = set(self.region_preferences.using(using).values_list("region_id", flat=True)) if stored else set()
            supplied = preferred_regions is not UNSET
            ids = {getattr(region, "pk", region) for region in preferred_regions} if supplied else (set() if effective.all_regions else existing)
            effective._pending_region_ids = ids
            try:
                effective.full_clean()
                validate_region_set(effective.workspace_id, ids, existing, using)
                self.code = effective.code
                if update_fields is None or "budget_status" in update_fields:
                    self.budget_status = effective.budget_status
                result = super().save(*args, **kwargs)
                if effective.all_regions:
                    self.region_preferences.using(using).all().delete()
                elif supplied:
                    # Add first so replacing the last preference never removes geography.
                    self.preferred_regions.add(*ids)
                    self.region_preferences.using(using).exclude(region_id__in=ids).delete()
                return result
            finally:
                del effective._pending_region_ids

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


class PreferenceQuerySet(models.QuerySet):
    def delete(self):
        from apps.organizations.models import Workspace

        with transaction.atomic(using=self.db):
            rows = list(self.values_list("pk", "customer_id"))
            customer_ids = {customer_id for _, customer_id in rows}
            workspace_ids = Customer.objects.using(self.db).filter(pk__in=customer_ids).values_list("workspace_id", flat=True)
            list(Workspace.objects.using(self.db).select_for_update().filter(pk__in=workspace_ids).order_by("pk"))
            if set(self.model.objects.using(self.db).filter(pk__in=[pk for pk, _ in rows]).values_list("pk", "customer_id")) != set(rows):
                raise ValidationError({"preferred_regions": _("مناطق تغییر کرده‌اند؛ دوباره تلاش کنید.")})
            customers = Customer.objects.using(self.db).select_for_update().filter(pk__in=customer_ids).order_by("pk")
            ids = [pk for pk, _ in rows]
            for customer in customers:
                if not customer.all_regions and not self.model.objects.using(self.db).filter(customer=customer).exclude(pk__in=ids).exists():
                    raise ValidationError({"preferred_regions": _("حداقل یک منطقه مورد نظر باید باقی بماند.")})
            return super(PreferenceQuerySet, self.model.objects.using(self.db).filter(pk__in=ids)).delete()


class CustomerRegionPreference(models.Model):
    objects = PreferenceQuerySet.as_manager()
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
        from apps.organizations.models import Workspace

        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            stored = type(self).objects.using(using).filter(pk=self.pk).first()
            customer_ids = {self.customer_id} | ({stored.customer_id} if stored else set())
            workspace_ids = Customer.objects.using(using).filter(pk__in=customer_ids).values_list("workspace_id", flat=True)
            list(Workspace.objects.using(using).select_for_update().filter(pk__in=workspace_ids).order_by("pk"))
            list(Customer.objects.using(using).select_for_update().filter(pk__in=customer_ids).order_by("pk"))
            if stored and type(self).objects.using(using).filter(pk=self.pk).values_list("customer_id", flat=True).first() != stored.customer_id:
                raise ValidationError({"preferred_regions": _("مناطق تغییر کرده‌اند؛ دوباره تلاش کنید.")})
            effective = self
            if kwargs.get("update_fields") is not None and stored:
                kwargs["update_fields"] = frozenset(kwargs["update_fields"])
                if not kwargs["update_fields"]:
                    return
                effective = type(self).objects.using(using).get(pk=self.pk)
                for field in self._meta.concrete_fields:
                    if {field.name, field.attname} & kwargs["update_fields"]:
                        setattr(effective, field.attname, getattr(self, field.attname))
            effective.full_clean()
            if stored and stored.customer_id != effective.customer_id:
                if not type(self).objects.using(using).filter(customer_id=stored.customer_id).exclude(pk=self.pk).exists():
                    raise ValidationError({"preferred_regions": _("حداقل یک منطقه مورد نظر باید باقی بماند.")})
            return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        return type(self).objects.using(using or self._state.db).filter(pk=self.pk).delete()
