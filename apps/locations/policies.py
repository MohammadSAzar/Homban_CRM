from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.authentication import customer_can_authenticate
from apps.organizations.authority import resolve_workspace_authority
from .models import City, Region


def can_manage_location(actor):
    if not actor.is_authenticated or not customer_can_authenticate(actor):
        return False
    resolution = resolve_workspace_authority(actor.workspace)
    return (
        resolution.status == "resolved"
        and resolution.authority_user.pk == actor.pk
    )


def require_location_manager(actor):
    if not can_manage_location(actor):
        raise PermissionDenied(_("اجازه مدیریت تنظیمات مکانی را ندارید."))


def _require_customer(actor):
    if not actor.is_authenticated or not customer_can_authenticate(actor):
        raise PermissionDenied(_("دسترسی به مجموعه فعال نیست."))


def visible_cities(actor):
    _require_customer(actor)
    cities = City.objects.filter(workspace_id=actor.workspace_id)
    if not can_manage_location(actor):
        cities = cities.filter(is_active=True)
    return cities.order_by("name", "pk")


def visible_regions(actor):
    _require_customer(actor)
    regions = Region.objects.filter(
        workspace_id=actor.workspace_id, city__workspace_id=actor.workspace_id,
    ).select_related("city")
    if not can_manage_location(actor):
        regions = regions.filter(is_active=True, city__is_active=True)
    return regions.order_by("city__name", "name", "pk")


class LocationPermission(BasePermission):
    message = _("اجازه مدیریت تنظیمات مکانی را ندارید.")

    def has_permission(self, request, view):
        actor = request.user
        if not actor.is_authenticated or not customer_can_authenticate(actor):
            return False
        return request.method in SAFE_METHODS or can_manage_location(actor)
