from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.accounts.authentication import customer_can_authenticate
from apps.accounts.models import User
from .models import Customer


def require_customer_actor(actor):
    if not actor.is_authenticated or not customer_can_authenticate(actor) or actor.role not in (
        User.Role.AGENCY_MANAGER, User.Role.RANGE_MANAGER, User.Role.CONSULTANT,
    ):
        raise PermissionDenied(_("اجازه دسترسی به مشتریان را ندارید."))


def scoped_consultants(actor):
    require_customer_actor(actor)
    users = User.objects.filter(workspace_id=actor.workspace_id, role=User.Role.CONSULTANT)
    if actor.role == User.Role.CONSULTANT:
        return users.filter(pk=actor.pk)
    if actor.role == User.Role.RANGE_MANAGER:
        return users.filter(
            range_membership__workspace_id=actor.workspace_id,
            range_membership__range__workspace_id=actor.workspace_id,
            range_membership__range__manager_id=actor.pk,
            range_membership__range__is_active=True,
        )
    return users


def visible_customers(actor):
    # Tenant and actor scope always precede lookup, filtering and serialization.
    users = scoped_consultants(actor)
    return Customer.objects.filter(workspace_id=actor.workspace_id, assigned_to_id__in=users.values("pk"))


class CustomerPermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        require_customer_actor(request.user)
        return True
