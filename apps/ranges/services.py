from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.accounts.policies import managed_range, require_manageable_target
from apps.accounts.services import lock_actor, reject_extra_fields
from apps.locations.models import Region
from .models import Range, RangeMembership


def require_agency_manager(actor):
    if actor.role != User.Role.AGENCY_MANAGER:
        raise PermissionDenied(_("فقط مدیر املاک اجازه تغییر ساختار رنج را دارد."))


def _locked_range(actor, range_id):
    queryset = Range.objects.filter(workspace_id=actor.workspace_id)
    if actor.role == User.Role.RANGE_MANAGER:
        queryset = queryset.filter(manager_id=actor.pk)
    try:
        return queryset.select_for_update().get(pk=range_id)
    except Range.DoesNotExist:
        raise NotFound(_("رنج یافت نشد.")) from None


def _resolve_regions(actor, team, region_ids):
    ids = set(region_ids)
    regions = list(Region.objects.select_for_update().select_related("city").filter(
        pk__in=ids, workspace_id=actor.workspace_id, city__workspace_id=actor.workspace_id,
    ))
    if len(regions) != len(ids):
        raise ValidationError({"regions": _("مناطق انتخاب‌شده معتبر نیستند.")})
    existing = set(team.regions.values_list("pk", flat=True)) if not team._state.adding else set()
    if any(region.pk not in existing and (not region.is_active or not region.city.is_active) for region in regions):
        raise ValidationError({"regions": _("انتساب جدید به منطقه یا شهر غیرفعال مجاز نیست.")})
    return regions


@transaction.atomic
def save_range(*, actor, data, range_id=None):
    actor = lock_actor(actor)
    require_agency_manager(actor)
    team = _locked_range(actor, range_id) if range_id else Range(workspace_id=actor.workspace_id)
    reject_extra_fields(data, {"name", "manager", "activity_scope", "regions", "is_active"})
    if "manager" in data:
        if data["manager"] is None:
            team.manager = None
        else:
            try:
                team.manager = User.objects.select_for_update().get(
                    pk=data["manager"], workspace_id=actor.workspace_id, role=User.Role.RANGE_MANAGER,
                )
            except User.DoesNotExist:
                raise ValidationError({"manager": _("مدیر رنج انتخاب‌شده معتبر نیست.")}) from None
    regions = _resolve_regions(actor, team, data["regions"]) if "regions" in data else None
    for field in ("name", "activity_scope", "is_active"):
        if field in data:
            setattr(team, field, data[field])
    duplicates = Range.objects.filter(workspace_id=actor.workspace_id, name=team.name).exclude(pk=team.pk)
    if duplicates.exists():
        raise ValidationError({"name": _("این نام رنج در مجموعه استفاده شده است.")})
    try:
        with transaction.atomic():
            team.save()
            if regions is not None:
                team.regions.set(regions)
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error
    except IntegrityError as error:
        if duplicates.exists():
            raise ValidationError({"name": _("این نام رنج در مجموعه استفاده شده است.")}) from error
        raise
    return team


def _membership_context(actor, range_id, user_id):
    team = _locked_range(actor, range_id)
    if actor.role == User.Role.RANGE_MANAGER:
        own = managed_range(actor, lock=True)
        if own.pk != team.pk:
            raise PermissionDenied(_("فقط مدیریت اعضای رنج فعال خودتان مجاز است."))
    try:
        user = User.objects.select_for_update().get(pk=user_id, workspace_id=actor.workspace_id)
    except User.DoesNotExist:
        raise NotFound(_("مشاور یافت نشد.")) from None
    membership = RangeMembership.objects.select_for_update().filter(user=user).first()
    if membership and (membership.workspace_id != actor.workspace_id
                       or membership.range.workspace_id != actor.workspace_id):
        raise NotFound(_("مشاور یافت نشد."))
    if actor.role == User.Role.RANGE_MANAGER and membership and membership.range_id != team.pk:
        raise NotFound(_("مشاور یافت نشد."))
    if user.role != User.Role.CONSULTANT:
        raise ValidationError({"user": _("فقط مشاور می‌تواند عضو رنج باشد.")})
    require_manageable_target(actor, user)
    return team, user, membership


@transaction.atomic
def assign_consultant(*, actor, range_id, user_id, data):
    actor = lock_actor(actor)
    team, user, membership = _membership_context(actor, range_id, user_id)
    reject_extra_fields(data, {"from_range"})
    if not team.is_active:
        raise ValidationError({"range": _("انتساب مشاور به رنج غیرفعال مجاز نیست.")})
    if actor.role == User.Role.RANGE_MANAGER and "from_range" in data:
        raise ValidationError({"from_range": _("انتقال مشاور از رنج دیگر برای شما مجاز نیست.")})
    if membership and membership.range_id == team.pk:
        return membership
    previous_id = membership.range_id if membership else None
    if (membership and "from_range" not in data) or data.get("from_range") != previous_id:
        raise ValidationError({"from_range": _("برای انتقال، رنج فعلی مشاور را به‌درستی مشخص کنید.")})
    # OneToOne membership and the workspace/user locks prevent duplicate service writes.
    if membership:
        membership.range = team
        membership.save(update_fields=["range"])
    else:
        membership = RangeMembership.objects.create(workspace_id=actor.workspace_id, range=team, user=user)
    return membership


@transaction.atomic
def remove_consultant(*, actor, range_id, user_id, data):
    actor = lock_actor(actor)
    team, user, membership = _membership_context(actor, range_id, user_id)
    reject_extra_fields(data, set())
    if not membership or membership.range_id != team.pk:
        raise NotFound(_("عضویت یافت نشد."))
    membership.delete()
