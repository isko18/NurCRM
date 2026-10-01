# apps/scale/consumers.py
import json
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
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

        # ищем компанию по токену (через sync_to_async)
        try:
            company = await sync_to_async(Company.objects.get)(
                scale_api_token=token
            )
        except Company.DoesNotExist:
            await self.close(code=4002)
            return

        self.company_id = str(company.id)
        self.group_name = f"scale_company_{self.company_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send(
            json.dumps({"type": "hello", "company_id": self.company_id})
        )

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )

    async def receive(self, text_data=None, bytes_data=None):
        """
        Сообщения ОТ агента (к серверу).
        Здесь ловим результат загрузки ПЛУ.
        """
        if not text_data:
            return

        try:
            data = json.loads(text_data)
        except Exception:
            # можно залогировать, но не роняем соединение
            return

        action = data.get("action")

        # агент прислал результат загрузки ПЛУ
        if action == "plu_batch_result":
            items = data.get("items") or []

            # тут можно:
            # - сохранить результат в БД
            # - разослать во фронт через другую группу
            # сейчас просто шлём в ту же группу как event,
            # чтобы фронт-клиенты (если подключатся) могли отобразить.
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "plu_batch_result_event",
                    "payload": {
                        "company_id": self.company_id,
                        "items": items,
                    },
                },
            )
            return

        # остальные action пока игнорируем

    async def send_scale_payload(self, event):
        """
        Сервер -> агент (и потенциально другие клиенты этой группы).
        Используется send_products_to_scale (type="send_scale_payload").
        """
        payload = event.get("payload") or {}
        await self.send(json.dumps(payload))

    async def plu_batch_result_event(self, event):
        """
        Результат от агента, проброшенный в группу.
        Можно слушать тем же URL с другого клиента (например фронт).
        """
        payload = event.get("payload") or {}
        await self.send(json.dumps(payload))
