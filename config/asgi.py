import os

from django.core.asgi import get_asgi_application


os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings.production",
)

django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from django.conf import settings
from apps.chat.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    'http': django_application,
    'websocket': OriginValidator(URLRouter(websocket_urlpatterns), settings.CHAT_WEBSOCKET_ORIGINS),
})
