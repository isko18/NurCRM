import os
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from apps.instagram.ws_jwt import JWTAuthMiddleware
from apps.instagram import routing as ig_routing
from apps.scale import routing as main_routing  # <--- ДОБАВИЛИ

# Объединяем все websocket-маршруты:
all_ws_patterns = ig_routing.websocket_urlpatterns + main_routing.websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": ASGIStaticFilesHandler(django_asgi_app),
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(all_ws_patterns)
        )
    ),
})
