import os
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator

from core.ws_jwt import JWTAuthMiddleware
from apps.instagram import routing as ig_routing
from apps.scale import ws_routing as scale_ws_routing
from apps.cafe import routing as cafe_routing
from apps.consalting import routing as consalting_routing
from apps.main import routing as main_routing

# один общий список всех WS-маршрутов
websocket_urlpatterns = (
    ig_routing.websocket_urlpatterns
    + scale_ws_routing.websocket_urlpatterns
    + cafe_routing.websocket_urlpatterns
    + consalting_routing.websocket_urlpatterns
    + main_routing.websocket_urlpatterns
)


def _ws_allowed_origins():
    """Разрешённые Origin для WebSocket.

    WS-аутентификация выполняется по JWT в query/Authorization (не по кукам),
    поэтому CSWSH неактуален и привязывать origin к ALLOWED_HOSTS не нужно — это
    ломало и браузерные подключения с фронт-домена, и клиенты без заголовка
    Origin (мобильные/нативные приложения, тест-инструменты), которые
    OriginValidator со списком (без "*") отклоняет с 403.

    По умолчанию разрешаем все origin (защита — JWT). Ограничить можно через env:
      DJANGO_WS_ALLOWED_ORIGINS="https://app.nurcrm.kg,https://nurcrm.kg"
    """
    env_val = os.getenv("DJANGO_WS_ALLOWED_ORIGINS")
    if env_val:
        origins = [o.strip() for o in env_val.split(",") if o.strip()]
        return origins or ["*"]
    return ["*"]


application = ProtocolTypeRouter({
    "http": ASGIStaticFilesHandler(django_asgi_app),
    "websocket": OriginValidator(
        JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        ),
        _ws_allowed_origins(),
    ),
})
