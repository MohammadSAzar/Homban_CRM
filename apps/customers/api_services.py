from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.accounts.services import reject_extra_fields
from apps.locations.models import Region
from apps.organizations.models import Workspace
from .models import Customer, CustomerValuableReason
from .policies import require_customer_actor, scoped_consultants, visible_customers
from .services import create_customer


WRITE_FIELDS = (
    "assigned_to", "customer_type", "status", "name", "mobile", "description",
    "is_valuable", "min_area", "max_area", "min_building_age", "max_building_age",
    "bedrooms", "budget", "budget_status", "deposit_budget", "monthly_rent_budget",
    "all_regions", "preferred_regions", "valuable_reasons",
)


def lock_actor(actor):
    require_customer_actor(actor)
    try:
        workspace = Workspace.objects.select_for_update().get(pk=actor.workspace_id, is_active=True)
        current = User.objects.select_for_update().get(pk=actor.pk, workspace=workspace)
    except (Workspace.DoesNotExist, User.DoesNotExist):
        raise PermissionDenied(_("دسترسی به مجموعه فعال نیست.")) from None
    current.workspace = workspace
    require_customer_actor(current)
    return current


def get_scoped_customer(actor, customer_id, *, lock=False):
    queryset = visible_customers(actor)
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=customer_id)
    except Customer.DoesNotExist:
        raise NotFound(_("مشتری یافت نشد.")) from None


@transaction.atomic
def save_customer(*, actor, data, customer_id=None):
    actor = lock_actor(actor)
    item = get_scoped_customer(actor, customer_id, lock=True) if customer_id else None
    reject_extra_fields(data, set(WRITE_FIELDS))
    fields = dict(data)
    reasons = fields.pop("valuable_reasons", None)
    region_ids = fields.pop("preferred_regions", None)
    if item is None or "assigned_to" in fields:
        target_id = fields.get("assigned_to", actor.pk if actor.role == User.Role.CONSULTANT else None)
        assignee = scoped_consultants(actor).filter(pk=target_id, is_active=True).first()
        if assignee is None:
            raise ValidationError({"assigned_to": _("مشاور فعال و مجاز در محدوده خود انتخاب کنید.")})
        fields["assigned_to"] = assignee
    regions = None
    if region_ids is not None:
        regions = list(Region.objects.filter(
            pk__in=region_ids, workspace_id=actor.workspace_id, city__workspace_id=actor.workspace_id,
        ))
        if len(regions) != len(set(region_ids)):
            raise ValidationError({"preferred_regions": _("مناطق انتخاب‌شده معتبر نیستند.")})
    try:
        if item is None:
            return create_customer(
                workspace=actor.workspace, preferred_regions=regions or (),
                valuable_reasons=reasons or (), **fields,
            )
        if "customer_type" in fields and fields["customer_type"] != item.customer_type:
            incompatible = ("budget", "budget_status") if fields["customer_type"] == "tenant" else ("deposit_budget", "monthly_rent_budget")
            for key in incompatible:
                if fields.get(key) is not None:
                    raise ValidationError({key: _("این مقدار برای نوع مشتری انتخاب‌شده باید خالی باشد.")})
                fields[key] = None
        for key, value in fields.items():
            setattr(item, key, value)
        item.save(**({"preferred_regions": regions} if regions is not None else {}))
        if reasons is not None:
            item.valuable_reasons.all().delete()
            for reason in reasons:
                CustomerValuableReason.objects.create(customer=item, reason=reason)
        return item
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error
