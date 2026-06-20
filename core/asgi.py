import os
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

django_asgi_app = get_asgi_application()

from django.conf import settings
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator

from core.ws_jwt import JWTAuthMiddleware
from apps.instagram import routing as ig_routing
from apps.scale import ws_routing as scale_ws_routing
from apps.cafe import routing as cafe_routing
from apps.consalting import routing as consalting_routing

# один общий список всех WS-маршрутов
websocket_urlpatterns = (
    ig_routing.websocket_urlpatterns
    + scale_ws_routing.websocket_urlpatterns
    + cafe_routing.websocket_urlpatterns
    + consalting_routing.websocket_urlpatterns
)


def _ws_allowed_origins():
    """Разрешённые Origin для WebSocket.

    WS-аутентификация выполняется по JWT в query/Authorization (не по кукам),
    поэтому CSWSH не актуален и привязывать origin строго к ALLOWED_HOSTS не нужно
    — это ломало браузерные подключения с фронт-домена. Берём те же origin, что
    разрешены для HTTP (CORS), плюс хосты ALLOWED_HOSTS. Управляется env:
      DJANGO_WS_ALLOWED_ORIGINS="https://app.nurcrm.kg,https://nurcrm.kg" или "*".
    """
    env_val = os.getenv("DJANGO_WS_ALLOWED_ORIGINS")
    if env_val:
        return [o.strip() for o in env_val.split(",") if o.strip()]

    if getattr(settings, "CORS_ORIGIN_ALLOW_ALL", False):
        return ["*"]

    origins = []
    origins += list(getattr(settings, "CORS_ALLOWED_ORIGINS", []) or [])
    origins += list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
    # bare-хосты из ALLOWED_HOSTS (OriginValidator понимает и хост без схемы)
    origins += [h for h in (getattr(settings, "ALLOWED_HOSTS", []) or []) if h and h != "*"]

    # уникализируем, сохраняя порядок
    seen, result = set(), []
    for o in origins:
        if o not in seen:
            seen.add(o)
            result.append(o)
    return result or ["*"]


application = ProtocolTypeRouter({
    "http": ASGIStaticFilesHandler(django_asgi_app),
    "websocket": OriginValidator(
        JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        ),
        _ws_allowed_origins(),
    ),
})
