from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.accounts.policies import has_organizational_authority, managed_range, require_manageable_target
from apps.accounts.services import lock_actor, reject_extra_fields
from apps.locations.models import Region
from apps.organizations.models import Workspace
from apps.organizations.topology_services import (
    TopologyMutationError, require_valid_locked_topology, topology_transaction,
)
from apps.organizations import topology_transitions
from .models import Range, RangeMembership


def require_range_structure_authority(actor):
    if not has_organizational_authority(actor):
        raise PermissionDenied(_("فقط مدیر مجاز مجموعه اجازه تغییر ساختار رنج را دارد."))


def _locked_range(actor, range_id):
    queryset = Range.objects.filter(workspace_id=actor.workspace_id)
    if not has_organizational_authority(actor):
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
def _save_range_ordinary(*, actor, data, range_id=None):
    actor = lock_actor(actor)
    require_range_structure_authority(actor)
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


def _structure_actor(actor):
    """Outer atomic caller: lock Workspace only, then reload and authorize actor.

    Do not use lock_actor here: D2b must lock all Users in PK order before
    Ranges. Current authority is included in D2b's current-topology discovery.
    The Workspace lock serializes cooperating actor/role mutations meanwhile.
    """
    if not actor.is_authenticated:
        raise PermissionDenied(_("دسترسی مدیریت رنج فعال نیست."))
    workspace = Workspace.objects.select_for_update().filter(pk=actor.workspace_id, is_active=True).first()
    if workspace is None:
        raise PermissionDenied(_("دسترسی مدیریت رنج فعال نیست."))
    current = User.objects.filter(pk=actor.pk, workspace=workspace).first()
    if current is None:
        raise PermissionDenied(_("دسترسی مدیریت رنج فعال نیست."))
    current.workspace = workspace
    require_range_structure_authority(current)
    return current


def _topology_api_error(error, candidate_field="manager"):
    """Stable Persian API errors; never expose internal diagnostics or foreign IDs."""
    codes = set(error.errors)
    if "outgoing_agency_manager_is_owner" in codes:
        return ValidationError({"non_field_errors": _("جایگزینی مدیر اصلیِ مالک تا تعیین فرایند مالکیت مجاز نیست.")})
    if "range_validation_failed" in codes:
        return ValidationError({"name": _("اطلاعات یا نام رنج معتبر نیست یا نام تکراری است.")})
    if codes & {"user_missing_or_wrong_workspace", "incoming_manager_wrong_role", "incoming_manager_inactive",
                "incoming_agency_manager_must_be_inactive", "range_manager_multiple_active_ranges",
                "prepared_range_missing_manager", "manager_unchanged"}:
        return ValidationError({candidate_field: _("مدیر انتخاب‌شده معتبر نیست یا شرایط انتساب را ندارد.")})
    return ValidationError({"non_field_errors": _("ساختار فعلی مجموعه برای این عملیات معتبر نیست؛ از فرایند اختصاصی تغییر ساختار استفاده کنید.")})


def _metadata(actor, team, data):
    """Companions run after topology locks, inside the same outer transaction."""
    regions = _resolve_regions(actor, team, data["regions"]) if "regions" in data else None
    for field in ("name", "activity_scope"):
        if field in data:
            setattr(team, field, data[field])
    try:
        team.save()
        if regions is not None:
            team.regions.set(regions)
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error
    return team


