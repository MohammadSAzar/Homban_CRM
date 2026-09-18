from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.organizations.models import Workspace
from .models import City, Region
from .policies import require_location_manager


def _lock_workspace(actor):
    """Use the same workspace-then-user lock order as user management."""
    require_location_manager(actor)
    try:
        workspace = Workspace.objects.select_for_update().get(pk=actor.workspace_id, is_active=True)
        current = User.objects.select_for_update().get(pk=actor.pk, workspace=workspace)
    except (Workspace.DoesNotExist, User.DoesNotExist):
        raise PermissionDenied(_("دسترسی به تنظیمات مکانی فعال نیست.")) from None
    current.workspace = workspace
    require_location_manager(current)
    return workspace


def _validate_fields(data, allowed):
    invalid = set(data) - allowed
    if invalid:
        raise ValidationError({key: _("ارسال یا تغییر این فیلد مجاز نیست.") for key in sorted(invalid)})


def _duplicates(instance):
    filters = {"workspace_id": instance.workspace_id, "name": instance.name}
    if isinstance(instance, Region):
        filters["city_id"] = instance.city_id
    return type(instance).objects.filter(**filters).exclude(pk=instance.pk)


def _save_location(*, actor, model, data, object_id):
    workspace = _lock_workspace(actor)
    if object_id is None:
        instance = model(workspace=workspace, source=model.Source.MANUAL)
    else:
        try:
            instance = model.objects.select_for_update().get(pk=object_id, workspace=workspace)
        except model.DoesNotExist:
            raise NotFound(_("مکان یافت نشد.")) from None
        if instance.source != model.Source.MANUAL:
            raise PermissionDenied(_("ویرایش مکان‌های منبع خارجی فقط از مسیر همگام‌سازی مجاز است."))
    allowed = {"name", "is_active"} | ({"city"} if model is Region else set())
    _validate_fields(data, allowed)
    if model is Region and "city" in data:
        try:
            instance.city = City.objects.select_for_update().get(pk=data["city"], workspace=workspace)
        except City.DoesNotExist:
            raise ValidationError({"city": _("شهر انتخاب‌شده معتبر نیست.")}) from None
    for field in ("name", "is_active"):
        if field in data:
            setattr(instance, field, data[field])
    if _duplicates(instance).exists():
        raise ValidationError({"name": _("این نام در محدوده انتخاب‌شده استفاده شده است.")})
    try:
        with transaction.atomic():
            instance.full_clean()
            instance.save()
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error
    except IntegrityError as error:
        if _duplicates(instance).exists():
            raise ValidationError({"name": _("این نام در محدوده انتخاب‌شده استفاده شده است.")}) from error
        raise
    return instance


@transaction.atomic
def save_city(*, actor, data, object_id=None):
    return _save_location(actor=actor, model=City, data=data, object_id=object_id)


@transaction.atomic
def save_region(*, actor, data, object_id=None):
    return _save_location(actor=actor, model=Region, data=data, object_id=object_id)


@transaction.atomic
def set_region_mode(*, actor, data):
    workspace = _lock_workspace(actor)
    _validate_fields(data, {"region_mode"})
    mode = data.get("region_mode")
    if mode not in Workspace.RegionMode.values:
        raise ValidationError({"region_mode": _("نوع منطقه‌بندی معتبر نیست.")})
    workspace.region_mode = mode
    # Mode is configuration only: no deletion, source changes, or network calls.
    workspace.save(update_fields=["region_mode", "updated_at"])
    return workspace
