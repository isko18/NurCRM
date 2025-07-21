from django.urls import path
from apps.whatsapp.consumers import WazzupConsumer

websocket_urlpatterns = [
    path("ws/wazzup/", WazzupConsumer.as_asgi()),
]
