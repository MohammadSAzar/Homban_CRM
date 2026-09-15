from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .models import Range
from .validators import validate_region_assignment_workspace


@receiver(
    m2m_changed,
    sender=Range.regions.through,
    dispatch_uid="ranges.validate_region_assignment_workspace",
)
def validate_range_regions(sender, instance, action, model, pk_set, using, **kwargs):
    """Protect forward and reverse add/set calls before any links are inserted.

    Direct through-table writes do not emit m2m_changed and are unsupported.
    """
    if action != "pre_add" or not pk_set:
        return

    workspace_id = (
        type(instance).objects.using(using)
        .values_list("workspace_id", flat=True)
        .get(pk=instance.pk)
    )
    validate_region_assignment_workspace(
        workspace_id, model.objects.using(using).filter(pk__in=pk_set)
    )
