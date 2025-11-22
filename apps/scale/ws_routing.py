# apps/scale/ws_routing.py
from django.urls import re_path
from .consumers import AgentScaleConsumer  # наш consumer для агентов/весов

websocket_urlpatterns = [
    re_path(r"^ws/agents/$", AgentScaleConsumer.as_asgi()),
]
