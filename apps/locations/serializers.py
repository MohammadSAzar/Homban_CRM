from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.user_serializers import StrictInputSerializer
from apps.organizations.models import Workspace
from .models import City, Region


class CityWriteSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=100, label=_("نام شهر"))
    is_active = serializers.BooleanField(required=False, default=True, label=_("فعال"))


class RegionWriteSerializer(CityWriteSerializer):
    name = serializers.CharField(max_length=100, label=_("نام منطقه"))
    city = serializers.UUIDField(label=_("شهر"))


class CityReadSerializer(serializers.ModelSerializer):
    source_display = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = City
        fields = ("id", "name", "source", "source_display", "is_active")
        read_only_fields = fields


class RegionReadSerializer(serializers.ModelSerializer):
    city_name = serializers.CharField(source="city.name", read_only=True)
    source_display = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = Region
        fields = ("id", "city", "city_name", "name", "source", "source_display", "is_active")
        read_only_fields = fields


class RegionModeWriteSerializer(StrictInputSerializer):
    region_mode = serializers.ChoiceField(choices=Workspace.RegionMode.choices, label=_("نوع منطقه‌بندی"))


class RegionModeReadSerializer(serializers.ModelSerializer):
    region_mode_display = serializers.CharField(source="get_region_mode_display", read_only=True, allow_null=True)

    class Meta:
        model = Workspace
        fields = ("region_mode", "region_mode_display")
        read_only_fields = fields


class CityFilterSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False, label=_("فعال"))


class RegionFilterSerializer(CityFilterSerializer):
    city = serializers.UUIDField(required=False, label=_("شهر"))
