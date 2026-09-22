from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.locations.models import Region
from .models import Customer, CustomerRegionPreference


def validate_preferences(customer_id, region_ids, using="default"):
    """Read persisted workspace/state; permit retained, but not new, inactive links."""
    workspace_id = Customer.objects.using(using).filter(pk=customer_id).values_list("workspace_id", flat=True).first()
    ids = set(region_ids)
    regions = Region.objects.using(using).filter(pk__in=ids, workspace_id=workspace_id, city__workspace_id=workspace_id)
    if workspace_id is None or regions.count() != len(ids):
        raise ValidationError({"preferred_regions": _("مناطق باید متعلق به مجموعه مشتری باشند.")})
    existing = CustomerRegionPreference.objects.using(using).filter(customer_id=customer_id, region_id__in=ids).values_list("region_id", flat=True)
    if regions.exclude(pk__in=existing).filter(is_active=False).exists():
        raise ValidationError({"preferred_regions": _("انتخاب منطقه غیرفعال مجاز نیست.")})
