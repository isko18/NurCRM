import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .services import (
    send_message,
    edit_message,
    delete_message,
    get_channel_id_by_plain_id,
    get_first_active_channel_id
)


class WazzupConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        await self.send_json({
            "event": "connected",
            "message": "WebSocket соединение установлено"
        })

    async def disconnect(self, close_code):
        pass

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError as e:
            await self.send_json({"event": "error", "error": f"Неверный JSON: {str(e)}"})
            return

        command = data.get("command")

        if command == "send_message":
            await self.handle_send(data)
        elif command == "edit_message":
            await self.handle_edit(data)
        elif command == "delete_message":
            await self.handle_delete(data)
        else:
            await self.send_json({"event": "error", "error": "Неизвестная команда"})

    async def handle_send(self, data):
        try:
            transport = data.get("transport", "whatsapp")
            channel_id = data.get("channel_id")
            plain_id = data.get("plain_id")  # <-- заменено с channel_number

            if not channel_id:
                if plain_id:
                    channel_id = get_channel_id_by_plain_id(plain_id, transport)
                else:
                    channel_id = get_first_active_channel_id(transport)

            result = send_message(
                channel_id=channel_id,
                plain_id=plain_id,
                transport=transport,
                chat_type=data.get("chat_type", transport),
                chat_id=data.get("chat_id"),
                phone=data.get("phone"),
                username=data.get("username"),
                text=data.get("text"),
                content_uri=data.get("content_uri"),
                crm_user_id=data.get("crm_user_id"),
                crm_message_id=data.get("crm_message_id"),
                ref_message_id=data.get("ref_message_id"),
                buttons=data.get("buttons"),
                clear_unanswered=data.get("clear_unanswered", True)
            )
            await self.send_json({"event": "send_message", "result": result})
        except Exception as e:
            await self.send_json({"event": "send_message", "error": str(e)})

    async def handle_edit(self, data):
        try:
            result = edit_message(
                message_id=data.get("message_id"),
                text=data.get("text"),
                content_uri=data.get("content_uri"),
                crm_user_id=data.get("crm_user_id")
            )
            await self.send_json({"event": "edit_message", "result": result})
        except Exception as e:
            await self.send_json({"event": "edit_message", "error": str(e)})

    async def handle_delete(self, data):
        try:
            result = delete_message(data.get("message_id"))
            await self.send_json({"event": "delete_message", "result": result})
        except Exception as e:
            await self.send_json({"event": "delete_message", "error": str(e)})

    async def send_json(self, content):
        await self.send(text_data=json.dumps(content))
