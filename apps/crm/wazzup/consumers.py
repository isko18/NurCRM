import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class WazzupChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для Wazzup чатов и уведомлений в реальном времени
    Маршрут: ws/wazzup/ или ws/wazzup/chat/<chat_id>/
    """

    async def connect(self):
        self.user = self.scope.get("user")
        self.chat_id = self.scope["url_route"]["kwargs"].get("chat_id")

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        company_id = getattr(self.user, "company_id", None)
        if not company_id:
            await self.close(code=4002)
            return

        # Группа для компании
        self.company_group = f"wazzup_company_{company_id}"
        await self.channel_layer.group_add(self.company_group, self.channel_name)

        # Группа для конкретного чата
        if self.chat_id:
            self.chat_group = f"wazzup_chat_{self.chat_id}"
            await self.channel_layer.group_add(self.chat_group, self.channel_name)
        else:
            self.chat_group = None

        await self.accept()
        logger.info(f"WebSocket connected for user {self.user} in group {self.company_group}")

    async def disconnect(self, close_code):
        if hasattr(self, "company_group") and self.company_group:
            await self.channel_layer.group_discard(self.company_group, self.channel_name)
        if hasattr(self, "chat_group") and self.chat_group:
            await self.channel_layer.group_discard(self.chat_group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
            action = data.get("action")
            # Пинг/Понг для сокета
            if action == "ping":
                await self.send(text_data=json.dumps({"action": "pong"}))
        except Exception as e:
            logger.error(f"Error handling WS message: {e}")

    async def wazzup_event(self, event):
        """
        Пересылка события клиенту
        """
        await self.send(text_data=json.dumps(event.get("event", {})))
