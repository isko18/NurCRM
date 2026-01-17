# apps/cafe/routing.py
from django.urls import re_path
from .consumers import CafeOrderConsumer, CafeTableConsumer

websocket_urlpatterns = [
    re_path(
        r"^ws/cafe/orders/$",
        CafeOrderConsumer.as_asgi(),
        name="ws-cafe-orders"
    ),
    re_path(
        r"^ws/cafe/tables/$",
        CafeTableConsumer.as_asgi(),
        name="ws-cafe-tables"
    ),
]
