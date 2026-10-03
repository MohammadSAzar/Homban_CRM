"""Score-free collaboration history. Normal writes are service-only."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

_COLLABORATION_WRITE = object()


def service_only(*args, **kwargs):
    raise ValidationError(_("تغییر درخواست همکاری فقط از طریق سرویس مجاز است."))


class CollaborationQuerySet(models.QuerySet):
    update = bulk_update = bulk_create = delete = service_only


def pair_is_valid(file, customer, requester, recipient):
    workspace = requester.workspace_id
    return bool(
        workspace and workspace == recipient.workspace_id == file.workspace_id == customer.workspace_id
        and requester.workspace.is_active and recipient.workspace.is_active
        and requester.is_active and recipient.is_active
        and requester.role == recipient.role == "consultant" and requester.pk != recipient.pk
        and {file.assigned_to_id, customer.assigned_to_id} == {requester.pk, recipient.pk}
        and file.status == customer.status == "active"
        and (file.transaction_type, customer.customer_type) in (("sale", "buyer"), ("rent", "tenant"))
        and file.city.workspace_id == workspace == file.region.workspace_id == file.region.city.workspace_id
        and file.region.city_id == file.city_id
        and all(region.workspace_id == workspace == region.city.workspace_id for region in customer.preferred_regions.all())
    )


class CollaborationRequest(models.Model):
    class Status(models.TextChoices):
        NEW = "new", _("جدید")
        SEEN = "seen", _("دیده‌شده")
        ACCEPTED = "accepted", _("پذیرفته‌شده")
        REJECTED = "rejected", _("ردشده")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    property_file = models.ForeignKey("properties.PropertyFile", on_delete=models.PROTECT, related_name="collaboration_requests", verbose_name=_("فایل"))
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="collaboration_requests", verbose_name=_("مشتری"))
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_collaborations", verbose_name=_("درخواست‌کننده"))
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="received_collaborations", verbose_name=_("گیرنده"))
    participant_a = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", editable=False, verbose_name=_("شرکت‌کننده اول"))
    participant_b = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", editable=False, verbose_name=_("شرکت‌کننده دوم"))
    manual_status = models.CharField(max_length=8, choices=Status.choices, default=Status.NEW, verbose_name=_("وضعیت پاسخ"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان ایجاد"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("زمان تغییر"))
    objects = CollaborationQuerySet.as_manager()

    class Meta:
        verbose_name = _("درخواست همکاری")
        verbose_name_plural = _("درخواست‌های همکاری")
        constraints = [
            models.UniqueConstraint(fields=["property_file", "customer", "participant_a", "participant_b"], name="unique_collaboration_pair"),
            models.CheckConstraint(condition=Q(participant_a__lt=F("participant_b")), name="collaboration_ordered_pair"),
            models.CheckConstraint(condition=(Q(requester=F("participant_a"), recipient=F("participant_b")) | Q(requester=F("participant_b"), recipient=F("participant_a"))), name="collaboration_participants"),
            models.CheckConstraint(condition=Q(manual_status__in=["new", "seen", "accepted", "rejected"]), name="collaboration_status_valid"),
        ]

    @property
    def is_valid(self):
        # Derived from authoritative loaded sources, never a stale background flag.
        return pair_is_valid(self.property_file, self.customer, self.requester, self.recipient)

    def clean(self):
        super().clean()
        fields = ("property_file_id", "customer_id", "requester_id", "recipient_id", "participant_a_id", "participant_b_id")
        stored = type(self).objects.filter(pk=self.pk).first() if not self._state.adding else None
        if stored and any(getattr(stored, name) != getattr(self, name) for name in fields):
            raise ValidationError(_("منابع و شرکت‌کنندگان درخواست قابل تغییر نیستند."))
        if not stored and all(getattr(self, name) for name in fields) and not self.is_valid:
            raise ValidationError(_("زوج فایل و مشتری برای همکاری معتبر نیست."))

    def save(self, *args, _token=None, **kwargs):
        if _token is not _COLLABORATION_WRITE:
            service_only()
        # Services only perform whole-row writes; reject partial internal bypasses.
        if kwargs.get("update_fields") is not None:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    delete = service_only


class CollaborationEvent(models.Model):
    """One durable recipient alert, atomically inserted with the original request."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("شناسه"))
    request = models.OneToOneField(CollaborationRequest, on_delete=models.PROTECT, related_name="creation_event", verbose_name=_("درخواست"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("زمان ایجاد"))
    objects = CollaborationQuerySet.as_manager()

    class Meta:
        verbose_name = _("رویداد درخواست همکاری" )
        verbose_name_plural = _("رویدادهای درخواست همکاری")

    def save(self, *args, _token=None, **kwargs):
        if _token is not _COLLABORATION_WRITE or not self._state.adding:
            service_only()
        self.full_clean()
        return super().save(*args, **kwargs)

    delete = service_only
