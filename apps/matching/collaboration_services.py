"""Participant-only commands. Workspace lock serializes source changes and opposite submissions."""
from django.db import transaction
from django.db.models import Prefetch
from django.utils.crypto import constant_time_compare
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.accounts.authentication import customer_can_authenticate
from apps.organizations.models import Workspace
from apps.properties.models import PropertyFile
from apps.customers.models import Customer
from apps.locations.models import Region
from .models import MatchRecommendation
from .collaboration_models import CollaborationRequest, CollaborationEvent, _COLLABORATION_WRITE, pair_is_valid
from .collaboration_references import verify_reference, ownership_fingerprint
from .collaboration_serializers import collaboration_data, identity


def _actor(actor):
    workspace = Workspace.objects.select_for_update().filter(pk=actor.workspace_id, is_active=True).first()
    user = User.objects.select_for_update().filter(pk=actor.pk, workspace=workspace).first() if workspace else None
    if user is None:
        raise PermissionDenied(_("دسترسی مجاز نیست."))
    user.workspace = workspace
    if not customer_can_authenticate(user) or user.role != "consultant":
        raise PermissionDenied(_("درخواست همکاری فقط برای مشاور شرکت‌کننده مجاز است."))
    return user


def _pair(actor, file_id, customer_id):
    file = PropertyFile.objects.select_for_update().select_related("city", "region__city").filter(pk=file_id, workspace_id=actor.workspace_id).first()
    customer = Customer.objects.select_for_update().prefetch_related(Prefetch("preferred_regions", queryset=Region.objects.select_related("city"))).filter(pk=customer_id, workspace_id=actor.workspace_id).first()
    if not file or not customer or actor.pk not in (file.assigned_to_id, customer.assigned_to_id):
        raise NotFound(_("زوج فایل و مشتری یافت نشد."))
    other_id = customer.assigned_to_id if file.assigned_to_id == actor.pk else file.assigned_to_id
    other = User.objects.select_related("workspace").filter(pk=other_id, workspace_id=actor.workspace_id).first()
    if other is None or not pair_is_valid(file, customer, actor, other):
        raise ValidationError(_("زوج فایل و مشتری برای همکاری فعال و معتبر نیست."))
    return file, customer, other


def _establish(actor, file, customer, other):
    # Every public entry holds the Workspace lock BEFORE this lookup. Opposite
    # submissions serialize here; the unique canonical DB key remains a backstop.
    a, b = sorted((actor.pk, other.pk))
    row = CollaborationRequest.objects.filter(property_file=file, customer=customer, participant_a_id=a, participant_b_id=b).first()
    created = row is None
    if created:
        row = CollaborationRequest(property_file=file, customer=customer, requester=actor, recipient=other, participant_a_id=a, participant_b_id=b)
        row.save(_token=_COLLABORATION_WRITE)
        CollaborationEvent(request=row).save(_token=_COLLABORATION_WRITE)
    else:
        row.property_file, row.customer = file, customer
        row.requester, row.recipient = (actor, other) if row.requester_id == actor.pk else (other, actor)
    result = collaboration_data(row)
    result.update(created=created, other_consultant=identity(other), message=_("درخواست همکاری ثبت شد.") if created else _("برای این فایل و مشتری قبلاً بین شما و %(name)s درخواست همکاری ثبت شده است.") % {"name": other.get_full_name() or other.username})
    return result


@transaction.atomic
def create_from_recommendation(*, actor, recommendation_id):
    actor = _actor(actor)
    row = MatchRecommendation.objects.filter(pk=recommendation_id, viewer=actor).first()
    if row is None:
        raise NotFound(_("پیشنهاد یافت نشد."))
    file, customer, other = _pair(actor, row.property_file_id, row.customer_id)
    if not row.is_viewer_valid or not row.is_source_valid:
        raise ValidationError(_("پیشنهاد منقضی شده است."))
    return _establish(actor, file, customer, other)


@transaction.atomic
def create_from_live(*, actor, reference):
    actor = _actor(actor)
    data = verify_reference(reference, actor)
    file, customer, other = _pair(actor, data["file"], data["customer"])
    if not constant_time_compare(ownership_fingerprint(file, customer), data["ownership"]):
        raise ValidationError(_("مالکیت منابع پس از صدور مرجع تغییر کرده است."))
    return _establish(actor, file, customer, other)


@transaction.atomic
def open_request(*, actor, request_id, status=None):
    actor = _actor(actor)
    row = CollaborationRequest.objects.select_for_update().select_related(
        "requester__workspace", "recipient__workspace", "property_file__city", "property_file__region__city", "customer",
    ).prefetch_related(Prefetch("customer__preferred_regions", queryset=Region.objects.select_related("city"))).for_participant(actor).filter(pk=request_id).first()
    if row is None:
        raise NotFound(_("درخواست همکاری یافت نشد."))
    if status is not None:
        if actor.pk != row.recipient_id:
            raise PermissionDenied(_("فقط گیرنده می‌تواند پاسخ درخواست را تغییر دهد."))
        if status not in ("seen", "accepted", "rejected") or not row.is_valid:
            raise ValidationError(_("تغییر وضعیت درخواست معتبر نیست."))
    elif actor.pk == row.recipient_id and row.manual_status == "new" and row.is_valid:
        status = "seen"
    if status is not None and status != row.manual_status:
        row.manual_status = status
        row.save(_token=_COLLABORATION_WRITE)
    return collaboration_data(row)
