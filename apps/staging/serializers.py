from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from apps.accounts.user_serializers import StrictInputSerializer
from .models import StagedRecord


class ReviewInput(StrictInputSerializer):
    normalized_data = serializers.JSONField(required=False, label=_("داده آماده‌سازی‌شده"))
    validation_issues = serializers.JSONField(required=False, label=_("موارد نیازمند بررسی"))


class EmptyInput(StrictInputSerializer):
    pass


class StagingListSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()
    issue_count = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()

    def get_created_by(self, row):
        user = row.created_by
        if user is None or user.workspace_id != row.workspace_id:
            return None
        return {"id": str(user.pk), "username": user.username, "first_name": user.first_name, "last_name": user.last_name}

    def get_issue_count(self, row):
        return len(row.validation_issues)

    def get_target(self, row):
        target_id = row.converted_property_file_id or row.converted_customer_id
        return {"type": row.entity_type, "id": str(target_id)} if target_id else None

    class Meta:
        model = StagedRecord
        fields = ("id", "entity_type", "source_type", "status", "created_by", "created_at", "updated_at", "issue_count", "converted_at", "target")


class StagingDetailSerializer(StagingListSerializer):
    class Meta(StagingListSerializer.Meta):
        fields = StagingListSerializer.Meta.fields + ("raw_payload", "normalized_data", "validation_issues")
