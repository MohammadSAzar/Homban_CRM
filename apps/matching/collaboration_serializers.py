from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from apps.accounts.user_serializers import StrictInputSerializer
from .live_serializers import FileCandidateSerializer, CustomerCandidateSerializer


class LiveReferenceSerializer(StrictInputSerializer):
    reference = serializers.CharField(max_length=2048, label=_("مرجع تطبیق"))


class CollaborationStatusSerializer(StrictInputSerializer):
    status = serializers.ChoiceField(choices=("seen", "accepted", "rejected"), label=_("وضعیت"))


def identity(user):
    return {"id": str(user.pk), "username": user.username, "first_name": user.first_name,
            "last_name": user.last_name, "role": user.role}


def collaboration_data(row):
    valid = row.is_valid
    return {
        "id": str(row.pk), "url": f"/api/v1/collaboration-requests/{row.pk}/",
        "manual_status": row.manual_status, "is_valid": valid,
        "requester": identity(row.requester), "recipient": identity(row.recipient),
        # Historical participants receive no current source data after invalidation.
        "property_file": FileCandidateSerializer(row.property_file).data if valid else None,
        "customer": CustomerCandidateSerializer(row.customer).data if valid else None,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }
