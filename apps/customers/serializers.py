from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.user_serializers import StrictInputSerializer
from apps.locations.models import Region
from .api_services import WRITE_FIELDS
from .models import Customer


class CustomerWriteSerializer(StrictInputSerializer, serializers.ModelSerializer):
    assigned_to = serializers.UUIDField(required=False, label=_("مشاور مسئول"))
    preferred_regions = serializers.ListField(child=serializers.UUIDField(), required=False, label=_("مناطق مورد نظر"))
    valuable_reasons = serializers.ListField(child=serializers.CharField(max_length=150), required=False, max_length=100, label=_("دلایل ارزشمندی"))

    class Meta:
        model = Customer
        fields = WRITE_FIELDS
        validators = []


class ConsultantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name")


class CustomerListSerializer(serializers.ModelSerializer):
    assigned_to = ConsultantSummarySerializer(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    customer_type_display = serializers.CharField(source="get_customer_type_display", read_only=True)
    budget_status_display = serializers.CharField(source="get_budget_status_display", read_only=True, allow_null=True)

    class Meta:
        model = Customer
        fields = (
            "id", "code", "assigned_to", "name", "customer_type", "customer_type_display",
            "status", "status_display", "min_area", "max_area", "min_building_age",
            "max_building_age", "bedrooms", "budget", "budget_status", "budget_status_display",
            "deposit_budget", "monthly_rent_budget", "is_valuable", "created_at", "updated_at",
        )


class PreferredRegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Region
        fields = ("id", "name", "city", "is_active")


class CustomerDetailSerializer(CustomerListSerializer):
    preferred_regions = PreferredRegionSerializer(source="scoped_regions", many=True, read_only=True)
    valuable_reasons = serializers.SlugRelatedField(many=True, read_only=True, slug_field="reason")

    class Meta(CustomerListSerializer.Meta):
        fields = CustomerListSerializer.Meta.fields + ("mobile", "description", "preferred_regions", "valuable_reasons")
