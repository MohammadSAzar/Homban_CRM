from django.urls import path
from .consumers import ConversationConsumer
from .announcement_consumer import AnnouncementConsumer
from .general_group_consumer import GeneralGroupConsumer

websocket_urlpatterns = [
    path('ws/chat/general-group/', GeneralGroupConsumer.as_asgi()),
    path('ws/chat/announcements/', AnnouncementConsumer.as_asgi()),
    path('ws/chat/conversations/<uuid:pk>/', ConversationConsumer.as_asgi()),
]
