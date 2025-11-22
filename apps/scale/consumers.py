# apps/scales/consumers.py
import json
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from apps.main.models import Company


class AgentScaleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        qs = self.scope.get("query_string", b"").decode()
        params = parse_qs(qs)
        token_list = params.get("token") or []
        token = token_list[0] if token_list else None

        if not token:
            await self.close(code=4001)
            return

        company = await self.get_company_by_token(token)
        if not company:
            await self.close(code=4002)
            return

        self.company_id = str(company.id)
        self.group_name = f"scale_company_{self.company_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send_json({"type": "hello", "company_id": self.company_id})

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        return

    async def send_scale_payload(self, event):
        await self.send(json.dumps(event.get("payload") or {}))

    # === ВАЖНО: асинхронный доступ к базе ===
    @database_sync_to_async
    def get_company_by_token(self, token):
        try:
            return Company.objects.get(scale_api_token=token)
        except Company.DoesNotExist:
            return None
        except Company.MultipleObjectsReturned:
            return None
