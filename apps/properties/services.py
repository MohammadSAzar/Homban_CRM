from django.db import transaction

from apps.organizations.models import Workspace

from .models import PropertyFile, PropertyFileImage, PropertyFileValuableReason


@transaction.atomic
def create_property_file(*, workspace, valuable_reasons=(), image_references=(), **fields):
    """Persist a validated file and its children together; internal domain operation.

    This does not grant actor permissions. A future API must authorize its actor
    and derive workspace before calling this service.
    """
    # Share the management services' lock order, including Region city changes.
    workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
    property_file = PropertyFile.objects.create(workspace=workspace, **fields)
    for reason in valuable_reasons:
        PropertyFileValuableReason.objects.create(property_file=property_file, reason=reason)
    for position, reference in enumerate(image_references):
        PropertyFileImage.objects.create(property_file=property_file, reference=reference, sort_order=position)
    return property_file
