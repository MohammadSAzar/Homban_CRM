from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_region_assignment_workspace(workspace_id, related_objects):
    """Validate ranges or regions in one query, in either relation direction."""
    if related_objects.exclude(workspace_id=workspace_id).exists():
        raise ValidationError(
            {"regions": _("رنج و مناطق انتخاب‌شده باید متعلق به یک مجموعه باشند.")}
        )
