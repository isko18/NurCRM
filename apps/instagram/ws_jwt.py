# apps/instagram/ws_jwt.py
from urllib.parse import parse_qs

from django.contrib.auth.models import AnonymousUser
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async

try:
    from rest_framework_simplejwt.authentication import JWTAuthentication
except Exception:
    JWTAuthentication = None


@database_sync_to_async
def _user_from_token(token: str):
    if not JWTAuthentication or not token:
        return AnonymousUser()
    try:
        auth = JWTAuthentication()
        validated = auth.get_validated_token(token)
        return auth.get_user(validated)
    except Exception:
        return AnonymousUser()


class JWTAuthMiddleware:
    """
    ASGI3-совместимый middleware для Channels 4:
    ожидает JWT в querystring: ?token=<JWT>
    """
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        # Клонируем scope, чтобы не мутировать внешний
        scope = dict(scope)
        qs = parse_qs(scope.get("query_string", b"").decode())
        token = (qs.get("token") or [None])[0]
        scope["user"] = await _user_from_token(token)
        return await self.inner(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    # Сочетаем с cookie-AuthMiddlewareStack, чтобы не ломать поведение,
    # если вдруг нет токена — будут работать сессии через cookies.
    return JWTAuthMiddleware(AuthMiddlewareStack(inner))
