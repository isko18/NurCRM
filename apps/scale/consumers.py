# apps/scales/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from urllib.parse import parse_qs
from django.contrib.auth import get_user_model
from apps.main.models import Company  # подставь свою модель компании, если она в другом месте

User = get_user_model()


class AgentScaleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """
        Ждём ?token=<scale_api_token> в query string.
        По нему ищем company и вешаемся на группу этой компании.
        """
        qs = self.scope.get("query_string", b"").decode()
        params = parse_qs(qs)
        token_list = params.get("token") or []
        token = token_list[0] if token_list else None

        if not token:
            await self.close(code=4001)
            return

        # ищем компанию по токену
        try:
            company = Company.objects.get(scale_token=token)
        except Company.DoesNotExist:
            await self.close(code=4002)
            return

        self.company_id = str(company.id)
        self.group_name = f"scale_company_{self.company_id}"

        # подписываемся на группу компании
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # можно отправить привет
        await self.send_json({"type": "hello", "company_id": self.company_id})

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        """
        Агента можно слушать, но пока просто логика-заглушка.
        """
        # если нужно что-то особенное от агента — парси тут
        return

    # метод для отправки данных из group_send
    async def send_scale_payload(self, event):
        """
        event: {"type": "send_scale_payload", "payload": {...}}
        """
        payload = event.get("payload") or {}
        await self.send(json.dumps(payload))
