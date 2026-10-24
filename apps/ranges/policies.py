from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from apps.accounts.policies import has_organizational_authority, is_local_range_manager
from apps.locations.models import Region
from .models import Range, RangeMembership


def can_read_ranges(actor):
    return bool(actor.is_authenticated and customer_can_authenticate(actor) and (
        has_organizational_authority(actor) or is_local_range_manager(actor)
        or actor.role == User.Role.CONSULTANT
    ))


def visible_ranges(actor):
    if not can_read_ranges(actor):
        raise PermissionDenied(_("اجازه مشاهده رنج‌ها را ندارید."))
    queryset = Range.objects.filter(workspace_id=actor.workspace_id)
    if has_organizational_authority(actor):
        return queryset
    if actor.role == User.Role.RANGE_MANAGER:
        return queryset.filter(manager_id=actor.pk)
    return queryset.filter(pk__in=RangeMembership.objects.filter(
        workspace_id=actor.workspace_id, user_id=actor.pk,
    ).values("range_id"))


def range_details(actor, *, saved_range=None):
    regions = Region.objects.filter(workspace_id=actor.workspace_id, city__workspace_id=actor.workspace_id)
    if saved_range is None and not (has_organizational_authority(actor) or is_local_range_manager(actor)):
        regions = regions.filter(is_active=True, city__is_active=True)
    # A successful structural write may revoke its own authority. Serialize only
    # that already-authorized result; subsequent reads resolve normal scope again.
    queryset = (Range.objects.filter(pk=saved_range.pk, workspace_id=actor.workspace_id)
                if saved_range is not None else visible_ranges(actor))
    return queryset.select_related("manager").annotate(
        consultant_count=Count("memberships", filter=Q(
            memberships__workspace_id=actor.workspace_id,
            memberships__user__workspace_id=actor.workspace_id,
            memberships__user__role=User.Role.CONSULTANT,
        )),
        has_region_constraints=Exists(Range.regions.through.objects.filter(range_id=OuterRef("pk"))),
    ).prefetch_related(Prefetch("regions", queryset=regions.order_by("name", "pk"), to_attr="visible_regions")).order_by("name", "pk")


def require_roster_read(actor, team):
    if not can_read_ranges(actor) or team.workspace_id != actor.workspace_id:
        raise PermissionDenied(_("اجازه مشاهده فهرست مشاوران را ندارید."))
    if not (has_organizational_authority(actor)
            or (actor.role == User.Role.RANGE_MANAGER and team.manager_id == actor.pk)):
        raise PermissionDenied(_("اجازه مشاهده فهرست مشاوران را ندارید."))


def roster_queryset(actor, team):
    require_roster_read(actor, team)
    return User.objects.filter(
        workspace_id=actor.workspace_id, role=User.Role.CONSULTANT,
        range_membership__workspace_id=actor.workspace_id, range_membership__range_id=team.pk,
    ).order_by("username", "pk")


class RangePermission(BasePermission):
    message = _("اجازه انجام این عملیات روی رنج را ندارید.")

    def has_permission(self, request, view):
        actor = request.user
        if not can_read_ranges(actor):
            return False
        if request.method in SAFE_METHODS:
            return True
        if getattr(view, "membership_write", False):
            return has_organizational_authority(actor) or is_local_range_manager(actor)
        return has_organizational_authority(actor)
