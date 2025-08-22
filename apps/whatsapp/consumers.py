import json
from channels.generic.websocket import AsyncWebsocketConsumer


class WhatsAppConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.company_id = self.scope["url_route"]["kwargs"]["company_id"]
        self.group_name = f"wa_{self.company_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def wa_qr(self, event):
        await self.send(text_data=json.dumps({"type": "qr", "qr": event["qr"]}))

    async def wa_status(self, event):
        await self.send(text_data=json.dumps({"type": "status", "status": event["status"]}))

    async def wa_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "message",
            "id": event["id"],
            "phone": event["phone"],
            "text": event["text"],
            "caption": event.get("caption"),
            "direction": event["direction"],
            "ts": event["ts"],
        }))
