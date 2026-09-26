from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import MatchingProfileSerializer
from .services import own_profile


class MatchingProfileView(APIView):
    def get(self, request):
        return Response(MatchingProfileSerializer(own_profile(actor=request.user)).data)

    def patch(self, request):
        serializer = MatchingProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profile = own_profile(actor=request.user, data=serializer.validated_data)
        return Response(MatchingProfileSerializer(profile).data)


class MatchingProfileResetView(APIView):
    def post(self, request):
        # An empty strict serializer also rejects non-object request bodies.
        from apps.accounts.user_serializers import StrictInputSerializer
        serializer = StrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = own_profile(actor=request.user, data=serializer.validated_data, reset=True)
        return Response(MatchingProfileSerializer(profile).data)
