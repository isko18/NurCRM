# apps/instagram/ws_jwt.py
from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken

class JWTAuthMiddleware:
    """
    Прокидывает user в scope["user"] по JWT в query string (?token=...) или заголовках.
    Для /ws/agents/ — JWT не нужен, пропускаем как есть.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") or ""

        # 🔥 ВАЖНО: для /ws/agents/ ничего не проверяем, сразу пропускаем
        if path.startswith("/ws/agents/"):
            return await self.inner(scope, receive, send)

        # --- дальше твоя старая логика для инсты/чата ---
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)

        token = None

        # пример: ws://.../ws/instagram/?token=<JWT>
        if "token" in params:
            token = params["token"][0]

        # либо из headers (если так делаешь с фронта)
        if not token:
            for name, value in scope.get("headers", []):
                if name.lower() == b"authorization":
                    # "Bearer xxx"
                    auth_val = value.decode()
                    if auth_val.lower().startswith("bearer "):
                        token = auth_val.split(" ", 1)[1].strip()
                    break

        if not token:
            scope["user"] = AnonymousUser()
            return await self.inner(scope, receive, send)

        try:
            access = AccessToken(token)
            user_id = access["user_id"]
        except Exception:
            scope["user"] = AnonymousUser()
            return await self.inner(scope, receive, send)

        from django.contrib.auth import get_user_model
        User = get_user_model()

        @database_sync_to_async
        def get_user(uid):
            try:
                return User.objects.get(id=uid)
            except User.DoesNotExist:
                return AnonymousUser()

        scope["user"] = await get_user(user_id)
        return await self.inner(scope, receive, send)
