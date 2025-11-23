import json
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async

from apps.main.models import Company


class AgentScaleConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """
        Ждём ?token=<scale_api_token> в query string.
        """
        qs = self.scope.get("query_string", b"").decode()
        params = parse_qs(qs)
        token_list = params.get("token") or []
        token = token_list[0] if token_list else None

        if not token:
            await self.close(code=4001)
            return

        # ищем компанию по токену (ВАЖНО: через sync_to_async)
        company = await sync_to_async(self._get_company_by_token)(token)
        if company is None:
            await self.close(code=4002)
            return

        self.company = company
        self.company_id = str(company.id)
        self.group_name = f"scale_company_{self.company_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"type": "hello", "company_id": self.company_id})

    def _get_company_by_token(self, token: str):
        try:
            return Company.objects.get(scale_api_token=token)
        except Company.DoesNotExist:
            return None

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Если захочешь — тут можно принимать от агента статус, логи и т.д.
        return

    async def send_scale_payload(self, event):
        """
        event: {"type": "send_scale_payload", "payload": {...}}
        """
        payload = event.get("payload") or {}
        await self.send(json.dumps(payload))

    async def send_json(self, data: dict):
        await super().send(text_data=json.dumps(data))
