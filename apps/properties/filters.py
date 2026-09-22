from django_filters import rest_framework as filters

from .models import PropertyFile


class PropertyFileFilter(filters.FilterSet):
    assigned_to = filters.UUIDFilter(field_name="assigned_to_id")
    city = filters.UUIDFilter(field_name="city_id")
    region = filters.UUIDFilter(field_name="region_id")
    min_area = filters.NumberFilter(field_name="area", lookup_expr="gte")
    max_area = filters.NumberFilter(field_name="area", lookup_expr="lte")
    min_total_price = filters.NumberFilter(field_name="total_price", lookup_expr="gte")
    max_total_price = filters.NumberFilter(field_name="total_price", lookup_expr="lte")

    class Meta:
        model = PropertyFile
        fields = ("transaction_type", "status", "assigned_to", "city", "region", "bedrooms", "source", "is_valuable")
