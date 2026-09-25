from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.locations.models import Region
from .models import Customer, CustomerRegionPreference


def validate_preferences(customer_id, region_ids, using="default"):
    """Read persisted workspace/state; permit retained, but not new, inactive links."""
    from apps.organizations.models import Workspace
    with transaction.atomic(using=using):
        workspace_id = Customer.objects.using(using).filter(pk=customer_id).values_list("workspace_id", flat=True).first()
        list(Workspace.objects.using(using).select_for_update().filter(pk=workspace_id))
        customer = Customer.objects.using(using).select_for_update().filter(pk=customer_id).first()
        if customer is None or customer.all_regions:
            raise ValidationError({"preferred_regions": _("انتخاب منطقه مشخص در این حالت مجاز نیست.")})
        existing = CustomerRegionPreference.objects.using(using).filter(customer_id=customer_id).values_list("region_id", flat=True)
        validate_region_set(workspace_id, region_ids, existing, using)


def validate_region_set(workspace_id, region_ids, existing, using="default"):
    ids = set(region_ids)
    regions = Region.objects.using(using).filter(pk__in=ids, workspace_id=workspace_id, city__workspace_id=workspace_id)
    if workspace_id is None or regions.count() != len(ids):
        raise ValidationError({"preferred_regions": _("مناطق باید متعلق به مجموعه مشتری باشند.")})
    if regions.exclude(pk__in=existing).filter(is_active=False).exists():
        raise ValidationError({"preferred_regions": _("انتخاب منطقه غیرفعال مجاز نیست.")})
