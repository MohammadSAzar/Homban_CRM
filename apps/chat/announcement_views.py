from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from .announcement_serializers import AnnouncementInput, PublisherInput, announcement_data, publisher_data
from .announcement_services import (delegate_publisher, list_announcements, list_publishers,
                                    publish_announcement, remove_publisher)
from .serializers import EmptySerializer, PageSerializer
from .views import ChatPagination, validate


class AnnouncementListView(APIView):
    @transaction.atomic
    def get(self, request):
        rows = list_announcements(request.user)
        validate(PageSerializer, request.query_params.dict())
        pagination = ChatPagination()
        page = pagination.paginate_queryset(rows, request)
        return pagination.get_paginated_response([announcement_data(row) for row in page])

    def post(self, request):
        validate(EmptySerializer, request.query_params.dict())
        data = validate(AnnouncementInput, request.data)
        row = publish_announcement(actor=request.user, **data)
        return Response(announcement_data(row), status=201)


class PublisherListView(APIView):
    @transaction.atomic
    def get(self, request):
        rows = list_publishers(request.user)
        validate(PageSerializer, request.query_params.dict())
        pagination = ChatPagination()
        page = pagination.paginate_queryset(rows, request)
        return pagination.get_paginated_response([publisher_data(row) for row in page])

    def post(self, request):
        validate(EmptySerializer, request.query_params.dict())
        data = validate(PublisherInput, request.data)
        row, created = delegate_publisher(actor=request.user, **data)
        return Response(publisher_data(row), status=201 if created else 200)


class PublisherDetailView(APIView):
    def delete(self, request, pk):
        validate(EmptySerializer, request.query_params.dict())
        validate(EmptySerializer, request.data)
        remove_publisher(actor=request.user, delegation_id=pk)
        return Response(status=204)


class AnnouncementTicketView(APIView):
    def post(self, request):
        validate(EmptySerializer, request.query_params.dict())
        validate(EmptySerializer, request.data)
        from .announcement_realtime import create_ticket
        response = Response({'ticket': create_ticket(actor=request.user)})
        response['Cache-Control'] = 'no-store'
        return response
