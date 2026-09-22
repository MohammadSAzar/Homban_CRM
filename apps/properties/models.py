import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


NONNEGATIVE = MinValueValidator(0, message=_("مقدار نمی‌تواند منفی باشد."))


class PropertyFile(models.Model):
    class TransactionType(models.TextChoices):
        SALE = "sale", _("فروش")
        RENT = "rent", _("اجاره")

    class Status(models.TextChoices):
        ACTIVE = "active", _("فعال")
        INACTIVE = "inactive", _("غیرفعال")
        SOLD = "sold", _("فروخته‌شده")
        RENTED = "rented", _("اجاره‌داده‌شده")
        ARCHIVED = "archived", _("بایگانی‌شده")

    class Source(models.TextChoices):
        MANUAL = "manual", _("دستی")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.PROTECT, related_name="property_files", verbose_name=_("مجموعه"))
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="property_files", verbose_name=_("مشاور مسئول"))
    code = models.CharField(max_length=35, unique=True, editable=False, blank=True, verbose_name=_("کد فایل"))
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices, verbose_name=_("نوع معامله"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, verbose_name=_("وضعیت"))
    city = models.ForeignKey("locations.City", on_delete=models.PROTECT, related_name="property_files", verbose_name=_("شهر"))
    region = models.ForeignKey("locations.Region", on_delete=models.PROTECT, related_name="property_files", null=True, blank=True, verbose_name=_("منطقه"))
    address = models.TextField(blank=True, verbose_name=_("نشانی"))
    owner_name = models.CharField(max_length=150, blank=True, verbose_name=_("نام مالک"))
    owner_phone = models.CharField(max_length=20, blank=True, verbose_name=_("تلفن مالک"))
    visit_contact_phone = models.CharField(max_length=20, blank=True, verbose_name=_("تلفن هماهنگی بازدید"))
    area = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("مساحت (متر مربع)"))
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("تعداد اتاق خواب"))
    total_floors = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("تعداد طبقات"))
    units_per_floor = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("تعداد واحد در طبقه"))
    unit_floor = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("طبقه واحد"))
    building_age = models.PositiveSmallIntegerField(null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("سن بنا (سال)"))
    parking = models.BooleanField(null=True, blank=True, default=None, verbose_name=_("پارکینگ"))
    storage = models.BooleanField(null=True, blank=True, default=None, verbose_name=_("انباری"))
    elevator = models.BooleanField(null=True, blank=True, default=None, verbose_name=_("آسانسور"))
    balcony = models.BooleanField(null=True, blank=True, default=None, verbose_name=_("بالکن"))
    price_per_square_meter = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("قیمت هر متر مربع (تومان)"))
    total_price = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("قیمت کل (تومان)"))
    deposit_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("ودیعه (تومان)"))
    monthly_rent = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, validators=[NONNEGATIVE], verbose_name=_("اجاره ماهانه (تومان)"))
    description = models.TextField(blank=True, verbose_name=_("توضیحات"))
    source = models.CharField(max_length=30, choices=Source.choices, default=Source.MANUAL, verbose_name=_("منبع فایل"))
    is_valuable = models.BooleanField(default=False, verbose_name=_("ارزشمند"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("تاریخ ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("آخرین بروزرسانی"))

    class Meta:
        verbose_name = _("فایل ملکی")
        verbose_name_plural = _("فایل‌های ملکی")
        constraints = [
            models.CheckConstraint(condition=models.Q(**{f"{field}__gte": 0}) | models.Q(**{f"{field}__isnull": True}), name=f"pf_{field}_nonnegative", violation_error_message=_("مقدار نمی‌تواند منفی باشد."))
            for field in ("area", "bedrooms", "total_floors", "units_per_floor", "unit_floor", "building_age", "price_per_square_meter", "total_price", "deposit_amount", "monthly_rent")
        ] + [
            models.CheckConstraint(condition=(models.Q(transaction_type="sale", deposit_amount__isnull=True, monthly_rent__isnull=True) | models.Q(transaction_type="rent", price_per_square_meter__isnull=True, total_price__isnull=True)), name="pf_transaction_financials", violation_error_message=_("مبالغ باید با نوع معامله سازگار باشند.")),
            models.CheckConstraint(condition=models.Q(status__in=["active", "inactive", "sold", "rented", "archived"]), name="pf_valid_status", violation_error_message=_("وضعیت فایل معتبر نیست.")),
            models.CheckConstraint(condition=~models.Q(code=""), name="pf_code_not_empty", violation_error_message=_("کد فایل الزامی است.")),
        ]
        indexes = [
            models.Index(fields=["workspace", "status"], name="pf_workspace_status_idx"),
            models.Index(fields=["workspace", "transaction_type"], name="pf_workspace_type_idx"),
            models.Index(fields=["workspace", "region"], name="pf_workspace_region_idx"),
            models.Index(fields=["workspace", "assigned_to"], name="pf_workspace_assignee_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        expected_code = f"PF-{self.id.hex.upper()}" if isinstance(self.id, uuid.UUID) else None
        if expected_code:
            if self.code and self.code != expected_code:
                errors["code"] = _("کد فایل توسط سامانه تعیین می‌شود و قابل تغییر نیست.")
            else:
                self.code = expected_code
        for field in ("assigned_to", "city", "region"):
            related_id = getattr(self, f"{field}_id")
            if related_id:
                model = self._meta.get_field(field).remote_field.model
                related = model.objects.filter(pk=related_id).first()
                if related is None or related.workspace_id != self.workspace_id:
                    errors[field] = _("رکورد انتخاب‌شده متعلق به این مجموعه نیست.")
                elif field == "assigned_to" and related.role != "consultant":
                    errors[field] = _("فایل باید به یک مشاور اختصاص یابد.")
                elif field == "region" and related.city_id != self.city_id:
                    errors[field] = _("منطقه باید متعلق به شهر فایل باشد.")
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values("workspace_id").first()
            if original and original["workspace_id"] != self.workspace_id:
                errors["workspace"] = _("مجموعه فایل قابل تغییر نیست.")
        incompatible = {"sale": ("deposit_amount", "monthly_rent"), "rent": ("price_per_square_meter", "total_price")}
        for field in incompatible.get(self.transaction_type, ()):
            if getattr(self, field) is not None:
                errors[field] = _("این مبلغ برای نوع معامله انتخاب‌شده باید خالی باشد.")
        if errors:
            raise ValidationError(errors)

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

    def __str__(self):
        return self.code


class PropertyFileValuableReason(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    property_file = models.ForeignKey(PropertyFile, on_delete=models.CASCADE, related_name="valuable_reasons", verbose_name=_("فایل ملکی"))
    reason = models.CharField(max_length=150, verbose_name=_("دلیل ارزشمندی"))

    class Meta:
        verbose_name = _("دلیل ارزشمندی فایل")
        verbose_name_plural = _("دلایل ارزشمندی فایل")
        constraints = [models.UniqueConstraint(fields=["property_file", "reason"], name="pf_unique_valuable_reason")]

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PropertyFileImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    property_file = models.ForeignKey(PropertyFile, on_delete=models.CASCADE, related_name="images", verbose_name=_("فایل ملکی"))
    reference = models.CharField(max_length=2048, verbose_name=_("نشانی یا مرجع تصویر"))
    sort_order = models.PositiveIntegerField(default=0, validators=[NONNEGATIVE], verbose_name=_("ترتیب نمایش"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("تاریخ ایجاد"))

    class Meta:
        verbose_name = _("تصویر فایل")
        verbose_name_plural = _("تصاویر فایل")
        ordering = ["sort_order", "created_at", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(sort_order__gte=0), name="pf_image_order_nonnegative", violation_error_message=_("ترتیب تصویر نمی‌تواند منفی باشد."))]

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
