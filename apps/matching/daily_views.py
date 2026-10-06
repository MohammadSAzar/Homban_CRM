from django.core.exceptions import ValidationError as DomainValidationError
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from .collaboration_services import _actor
from .collaboration_serializers import CollaborationStatusSerializer
from .daily_serializers import DailyFilterSerializer, RecommendationStatusSerializer
from .daily_services import feed_page, matching_detail, collaboration_detail
from .recommendation_services import change_manual_status


class DailyTasksView(APIView):
    def get(self, request):
        serializer = DailyFilterSerializer(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        return feed_page(actor=request.user, filters=serializer.validated_data, request=request)


class DailyMatchingDetailView(APIView):
    def get(self, request, pk):
        return Response(matching_detail(actor=request.user, pk=pk))


class DailyMatchingStatusView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        serializer = RecommendationStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor = _actor(request.user)
        try:
            row = change_manual_status(actor=actor, recommendation_id=pk, status=serializer.validated_data['status'])
        except DomainValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages) from None
        return Response({'id': str(row.pk), 'item_type': 'match_recommendation', 'status': row.user_status})


class DailyCollaborationDetailView(APIView):
    def get(self, request, pk):
        return Response(collaboration_detail(actor=request.user, pk=pk))


class DailyCollaborationStatusView(APIView):
    def post(self, request, pk):
        serializer = CollaborationStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(collaboration_detail(actor=request.user, pk=pk, **serializer.validated_data))
