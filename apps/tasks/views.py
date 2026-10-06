from django.core.exceptions import ValidationError as DomainError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import ManualTask
from .serializers import TaskWriteSerializer, TaskStatusSerializer, TaskFilterSerializer, task_data
from .services import lock_actor, owned_task, create_task, update_task, change_status


class TaskPagination(PageNumberPagination):
    page_size = 50


class TaskView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, DomainError):
            exc = ValidationError(exc.message_dict if hasattr(exc,'message_dict') else exc.messages)
        return super().handle_exception(exc)


class TaskListView(TaskView):
    calendar = False

    @transaction.atomic
    def get(self, request):
        actor = lock_actor(request.user)
        filters = TaskFilterSerializer(data=request.query_params.dict(), context={'calendar':self.calendar})
        filters.is_valid(raise_exception=True)
        data = filters.validated_data
        rows = ManualTask.objects.for_owner(actor).order_by('scheduled_for','id')
        if data['status'] != 'all':
            rows = rows.filter(status=data['status'])
        if 'start' in data:
            rows = rows.filter(scheduled_for__gte=data['start'])
        if 'end' in data:
            rows = rows.filter(scheduled_for__lt=data['end'])
        pagination = TaskPagination()
        page = pagination.paginate_queryset(rows,request)
        now = timezone.now()
        return pagination.get_paginated_response([task_data(row, now=now) for row in page])


class TaskListCreateView(TaskListView):
    def post(self, request):
        serializer = TaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(task_data(create_task(actor=request.user,data=serializer.validated_data)), status=201)


class CalendarTasksView(TaskListView):
    calendar = True


class TaskReadView(TaskView):
    @transaction.atomic
    def get(self, request, pk):
        actor = lock_actor(request.user)
        return Response(task_data(owned_task(actor,pk)))


class TaskDetailView(TaskReadView):
    def patch(self, request, pk):
        serializer = TaskWriteSerializer(data=request.data,partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(task_data(update_task(actor=request.user,pk=pk,data=serializer.validated_data)))


class TaskStatusView(TaskView):
    def post(self, request, pk):
        serializer = TaskStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(task_data(change_status(actor=request.user,pk=pk,status=serializer.validated_data['status'])))
