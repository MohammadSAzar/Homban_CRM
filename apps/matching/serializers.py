from rest_framework import serializers
from apps.accounts.user_serializers import StrictInputSerializer
from .models import MatchingProfile, SETTING_FIELDS


class MatchingProfileSerializer(StrictInputSerializer, serializers.ModelSerializer):
    class Meta:
        model = MatchingProfile
        fields = SETTING_FIELDS
        validators = []
