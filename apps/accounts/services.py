from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.organizations.models import Workspace
from apps.ranges.models import Range, RangeMembership
from .models import User
from .policies import (
    managed_range, require_creatable_role, require_manageable_target,
    require_user_manager, visible_users,
)


PROFILE_FIELDS = {"first_name", "last_name", "phone_number", "is_active"}
CREATE_FIELDS = PROFILE_FIELDS | {"username", "password", "role", "range_id"}
UPDATE_FIELDS = PROFILE_FIELDS | {"range_id"}


def reject_extra_fields(data, allowed):
    invalid = set(data) - allowed
    if invalid:
        raise ValidationError({key: _("ارسال یا تغییر این فیلد مجاز نیست.") for key in sorted(invalid)})


def lock_actor(actor):
    """Serialize management writes per workspace and recheck current actor state."""
    require_user_manager(actor)
    try:
        workspace = Workspace.objects.select_for_update().get(pk=actor.workspace_id, is_active=True)
        current = User.objects.select_for_update().get(pk=actor.pk, workspace=workspace)
    except (Workspace.DoesNotExist, User.DoesNotExist):
        raise PermissionDenied(_("دسترسی مدیریت کاربران فعال نیست.")) from None
    current.workspace = workspace
    require_user_manager(current)
    return current


def resolve_assignment(actor, data, role):
    if actor.role == User.Role.RANGE_MANAGER:
        if "range_id" in data:
            raise ValidationError({"range_id": _("رنج مشاور به‌صورت خودکار تعیین می‌شود و قابل انتخاب نیست.")})
        return managed_range(actor, lock=True)
    if "range_id" not in data:
        return None
    if role not in (User.Role.CONSULTANT, User.Role.RANGE_MANAGER):
        raise ValidationError({"range_id": _("این نقش امکان انتساب به رنج را ندارد.")})
    if data["range_id"] is None:
        return None
    try:
        return Range.objects.select_for_update().get(
            pk=data["range_id"], workspace_id=actor.workspace_id, is_active=True,
        )
    except Range.DoesNotExist:
        raise ValidationError({"range_id": _("رنج انتخاب‌شده معتبر نیست.")}) from None


def save_validated_user(user):
    try:
        user.full_clean()
    except ModelValidationError as error:
        raise ValidationError(error.message_dict) from error
    user.save()


@transaction.atomic
def create_organizational_user(*, actor, data):
    actor = lock_actor(actor)
    reject_extra_fields(data, CREATE_FIELDS)
    require_creatable_role(actor, data["role"])
    team = resolve_assignment(actor, data, data["role"])
    if team and data["role"] == User.Role.RANGE_MANAGER and team.manager_id:
        raise ValidationError({"range_id": _("این رنج مدیر دارد؛ جایگزینی مدیر در این عملیات مجاز نیست.")})
    username = User.normalize_username(data["username"])
    if User.objects.filter(workspace_id=actor.workspace_id, username=username).exists():
        raise ValidationError({"username": _("این نام کاربری در مجموعه استفاده شده است.")})
    user = User(
        workspace_id=actor.workspace_id, username=username, role=data["role"],
        **{key: value for key, value in data.items() if key in PROFILE_FIELDS},
    )
    try:
        validate_password(data["password"], user=user)
    except ModelValidationError as error:
        raise ValidationError({"password": error.messages}) from error
    user.set_password(data["password"])
    try:
        with transaction.atomic():
            save_validated_user(user)
    except IntegrityError as error:
        if User.objects.filter(workspace_id=actor.workspace_id, username=username).exists():
            raise ValidationError({"username": _("این نام کاربری در مجموعه استفاده شده است.")}) from error
        raise
    if team:
        if user.role == User.Role.CONSULTANT:
            RangeMembership.objects.create(workspace_id=actor.workspace_id, range=team, user=user)
        else:
            team.manager = user
            team.save(update_fields=["manager", "updated_at"])
    return user


@transaction.atomic
def update_organizational_user(*, actor, user_id, data):
    actor = lock_actor(actor)
    if actor.role == User.Role.RANGE_MANAGER:
        managed_range(actor, lock=True)
    # Scope lookup before validation: foreign and nonexistent UUIDs both return 404.
    if not visible_users(actor).filter(pk=user_id).exists():
        raise NotFound(_("کاربر یافت نشد."))
    target = User.objects.select_for_update().get(pk=user_id, workspace_id=actor.workspace_id)
    require_manageable_target(actor, target)
    reject_extra_fields(data, UPDATE_FIELDS)
    if "range_id" in data:
        if target.role != User.Role.CONSULTANT:
            raise ValidationError({"range_id": _("تغییر عضویت رنج فقط برای مشاور مجاز است.")})
        team = resolve_assignment(actor, data, target.role)
        membership = RangeMembership.objects.select_for_update().filter(user=target).first()
        if membership and membership.workspace_id != actor.workspace_id:
            raise ValidationError({"range_id": _("عضویت فعلی کاربر معتبر نیست.")})
        if team is None:
            if membership:
                membership.delete()
        elif membership:
            membership.range = team
            membership.save(update_fields=["range"])
        else:
            RangeMembership.objects.create(workspace_id=actor.workspace_id, range=team, user=target)
    for field, value in data.items():
        if field in PROFILE_FIELDS:
            setattr(target, field, value)
    save_validated_user(target)
    return target
