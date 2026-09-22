from django_filters import rest_framework as filters

from .models import Customer


class CustomerFilter(filters.FilterSet):
    assigned_to = filters.UUIDFilter(field_name="assigned_to_id")
    preferred_region = filters.UUIDFilter(field_name="preferred_regions__id", distinct=True)
    min_area = filters.NumberFilter(field_name="min_area", lookup_expr="gte")
    max_area = filters.NumberFilter(field_name="max_area", lookup_expr="lte")
    min_budget = filters.NumberFilter(field_name="budget", lookup_expr="gte")
    max_budget = filters.NumberFilter(field_name="budget", lookup_expr="lte")
    min_deposit_budget = filters.NumberFilter(field_name="deposit_budget", lookup_expr="gte")
    max_deposit_budget = filters.NumberFilter(field_name="deposit_budget", lookup_expr="lte")
    min_monthly_rent_budget = filters.NumberFilter(field_name="monthly_rent_budget", lookup_expr="gte")
    max_monthly_rent_budget = filters.NumberFilter(field_name="monthly_rent_budget", lookup_expr="lte")

    class Meta:
        model = Customer
        fields = ("customer_type", "status", "assigned_to", "preferred_region", "bedrooms", "budget_status", "is_valuable")
