from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.user_serializers import StrictInputSerializer
from .collaboration_serializers import LiveReferenceSerializer, CollaborationStatusSerializer
from .collaboration_services import create_from_live, create_from_recommendation, open_request


class RecommendationCollaborationView(APIView):
    def post(self, request, pk):
        serializer = StrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = create_from_recommendation(actor=request.user, recommendation_id=pk)
        return Response(result, status=201 if result["created"] else 200)


class LiveCollaborationView(APIView):
    def post(self, request):
        serializer = LiveReferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = create_from_live(actor=request.user, **serializer.validated_data)
        return Response(result, status=201 if result["created"] else 200)


class CollaborationDetailView(APIView):
    def get(self, request, pk):
        return Response(open_request(actor=request.user, request_id=pk))


class CollaborationStatusView(APIView):
    def post(self, request, pk):
        serializer = CollaborationStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(open_request(actor=request.user, request_id=pk, **serializer.validated_data))
