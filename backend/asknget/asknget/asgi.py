"""
ASGI entry point: HTTP (the same Django app gunicorn serves) and WebSockets (the live bell, `/ws/notifications`).

Served by uvicorn in the `realtime` service (compose.yaml); nginx sends `/ws/` there. Vercel keeps using WSGI and has no
WebSockets; the frontend polls there instead.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'asknget.settings')

django_app = get_asgi_application()        # set Django up before importing anything that uses models

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import OriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402
from django.urls import path  # noqa: E402

from notifications.consumers import BellConsumer  # noqa: E402

# Browsers send Origin on a WebSocket; accept the API's own hosts (ALLOWED_HOSTS patterns, e.g. ".asknget.com") and the
# frontend origins CORS allows (e.g. http://localhost:3000).
allowed_origins = [*settings.ALLOWED_HOSTS, *settings.CORS_ALLOWED_ORIGINS]

application = ProtocolTypeRouter({
    "http": django_app,
    "websocket": OriginValidator(URLRouter([path("ws/notifications", BellConsumer.as_asgi())]), allowed_origins),
})
