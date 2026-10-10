from django.urls import path
from .views import AttachmentView, ConversationDetailView, ConversationListView, ConversationReadView, MessageListView, RealtimeTicketView

urlpatterns = [
    path('chat/messages/<uuid:pk>/attachment/', AttachmentView.as_view(), name='chat-attachment'),
    path('chat/conversations/', ConversationListView.as_view(), name='chat-conversations'),
    path('chat/conversations/<uuid:pk>/', ConversationDetailView.as_view(), name='chat-detail'),
    path('chat/conversations/<uuid:pk>/messages/', MessageListView.as_view(), name='chat-messages'),
    path('chat/conversations/<uuid:pk>/read/', ConversationReadView.as_view(), name='chat-read'),
    path('chat/conversations/<uuid:pk>/realtime-ticket/', RealtimeTicketView.as_view(), name='chat-realtime-ticket'),
]
