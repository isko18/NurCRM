import json
from channels.generic.websocket import AsyncWebsocketConsumer


class InstagramConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для Instagram DM
    """
    async def connect(self):
        self.company_id = self.scope["url_route"]["kwargs"]["company_id"]
        self.group_name = f"instagram_{self.company_id}"

        # подписываемся на группу
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        # отписываемся
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # сообщения от Python → WS
    async def ig_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "message",
            "id": event["id"],
            "user_id": event["user_id"],
            "text": event.get("text"),
            "direction": event.get("direction"),
            "ts": event.get("ts"),
        }))

    async def ig_status(self, event):
        await self.send(text_data=json.dumps({
            "type": "status",
            "status": event["status"],
            "username": event.get("username"),
        }))
