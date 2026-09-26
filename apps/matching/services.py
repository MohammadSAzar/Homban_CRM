from django.core.exceptions import ValidationError as ModelValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from apps.accounts.services import reject_extra_fields
from apps.organizations.models import Workspace
from .models import MatchingProfile, SETTING_FIELDS


@transaction.atomic
def own_profile(*, actor, data=None, reset=False):
    """Serialize lazy creation/updates using Workspace -> User -> Profile locks."""
    try:
        workspace = Workspace.objects.select_for_update().get(pk=actor.workspace_id, is_active=True)
        user = User.objects.select_for_update().get(pk=actor.pk, workspace=workspace)
    except (Workspace.DoesNotExist, User.DoesNotExist):
        raise PermissionDenied(_("دسترسی به حساب کاربری یا مجموعه فعال نیست.")) from None
    user.workspace = workspace
    if not customer_can_authenticate(user):
        raise PermissionDenied(_("دسترسی به حساب کاربری یا مجموعه فعال نیست."))
    if data is not None:
        reject_extra_fields(data, set() if reset else set(SETTING_FIELDS))
    try:
        profile, _created = MatchingProfile.objects.select_for_update().get_or_create(user=user)
        if reset:
            for name in SETTING_FIELDS:
                setattr(profile, name, MatchingProfile._meta.get_field(name).get_default())
        elif data is not None:
            for name, value in data.items():
                setattr(profile, name, value)
        if reset or data is not None:
            profile.save()
        return profile
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error
