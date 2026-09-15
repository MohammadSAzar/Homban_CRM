from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings


def customer_can_authenticate(user):
    """Customer access depends on current user and workspace state, never claims."""
    return bool(
        user is not None
        and user.is_active
        and user.workspace_id
        and user.workspace.is_active
    )


def get_customer_for_token(token):
    user_model = get_user_model()
    try:
        user = user_model.objects.select_related("workspace").get(
            pk=token[api_settings.USER_ID_CLAIM]
        )
    except (KeyError, ValueError, TypeError, ValidationError, user_model.DoesNotExist):
        raise AuthenticationFailed(_("اطلاعات احراز هویت معتبر نیست.")) from None
    if not customer_can_authenticate(user):
        raise AuthenticationFailed(_("دسترسی به حساب کاربری یا مجموعه فعال نیست."))
    return user


class CustomerJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        return get_customer_for_token(validated_token)
