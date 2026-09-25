from django.db import transaction

from apps.organizations.models import Workspace
from .models import Customer, CustomerValuableReason


@transaction.atomic
def create_customer(*, workspace, preferred_regions=(), valuable_reasons=(), **fields):
    """Internal domain operation; future callers must authorize and derive workspace."""
    workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
    customer = Customer(workspace=workspace, **fields)
    customer.save(preferred_regions=preferred_regions)
    for reason in valuable_reasons:
        CustomerValuableReason.objects.create(customer=customer, reason=reason)
    return customer


@transaction.atomic
def set_preferred_regions(*, customer, regions):
    workspace = Workspace.objects.select_for_update().get(pk=customer.workspace_id)
    customer = Customer.objects.select_for_update().get(pk=customer.pk, workspace=workspace)
    customer.save(preferred_regions=regions)
    return customer
