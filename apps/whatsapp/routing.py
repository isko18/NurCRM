from django.urls import re_path
from apps.whatsapp import consumers

websocket_urlpatterns = [
    re_path(r"ws/wa/(?P<company_id>[0-9a-f-]+)/$", consumers.WhatsAppConsumer.as_asgi()),
]
