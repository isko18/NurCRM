from django.urls import re_path
from .consumers import AgentScaleConsumer

websocket_urlpatterns = [
    re_path(r"^ws/agents/$", AgentScaleConsumer.as_asgi()),
]
