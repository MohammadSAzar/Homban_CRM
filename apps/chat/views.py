from django.db import transaction
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (EmptySerializer, OpenSerializer, PageSerializer, SendSerializer,
                          conversation_data, message_data)
from .services import (lock_actor, mark_conversation_read, open_conversation,
                       owned_conversation, send_message, summaries)


def validate(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class ChatPagination(PageNumberPagination):
    page_size = 50


class ConversationListView(APIView):
    @transaction.atomic
    def get(self, request):
        actor = lock_actor(request.user)
        validate(PageSerializer, request.query_params.dict())
        pagination = ChatPagination()
        page = pagination.paginate_queryset(summaries(actor), request)
        return pagination.get_paginated_response([conversation_data(row, actor) for row in page])

    @transaction.atomic
    def post(self, request):
        validate(EmptySerializer, request.query_params.dict())
        data = validate(OpenSerializer, request.data)
        row, created = open_conversation(actor=request.user, **data)
        return Response(conversation_data(summaries(request.user).get(pk=row.pk), request.user),
                        status=201 if created else 200)


class ConversationDetailView(APIView):
    @transaction.atomic
    def get(self, request, pk):
        actor = lock_actor(request.user)
        validate(EmptySerializer, request.query_params.dict())
        row = owned_conversation(actor, pk)
        return Response(conversation_data(summaries(actor).get(pk=row.pk), actor))


class MessageListView(APIView):
    @transaction.atomic
    def get(self, request, pk):
        actor = lock_actor(request.user)
        validate(PageSerializer, request.query_params.dict())
        row = owned_conversation(actor, pk)
        pagination = ChatPagination()
        page = pagination.paginate_queryset(row.messages.order_by('created_at', 'pk'), request)
        return pagination.get_paginated_response([message_data(message, actor) for message in page])

    def post(self, request, pk):
        validate(EmptySerializer, request.query_params.dict())
        data = validate(SendSerializer, request.data)
        row = send_message(actor=request.user, pk=pk, **data)
        return Response(message_data(row, request.user), status=201)


class ConversationReadView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        validate(EmptySerializer, request.query_params.dict())
        validate(EmptySerializer, request.data)
        row = mark_conversation_read(actor=request.user, pk=pk)
        return Response(conversation_data(summaries(request.user).get(pk=row.pk), request.user))
