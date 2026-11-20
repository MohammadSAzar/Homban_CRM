"""Adapter to existing canonical input, scope, validation and creation."""
from apps.customers.serializers import CustomerWriteSerializer
from apps.customers.api_services import validate_customer_creation, save_customer


def prepare(actor, normalized_data):
    serializer = CustomerWriteSerializer(data=normalized_data)
    serializer.is_valid(raise_exception=True)
    validate_customer_creation(actor=actor, data=serializer.validated_data)
    return serializer.validated_data


def create(actor, data):
    return save_customer(actor=actor, data=data)
