# проектный routing.py (например, в config/routing.py)
from django.urls import path
from apps.scale.consumers import AgentScaleConsumer

websocket_urlpatterns = [
    path("ws/agents/<uuid:agent_id>/", AgentScaleConsumer.as_asgi()),
]
