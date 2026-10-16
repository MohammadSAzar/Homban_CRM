from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from .general_group_serializers import (GeneralGroupInput, GeneralGroupSettingsInput,
                                        general_group_message_data)
from .general_group_services import (general_group_state, set_general_group_enabled,
                                     list_general_group_messages, send_general_group_message)
from .serializers import EmptySerializer, PageSerializer
from .views import ChatPagination, validate


class GeneralGroupView(APIView):
    @transaction.atomic
    def get(self, request):
        rows = list_general_group_messages(request.user)
        validate(PageSerializer, request.query_params.dict())
        pagination = ChatPagination()
        page = pagination.paginate_queryset(rows, request)
        return pagination.get_paginated_response([general_group_message_data(row) for row in page])

    def post(self, request):
        validate(EmptySerializer, request.query_params.dict())
        data = validate(GeneralGroupInput, request.data)
        row = send_general_group_message(actor=request.user, **data)
        return Response(general_group_message_data(row), status=201)


class GeneralGroupSettingsView(APIView):
    def get(self, request):
        validate(EmptySerializer, request.query_params.dict())
        return Response(general_group_state(request.user))

    def patch(self, request):
        validate(EmptySerializer, request.query_params.dict())
        data = validate(GeneralGroupSettingsInput, request.data)
        return Response(set_general_group_enabled(actor=request.user, **data))
