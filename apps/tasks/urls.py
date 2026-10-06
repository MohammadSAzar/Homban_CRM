from django.urls import path
from .views import TaskListCreateView, TaskDetailView, TaskStatusView, TaskReadView, CalendarTasksView

urlpatterns = [
    path('manual-tasks/', TaskListCreateView.as_view(), name='manual-task-list'),
    path('manual-tasks/<uuid:pk>/', TaskDetailView.as_view(), name='manual-task-detail'),
    path('manual-tasks/<uuid:pk>/status/', TaskStatusView.as_view(), name='manual-task-status'),
    path('calendar/tasks/', CalendarTasksView.as_view(), name='calendar-tasks'),
    path('daily-tasks/manual/<uuid:pk>/', TaskReadView.as_view(), name='daily-manual-detail'),
    path('daily-tasks/manual/<uuid:pk>/status/', TaskStatusView.as_view(), name='daily-manual-status'),
]
