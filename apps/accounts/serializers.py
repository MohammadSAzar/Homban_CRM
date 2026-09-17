from django.contrib.auth import authenticate
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)

from .authentication import get_customer_for_token
from .models import User
from .workspace_context import resolve_customer_workspace


class CustomerLoginSerializer(TokenObtainPairSerializer):
    # Customer-facing names remain usernames even though Django uses UUID internally.
    username_field = "username"

    default_error_messages = {
        "no_active_account": _("نام کاربری یا رمز عبور نادرست است یا حساب دسترسی فعال ندارد."),
    }

    def validate(self, attrs):
        request = self.context["request"]
        workspace = resolve_customer_workspace(request)
        user = authenticate(
            request=request, workspace=workspace,
            username=attrs["username"], password=attrs["password"],
        )
        if user is None:
            raise AuthenticationFailed(self.error_messages["no_active_account"])
        refresh = self.get_token(user)
        return {"refresh": str(refresh), "access": str(refresh.access_token)}


class CustomerRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            refresh = self.token_class(attrs["refresh"])
            # Reject missing/deleted users and inactive memberships before rotation.
            get_customer_for_token(refresh)
            return super().validate(attrs)
        except TokenError:
            raise InvalidToken(_("توکن تمدید نامعتبر یا منقضی شده است.")) from None


class CurrentUserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    workspace_id = serializers.UUIDField(read_only=True)
    workspace_name = serializers.CharField(source="workspace.name", read_only=True)

    class Meta:
        model = User
        fields = (
            "id", "username", "first_name", "last_name", "phone_number", "role",
            "role_display", "is_workspace_owner", "workspace_id", "workspace_name",
        )
        read_only_fields = fields
