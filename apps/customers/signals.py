from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .models import Customer
from .validators import validate_preferences


@receiver(m2m_changed, sender=Customer.preferred_regions.through, dispatch_uid="customers.validate_preferences")
def validate_customer_regions(sender, instance, action, reverse, pk_set, using, **kwargs):
    if action != "pre_add" or not pk_set:
        return
    if reverse:
        for customer_id in pk_set:
            validate_preferences(customer_id, [instance.pk], using)
    else:
        validate_preferences(instance.pk, pk_set, using)
