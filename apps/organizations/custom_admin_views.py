from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils.translation import gettext_lazy as _

from apps.accounts.user_serializers import StrictInputSerializer
from .custom_admin_catalog import AdminPermission, permission_metadata
from .custom_admin_policy import require_custom_admin_access, require_grant_authority, custom_admin_current_state
from .custom_admin_services import create_grant, revoke_grant, list_grants


class GrantInput(StrictInputSerializer):
    user_id = serializers.UUIDField(label=_("کاربر"))
    permission = serializers.ChoiceField(choices=AdminPermission.choices, label=_("مجوز"))


def grant_data(row):
    metadata = permission_metadata(row.permission)
    if metadata is None:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied(_("مجوز نامعتبر است."))
    return {"id": str(row.pk), "user": {"id": str(row.user_id), "username": row.user.username,
            "first_name": row.user.first_name, "last_name": row.user.last_name},
            "permission": row.permission, "permission_display": metadata["label"],
            "created_at": row.created_at.isoformat()}


class PermissionCatalogView(APIView):
    def get(self, request):
        require_custom_admin_access(request.user)
        return Response([
            {"permission": key, "display": metadata["label"], **metadata}
            for key in AdminPermission.values
            for metadata in (permission_metadata(key),)
        ])


class GrantListView(APIView):
    def get(self, request):
        return Response([grant_data(row) for row in list_grants(actor=request.user)])

    def post(self, request):
        require_grant_authority(request.user)
        data = GrantInput(data=request.data)
        data.is_valid(raise_exception=True)
        row = create_grant(actor=request.user, **data.validated_data)
        return Response(grant_data(row), status=status.HTTP_201_CREATED)


class GrantDetailView(APIView):
    def delete(self, request, pk):
        revoke_grant(actor=request.user, grant_id=pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomAdminMeView(APIView):
    def get(self, request):
        return Response(custom_admin_current_state(request.user))
