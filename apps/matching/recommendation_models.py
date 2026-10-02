"""Saved recommendation projection; all normal writes go through lifecycle services."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

_LIFECYCLE_WRITE = object()


def lifecycle_only(*args, **kwargs):
    raise ValidationError(_("تغییر پیشنهاد فقط از طریق سرویس چرخه عمر مجاز است."))


class RecommendationQuerySet(models.QuerySet):
    update = bulk_update = bulk_create = delete = lifecycle_only

    def for_viewer(self, viewer):
        # Recheck live ownership even before the next reconciliation has run.
        return self.filter(
            viewer_id=viewer.pk, viewer__role="consultant", viewer__is_active=True,
            viewer__workspace__is_active=True,
            property_file__workspace_id=F("viewer__workspace_id"),
            customer__workspace_id=F("viewer__workspace_id"),
        ).filter(Q(property_file__assigned_to_id=viewer.pk) | Q(customer__assigned_to_id=viewer.pk))

    def active_for_viewer(self, viewer):
        return self.for_viewer(viewer).filter(
            is_viewer_valid=True, is_source_valid=True, is_currently_recommended=True,
            property_file__status="active", customer__status="active",
        )


class MatchRecommendation(models.Model):
    class Status(models.TextChoices):
        NEW = "new", _("جدید")
        SEEN = "seen", _("دیده‌شده")
        DONE = "done", _("انجام‌شده")
        REJECTED = "rejected", _("ردشده")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    viewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="match_recommendations", verbose_name=_("مشاور دریافت‌کننده"))
    property_file = models.ForeignKey("properties.PropertyFile", on_delete=models.PROTECT, related_name="match_recommendations", verbose_name=_("فایل"))
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="match_recommendations", verbose_name=_("مشتری"))
    user_status = models.CharField(max_length=8, choices=Status.choices, default=Status.NEW, verbose_name=_("وضعیت دستی"))
    current_score = models.DecimalField(max_digits=33, decimal_places=30, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name=_("امتیاز فعلی"))
    score_at_last_view = models.DecimalField(max_digits=33, decimal_places=30, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name=_("امتیاز آخرین مشاهده"))
    minimum_score = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(100)], verbose_name=_("آستانه آخرین ارزیابی"))
    formula_version = models.CharField(max_length=40, verbose_name=_("نسخه فرمول"))
    is_viewer_valid = models.BooleanField(default=True, verbose_name=_("اعتبار ارتباط مشاور"))
    is_source_valid = models.BooleanField(default=True, verbose_name=_("اعتبار منابع"))
    is_currently_recommended = models.BooleanField(default=True, verbose_name=_("پیشنهاد فعلی"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("زمان تغییر"))
    last_evaluated_at = models.DateTimeField(verbose_name=_("آخرین ارزیابی"))

    objects = RecommendationQuerySet.as_manager()

    class Meta:
        verbose_name = _("پیشنهاد تطبیق")
        verbose_name_plural = _("پیشنهادهای تطبیق")
        constraints = [
            models.UniqueConstraint(fields=["viewer", "property_file", "customer"], name="unique_viewer_match_pair"),
            models.CheckConstraint(condition=Q(user_status__in=["new", "seen", "done", "rejected"]), name="recommendation_status_valid"),
            models.CheckConstraint(condition=Q(current_score__isnull=True) | Q(current_score__gte=0, current_score__lte=100), name="recommendation_score_range"),
            models.CheckConstraint(condition=Q(score_at_last_view__isnull=True) | Q(score_at_last_view__gte=0, score_at_last_view__lte=100), name="recommendation_view_score_range"),
            models.CheckConstraint(condition=Q(minimum_score__gte=0, minimum_score__lte=100), name="recommendation_threshold_range"),
            models.CheckConstraint(condition=Q(is_currently_recommended=False) | Q(is_source_valid=True, is_viewer_valid=True, current_score__isnull=False), name="recommendation_valid_flags"),
        ]
        indexes = [models.Index(fields=["viewer", "user_status"], name="recommendation_view_status_idx")]

    def clean(self):
        super().clean()
        stored = type(self).objects.filter(pk=self.pk).first() if not self._state.adding else None
        if stored and any(getattr(self, name) != getattr(stored, name) for name in ("viewer_id", "property_file_id", "customer_id")):
            raise ValidationError(_("مشاور و منابع پیشنهاد قابل تغییر نیستند."))
        if not all((self.viewer_id, self.property_file_id, self.customer_id)):
            return  # Field/FK validation reports missing references.
        viewer = self._meta.get_field("viewer").remote_field.model.objects.filter(pk=self.viewer_id).first()
        file = self._meta.get_field("property_file").remote_field.model.objects.filter(pk=self.property_file_id).first()
        customer = self._meta.get_field("customer").remote_field.model.objects.filter(pk=self.customer_id).first()
        if viewer is None or file is None or customer is None:
            raise ValidationError(_("منابع پیشنهاد معتبر نیستند."))
        valid = bool(viewer.workspace_id and viewer.workspace_id == file.workspace_id == customer.workspace_id
                     and viewer.role == "consultant" and viewer.pk in (file.assigned_to_id, customer.assigned_to_id))
        if not valid and (stored is None or self.is_viewer_valid or self.is_currently_recommended):
            raise ValidationError(_("مشاور باید مالک حداقل یکی از منابع همین مجموعه باشد."))
        if self.user_status == self.Status.DONE and (stored is None or stored.user_status != self.Status.DONE):
            if not valid or file.assigned_to_id != viewer.pk or customer.assigned_to_id != viewer.pk:
                raise ValidationError(_("انجام‌شده فقط برای فایل و مشتری متعلق به خود مشاور مجاز است."))

    def save(self, *args, _token=None, **kwargs):
        if _token is not _LIFECYCLE_WRITE:
            lifecycle_only()
        with transaction.atomic():
            effective = self
            if kwargs.get("update_fields") is not None and not self._state.adding:
                kwargs["update_fields"] = frozenset(kwargs["update_fields"])
                if not kwargs["update_fields"]:
                    return
                effective = type(self).objects.select_for_update().get(pk=self.pk)
                for field in self._meta.concrete_fields:
                    if {field.name, field.attname} & kwargs["update_fields"]:
                        setattr(effective, field.attname, getattr(self, field.attname))
            effective.full_clean()
            return super().save(*args, **kwargs)

    delete = lifecycle_only

    @property
    def has_improved_score(self):
        # Read through for_viewer before exposing this projection to a customer.
        return bool(self.user_status == self.Status.SEEN and self.is_viewer_valid
                    and self.is_source_valid and self.is_currently_recommended
                    and self.current_score is not None and self.score_at_last_view is not None
                    and self.current_score > self.score_at_last_view >= self.minimum_score)
