# apps/ws_routing.py
from django.urls import re_path
from apps.instagram.consumers import InstagramConsumer  # реальный класс
from apps.instagram.ws_jwt import JWTAuthMiddleware
from apps.scale.consumers import AgentScaleConsumer

websocket_urlpatterns = [
    # Инстаграм: под JWT
    re_path(r"ws/instagram/?$", JWTAuthMiddleware(InstagramConsumer.as_asgi())),

    # Агенты: без JWT, свой токен в query string
    re_path(r"ws/agents/?$", AgentScaleConsumer.as_asgi()),
]
