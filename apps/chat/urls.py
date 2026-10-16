from django.urls import path
from .views import AttachmentView, ConversationDetailView, ConversationListView, ConversationReadView, MessageListView, RealtimeTicketView
from .announcement_views import AnnouncementListView, PublisherListView, PublisherDetailView, AnnouncementTicketView

urlpatterns = [
    path('chat/announcements/realtime-ticket/', AnnouncementTicketView.as_view(), name='chat-announcement-ticket'),
    path('chat/announcements/', AnnouncementListView.as_view(), name='chat-announcements'),
    path('chat/announcement-publishers/', PublisherListView.as_view(), name='chat-announcement-publishers'),
    path('chat/announcement-publishers/<uuid:pk>/', PublisherDetailView.as_view(), name='chat-announcement-publisher'),
    path('chat/messages/<uuid:pk>/attachment/', AttachmentView.as_view(), name='chat-attachment'),
    path('chat/conversations/', ConversationListView.as_view(), name='chat-conversations'),
    path('chat/conversations/<uuid:pk>/', ConversationDetailView.as_view(), name='chat-detail'),
    path('chat/conversations/<uuid:pk>/messages/', MessageListView.as_view(), name='chat-messages'),
    path('chat/conversations/<uuid:pk>/read/', ConversationReadView.as_view(), name='chat-read'),
    path('chat/conversations/<uuid:pk>/realtime-ticket/', RealtimeTicketView.as_view(), name='chat-realtime-ticket'),
]
