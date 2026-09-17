from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import User


class StrictInputSerializer(serializers.Serializer):
    """Reject extra fields instead of silently ignoring privilege-related input."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            invalid = set(data) - set(self.fields)
            if invalid:
                raise serializers.ValidationError({
                    field: _("ارسال یا تغییر این فیلد مجاز نیست.") for field in sorted(invalid)
                })
        return super().to_internal_value(data)


class UserUpdateSerializer(StrictInputSerializer):
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, label=_("نام"))
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, label=_("نام خانوادگی"))
    phone_number = serializers.CharField(max_length=20, required=False, allow_blank=True, label=_("شماره موبایل"))
    is_active = serializers.BooleanField(required=False, label=_("فعال"))
    range_id = serializers.UUIDField(required=False, allow_null=True, label=_("رنج"))


class UserCreateSerializer(UserUpdateSerializer):
    username = serializers.CharField(max_length=150, label=_("نام کاربری"))
    password = serializers.CharField(write_only=True, trim_whitespace=False, label=_("رمز عبور"))
    role = serializers.ChoiceField(choices=User.Role.choices, label=_("نقش"))


class OrganizationalUserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    range = serializers.SerializerMethodField()
    managed_ranges = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id", "username", "first_name", "last_name", "phone_number", "role",
            "role_display", "is_active", "is_workspace_owner", "range", "managed_ranges",
        )
        read_only_fields = fields

    def get_range(self, user):
        membership = getattr(user, "range_membership", None)
        if (user.role == User.Role.CONSULTANT and membership
                and membership.workspace_id == user.workspace_id
                and membership.range.workspace_id == user.workspace_id):
            return {"id": str(membership.range_id), "name": membership.range.name}
        return None

    def get_managed_ranges(self, user):
        if user.role != User.Role.RANGE_MANAGER:
            return []
        return [{"id": str(team.pk), "name": team.name} for team in user.visible_managed_ranges]
