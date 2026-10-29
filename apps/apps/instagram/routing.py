from django.urls import re_path
from .consumers import DirectConsumer

# Один pattern: /ws/instagram/<account_id>/  ИЛИ  /ws/instagram/<account_id>/thread/<thread_id>/
websocket_urlpatterns = [
    re_path(
        r"^ws/instagram/(?P<account_id>[0-9a-f-]{8}-[0-9a-f-]{4}-[0-9a-f-]{4}-[0-9a-f-]{4}-[0-9a-f-]{12})/"
        r"(?:thread/(?P<thread_id>\d+)/)?$",
        DirectConsumer.as_asgi(),
        name="ws-instagram"
    ),
]
