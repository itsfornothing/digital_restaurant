"""
ASGI entry point for the Restaurant Platform.

Handles HTTP requests via Django's standard ASGI application and WebSocket
connections via Django Channels with the Redis channel layer.
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

# Initialize Django ASGI application early so that AppRegistry is populated
# before importing consumers or routing that reference models.
django_asgi_app = get_asgi_application()

from apps.notifications import routing  # noqa: E402 — must come after Django setup

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                URLRouter(routing.websocket_urlpatterns)
            )
        ),
    }
)
