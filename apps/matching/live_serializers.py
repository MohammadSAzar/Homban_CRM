from dataclasses import asdict
from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.accounts.user_serializers import StrictInputSerializer
from apps.customers.models import Customer
from apps.properties.models import PropertyFile
from .serializers import MatchingProfileSerializer


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid")
        return data


class HardConstraintsSerializer(StrictInputSerializer):
    area = StrictBooleanField(required=False, label=_("خط قرمز مساحت"))
    bedrooms = StrictBooleanField(required=False, label=_("خط قرمز اتاق خواب"))
    building_age = StrictBooleanField(required=False, label=_("خط قرمز سن بنا"))
    region = StrictBooleanField(required=False, label=_("خط قرمز منطقه"))
    parking = StrictBooleanField(required=False, label=_("خط قرمز پارکینگ"))
    elevator = StrictBooleanField(required=False, label=_("خط قرمز آسانسور"))
    storage = StrictBooleanField(required=False, label=_("خط قرمز انباری"))
    balcony = StrictBooleanField(required=False, label=_("خط قرمز بالکن"))


class LiveMatchingRequestSerializer(StrictInputSerializer):
    overrides = MatchingProfileSerializer(required=False)
    hard_constraints = HardConstraintsSerializer(required=False)


class LivePageSerializer(StrictInputSerializer):
    page = serializers.IntegerField(min_value=1, required=False, default=1, label=_("صفحه"))


class FileCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyFile
        fields = ("id", "code", "transaction_type", "city", "region", "area", "bedrooms",
                  "building_age", "parking", "elevator", "storage", "balcony", "total_price",
                  "price_per_square_meter", "deposit_amount", "monthly_rent")
        read_only_fields = fields


class CustomerCandidateSerializer(serializers.ModelSerializer):
    preferred_regions = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ("id", "code", "customer_type", "min_area", "max_area", "bedrooms",
                  "min_building_age", "max_building_age", "all_regions", "preferred_regions",
                  "budget", "deposit_budget", "monthly_rent_budget")
        read_only_fields = fields


def matching_result_data(result):
    """Keep 50-digit engine Decimals as strings, never renderer-converted floats."""
    def primitive(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {key: primitive(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [primitive(item) for item in value]
        return value
    return primitive(asdict(result))