@transaction.atomic
def save_range(*, actor, data, range_id=None):
    actor = _structure_actor(actor)
    # Scope path resources before payload/topology validation; no subordinate lock.
    team = None
    if range_id is not None:
        team = Range.objects.filter(pk=range_id, workspace_id=actor.workspace_id).first()
        if team is None:
            raise NotFound(_("رنج یافت نشد."))
    reject_extra_fields(data, {"name", "manager", "activity_scope", "regions", "is_active"})
    if team is None:
        intent = "create" if data.get("is_active", True) else None
    else:
        status_change = "is_active" in data and data["is_active"] != team.is_active
        manager_change = "manager" in data and str(data["manager"]) != str(team.manager_id)
        if status_change and manager_change:
            raise ValidationError({"non_field_errors": _("در هر درخواست فقط یک تغییر ساختاری مجاز است؛ تغییر مدیر و وضعیت را جداگانه انجام دهید.")})
        if team.is_active and "manager" in data and data["manager"] is None:
            raise ValidationError({"manager": _("حذف مدیر رنج فعال مجاز نیست.")})
        intent = ("deactivate" if team.is_active else "activate") if status_change else (
            "manager" if team.is_active and manager_change else None)
    if intent is None:
        return _save_range_ordinary(actor=actor, data=data, range_id=range_id)
    try:
        start = require_valid_locked_topology(actor.workspace)
        if intent in ("create", "activate") and start.topology != "multi_team":
            raise ValidationError({"is_active": _("افزودن رنج فعال به مجموعه تک‌رنجی نیازمند فرایند صریح گسترش و تعیین مدیر املاک است.")})
        if intent == "create":
            if data.get("manager") is None:
                raise ValidationError({"manager": _("رنج فعال باید مدیر معتبر داشته باشد.")})
            team = topology_transitions.add_active_range_to_multi_team(
                workspace_id=actor.workspace_id, manager_id=data["manager"], range_name=data["name"],
                activity_scope=data.get("activity_scope", Range.ActivityScope.ALL))
        elif intent == "activate":
            team = topology_transitions.activate_prepared_range(workspace_id=actor.workspace_id, range_id=team.pk)
        elif intent == "manager":
            team = topology_transitions.replace_range_manager(
                workspace_id=actor.workspace_id, range_id=team.pk, incoming_manager_id=data["manager"])
        else:
            if start.topology == "single_team":
                raise ValidationError({"is_active": _("غیرفعال کردن آخرین رنج فعال مجموعه مجاز نیست.")})
            active_ids = list(Range.objects.filter(workspace_id=actor.workspace_id, is_active=True).values_list("pk", flat=True))
            if len(active_ids) == 2:
                keep_id = next(pk for pk in active_ids if pk != team.pk)
                topology_transitions.transition_multi_to_single(workspace_id=actor.workspace_id, keep_range_id=keep_id)
                team.refresh_from_db()
            else:
                with topology_transaction(workspace_id=actor.workspace_id, range_ids=(team.pk,),
                                          include_current_topology=True) as locked:
                    team = next(row for row in locked.ranges if row.pk == team.pk)
                    team.is_active = False
                    team.save(update_fields=["is_active", "updated_at"])
                    if not has_organizational_authority(actor):
                        raise TopologyMutationError("unexpected_final_authority")
        team = _metadata(actor, team, data)
        require_valid_locked_topology(actor.workspace)
        return team
    except TopologyMutationError as error:
        raise _topology_api_error(error) from error


@transaction.atomic
def expand_single_team(*, actor, data):
    actor = _structure_actor(actor)
    reject_extra_fields(data, {"agency_manager_id", "new_range_manager_id", "new_range_name", "activity_scope", "regions"})
    try:
        team = topology_transitions.transition_single_to_multi(
            workspace_id=actor.workspace_id, agency_manager_id=data["agency_manager_id"],
            new_range_manager_id=data["new_range_manager_id"], new_range_name=data["new_range_name"],
            activity_scope=data.get("activity_scope", Range.ActivityScope.ALL))
        _metadata(actor, team, {"regions": data["regions"]} if "regions" in data else {})
        require_valid_locked_topology(actor.workspace)
        return team
    except TopologyMutationError as error:
        raise _topology_api_error(error, "non_field_errors") from error


@transaction.atomic
def replace_agency_authority(*, actor, data):
    actor = _structure_actor(actor)
    reject_extra_fields(data, {"incoming_agency_manager_id"})
    try:
        return topology_transitions.replace_multi_team_agency_manager(
            workspace_id=actor.workspace_id, incoming_agency_manager_id=data["incoming_agency_manager_id"])
    except TopologyMutationError as error:
        raise _topology_api_error(error, "incoming_agency_manager_id") from error


def _membership_context(actor, range_id, user_id):
    team = _locked_range(actor, range_id)
    if not has_organizational_authority(actor):
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
    if not has_organizational_authority(actor) and membership and membership.range_id != team.pk:
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
    if not has_organizational_authority(actor) and "from_range" in data:
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
