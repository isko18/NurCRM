# apps/scales/routing.py
from django.urls import re_path
from .consumers import AgentScaleConsumer  # см. ниже

websocket_urlpatterns = [
    # простой URL без ID, всё будем определять по токену ?token=
    re_path(r"ws/agents/?$", AgentScaleConsumer.as_asgi()),
]
