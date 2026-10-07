from django.urls import path
from .views import (NotificationListView, NotificationDetailView, NotificationReadView,
                    NotificationUnreadView, NotificationReadAllView, NotificationCountView)

urlpatterns = [
    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/read-all/', NotificationReadAllView.as_view(), name='notification-read-all'),
    path('notifications/unread-count/', NotificationCountView.as_view(), name='notification-count'),
    path('notifications/<uuid:pk>/', NotificationDetailView.as_view(), name='notification-detail'),
    path('notifications/<uuid:pk>/read/', NotificationReadView.as_view(), name='notification-read'),
    path('notifications/<uuid:pk>/unread/', NotificationUnreadView.as_view(), name='notification-unread'),
]
