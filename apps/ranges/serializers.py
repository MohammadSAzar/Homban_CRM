from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.user_serializers import StrictInputSerializer
from .models import Range


class RangeWriteSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=100, label=_("نام رنج"))
    manager = serializers.UUIDField(required=False, allow_null=True, label=_("مدیر رنج"))
    activity_scope = serializers.ChoiceField(choices=Range.ActivityScope.choices, required=False, label=_("حوزه فعالیت"))
    regions = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True, label=_("مناطق"))
    is_active = serializers.BooleanField(required=False, default=True, label=_("فعال"))


class MembershipWriteSerializer(StrictInputSerializer):
    from_range = serializers.UUIDField(required=False, allow_null=True, label=_("رنج فعلی"))


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name")
        read_only_fields = fields


class RangeReadSerializer(serializers.ModelSerializer):
    manager = serializers.SerializerMethodField()
    activity_scope_display = serializers.CharField(source="get_activity_scope_display", read_only=True)
    regions = serializers.SerializerMethodField()
    consultant_count = serializers.IntegerField(read_only=True)
    has_region_constraints = serializers.BooleanField(read_only=True)

    class Meta:
        model = Range
        fields = ("id", "name", "manager", "activity_scope", "activity_scope_display", "is_active",
                  "regions", "consultant_count", "has_region_constraints")
        read_only_fields = fields

    def get_manager(self, team):
        if team.manager_id and team.manager.workspace_id == team.workspace_id:
            return UserSummarySerializer(team.manager).data
        return None

    def get_regions(self, team):
        return [{"id": str(region.pk), "name": region.name, "is_active": region.is_active}
                for region in team.visible_regions]
