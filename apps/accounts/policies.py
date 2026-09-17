from django.db.models import Prefetch, Q
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission

from apps.ranges.models import Range
from .authentication import customer_can_authenticate
from .models import User


CREATABLE_ROLES = (
    User.Role.RANGE_MANAGER, User.Role.CONSULTANT, User.Role.SECRETARY, User.Role.ADMIN,
)


def require_user_manager(actor):
    if not actor.is_authenticated or not customer_can_authenticate(actor) or actor.role not in (
        User.Role.AGENCY_MANAGER, User.Role.RANGE_MANAGER,
    ):
        raise PermissionDenied(_("اجازه مدیریت کاربران را ندارید."))


class CanManageOrganizationalUsers(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        require_user_manager(request.user)
        return True


def managed_range(actor, *, required=True, lock=False):
    ranges = Range.objects.filter(
        workspace_id=actor.workspace_id, manager_id=actor.pk, is_active=True,
    ).order_by("pk")
    if lock:
        ranges = ranges.select_for_update()
    candidates = list(ranges[:2])
    if len(candidates) == 1:
        return candidates[0]
    if required:
        raise ValidationError({"range_id": _("برای این عملیات باید دقیقاً مدیر یک رنج فعال در مجموعه خود باشید.")})
    return None


def visible_users(actor):
    require_user_manager(actor)
    users = User.objects.filter(workspace_id=actor.workspace_id)
    if actor.role == User.Role.RANGE_MANAGER:
        team = managed_range(actor, required=False)
        scope = Q(pk=actor.pk)
        if team:
            scope |= Q(
                role=User.Role.CONSULTANT,
                range_membership__workspace_id=actor.workspace_id,
                range_membership__range_id=team.pk,
            )
        users = users.filter(scope)
    return users


def user_details_queryset(workspace_id):
    return User.objects.filter(workspace_id=workspace_id).select_related(
        "range_membership__range",
    ).prefetch_related(Prefetch(
        "managed_ranges", queryset=Range.objects.filter(workspace_id=workspace_id).order_by("name", "pk"),
        to_attr="visible_managed_ranges",
    ))


def require_creatable_role(actor, role):
    allowed = CREATABLE_ROLES if actor.role == User.Role.AGENCY_MANAGER else (User.Role.CONSULTANT,)
    if role not in allowed:
        raise ValidationError({"role": _("ایجاد کاربر با این نقش برای شما مجاز نیست.")})


def require_manageable_target(actor, target):
    if (target.pk == actor.pk or target.role == User.Role.AGENCY_MANAGER
            or target.is_workspace_owner):
        raise PermissionDenied(_("مدیریت این حساب از این مسیر مجاز نیست."))
    if actor.role == User.Role.RANGE_MANAGER and target.role != User.Role.CONSULTANT:
        raise PermissionDenied(_("فقط مدیریت مشاوران رنج خودتان مجاز است."))
