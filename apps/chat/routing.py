from django.urls import path
from .consumers import ConversationConsumer

websocket_urlpatterns = [
    path('ws/chat/conversations/<uuid:pk>/', ConversationConsumer.as_asgi()),
]
