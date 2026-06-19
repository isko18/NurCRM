# apps/consalting/routing.py
from django.urls import re_path
from .consumers import ConsaltingFunnelConsumer

websocket_urlpatterns = [
    re_path(
        r"^ws/consalting/funnel/$",
        ConsaltingFunnelConsumer.as_asgi(),
        name="ws-consalting-funnel",
    ),
]
