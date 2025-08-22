import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

# 1) СНАЧАЛА инициализируем Django
django_asgi_app = get_asgi_application()

# 2) Потом импортируем всё, что тянет модели/consumers
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from apps.instagram.ws_jwt import JWTAuthMiddleware  # твой ASGI JWT-мидлвар
from apps.instagram import routing as ig_routing     # см. файл ниже

# 3) Собираем приложение
application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(ig_routing.websocket_urlpatterns)
        )
    ),
})
