from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.user_serializers import StrictInputSerializer
from .api_services import WRITE_FIELDS
from .models import PropertyFile, PropertyFileImage


class PropertyFileWriteSerializer(StrictInputSerializer, serializers.ModelSerializer):
    assigned_to = serializers.UUIDField(required=False, label=_("مشاور مسئول"))
    city = serializers.UUIDField(label=_("شهر"))
    region = serializers.UUIDField( label=_("منطقه"))
    valuable_reasons = serializers.ListField(child=serializers.CharField(max_length=150), required=False, max_length=100, label=_("دلایل ارزشمندی"))

    class Meta:
        model = PropertyFile
        fields = WRITE_FIELDS
        validators = []


class ConsultantSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name")


class PropertyFileListSerializer(serializers.ModelSerializer):
    assigned_to = ConsultantSummarySerializer(read_only=True)
    city_name = serializers.CharField(source="city.name", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    transaction_type_display = serializers.CharField(source="get_transaction_type_display", read_only=True)

    class Meta:
        model = PropertyFile
        fields = (
            "id", "code", "assigned_to", "transaction_type", "transaction_type_display",
            "status", "status_display", "city", "city_name", "region", "region_name",
            "area", "bedrooms", "building_age", "total_price", "price_per_square_meter",
            "deposit_amount", "monthly_rent", "source", "is_valuable", "created_at", "updated_at",
        )


class PropertyFileImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyFileImage
        fields = ("id", "reference", "sort_order", "created_at")


class PropertyFileDetailSerializer(PropertyFileListSerializer):
    images = PropertyFileImageSerializer(many=True, read_only=True)
    valuable_reasons = serializers.SlugRelatedField(many=True, read_only=True, slug_field="reason")

    class Meta(PropertyFileListSerializer.Meta):
        fields = PropertyFileListSerializer.Meta.fields + (
            "address", "owner_name", "owner_phone", "visit_contact_phone", "description",
            "total_floors", "units_per_floor", "unit_floor", "parking", "storage", "elevator",
            "balcony", "images", "valuable_reasons",
        )


class ImageWriteSerializer(StrictInputSerializer):
    reference = serializers.CharField(max_length=2048, label=_("مرجع تصویر"))


class ImageOrderSerializer(StrictInputSerializer):
    order = serializers.ListField(child=serializers.UUIDField(), label=_("ترتیب تصاویر"))
