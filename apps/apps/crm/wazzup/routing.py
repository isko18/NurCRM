from django.urls import re_path
from .consumers import WazzupChatConsumer

websocket_urlpatterns = [
    re_path(r"^ws/wazzup/$", WazzupChatConsumer.as_asgi()),
    re_path(r"^ws/wazzup/chat/(?P<chat_id>[^/]+)/$", WazzupChatConsumer.as_asgi()),
]
