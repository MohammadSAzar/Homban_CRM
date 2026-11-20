"""Adapter to existing canonical input, scope, validation and creation."""
from apps.properties.serializers import PropertyFileWriteSerializer
from apps.properties.api_services import validate_file_creation, save_file


def prepare(actor, normalized_data):
    serializer = PropertyFileWriteSerializer(data=normalized_data)
    serializer.is_valid(raise_exception=True)
    validate_file_creation(actor=actor, data=serializer.validated_data)
    return serializer.validated_data


def create(actor, data):
    return save_file(actor=actor, data=data)
