"""Explicit internal Workspace creation; no topology or authorization decisions."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models import Workspace


@transaction.atomic
def create_workspace(*, name, slug, customer_type, workspace_mode):
    """New application provisioning requires a mode; legacy ORM rows may be NULL."""
    if workspace_mode not in Workspace.WorkspaceMode.values:
        raise ValidationError({"workspace_mode": _("حالت محصول مجموعه باید صریحاً شخصی یا تیمی باشد.")})
    workspace = Workspace(
        name=name, slug=slug, customer_type=customer_type, workspace_mode=workspace_mode,
    )
    workspace.full_clean()
    workspace.save()
    return workspace
