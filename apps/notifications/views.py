from django.db import transaction
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Notification
from .serializers import EmptySerializer, NotificationFilterSerializer, notification_data
from .services import lock_recipient, owned_notification, set_read, read_all


def empty(data):
    serializer = EmptySerializer(data=data)
    serializer.is_valid(raise_exception=True)


class NotificationPagination(PageNumberPagination):
    page_size = 50


class NotificationListView(APIView):
    @transaction.atomic
    def get(self, request):
        actor = lock_recipient(request.user)
        serializer = NotificationFilterSerializer(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data
        rows = Notification.objects.for_recipient(actor).order_by('-created_at','-id')
        if filters['status'] != 'all':
            rows = rows.filter(read_at__isnull=filters['status'] == 'unread')
        if 'kind' in filters:
            rows = rows.filter(kind=filters['kind'])
        pagination = NotificationPagination()
        page = pagination.paginate_queryset(rows, request)
        return pagination.get_paginated_response([notification_data(row) for row in page])


class NotificationDetailView(APIView):
    @transaction.atomic
    def get(self, request, pk):
        empty(request.query_params.dict())
        actor = lock_recipient(request.user)
        return Response(notification_data(owned_notification(actor, pk)))


class NotificationReadView(APIView):
    read = True

    def post(self, request, pk):
        empty(request.query_params.dict())
        empty(request.data)
        return Response(notification_data(set_read(actor=request.user, pk=pk, read=self.read)))


class NotificationUnreadView(NotificationReadView):
    read = False


class NotificationReadAllView(APIView):
    def post(self, request):
        empty(request.query_params.dict())
        empty(request.data)
        return Response({'affected_count': read_all(actor=request.user)})


class NotificationCountView(APIView):
    @transaction.atomic
    def get(self, request):
        empty(request.query_params.dict())
        actor = lock_recipient(request.user)
        count = Notification.objects.for_recipient(actor).filter(read_at__isnull=True).count()
        return Response({'unread_count': count})
