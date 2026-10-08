from django.urls import path
from .views import ConversationDetailView, ConversationListView, ConversationReadView, MessageListView

urlpatterns = [
    path('chat/conversations/', ConversationListView.as_view(), name='chat-conversations'),
    path('chat/conversations/<uuid:pk>/', ConversationDetailView.as_view(), name='chat-detail'),
    path('chat/conversations/<uuid:pk>/messages/', MessageListView.as_view(), name='chat-messages'),
    path('chat/conversations/<uuid:pk>/read/', ConversationReadView.as_view(), name='chat-read'),
]
