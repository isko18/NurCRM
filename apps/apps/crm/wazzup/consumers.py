import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


@database_sync_to_async
def _handle_ws_send_message(user, data):
    from apps.consalting.models import WazzupAccountConsalting, LeadConsalting
    from apps.consalting.funnel.wazzup import WazzupConsaltingService

    lead_id = data.get("lead_id") or data.get("lead")
    text = data.get("text") or data.get("message") or ""
    media_url = data.get("media_url") or data.get("content_uri") or data.get("contentUri")
    account_id = data.get("account_id")
    phone = data.get("to") or data.get("phone")

    # 1. Отправка по lead_id (Воронка Консалтинга)
    if lead_id:
        lead = LeadConsalting.objects.filter(company=user.company, id=lead_id).first()
        if not lead:
            raise ValueError("Лид не найден.")

        if account_id:
            account = WazzupAccountConsalting.objects.filter(company=user.company, id=account_id).first()
        else:
            account = WazzupAccountConsalting.objects.filter(company=user.company, is_active=True).first()

        if not account:
            raise ValueError("Активный аккаунт Wazzup для консалтинга не найден.")

        wa_msg = WazzupConsaltingService.send_message(
            account=account,
            lead=lead,
            text=text,
            user=user,
            content_uri=media_url
        )
        return {
            "id": str(wa_msg.id),
            "message_id": wa_msg.message_id,
            "status": wa_msg.status,
            "text": wa_msg.text,
            "lead_id": str(lead.id),
        }

    # 2. Безопасная обработка для базового CRM (если приложение подключено)
    try:
        from apps.crm.models import WazzupAccount
        from .services import send_message_service

        if not phone:
            raise ValueError("Укажите 'lead_id' или номер телефона 'to'")

        if account_id:
            account = WazzupAccount.objects.filter(company=user.company, id=account_id).first()
        else:
            account = WazzupAccount.objects.filter(company=user.company, is_active=True).first()

        if not account:
            raise ValueError("Активный аккаунт Wazzup не найден.")

        msg = send_message_service(account, phone, text, media_url)
        return {
            "id": str(msg.id),
            "message_id": msg.message_id,
            "chat_id": msg.chat_id,
            "text": msg.text,
            "status": msg.status,
        }
    except Exception as e:
        raise ValueError(f"Ошибка отправки сообщения: {e}")


class WazzupChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer для Wazzup чатов и уведомлений в реальном времени.
    Маршрут: ws/wazzup/ или ws/wazzup/chat/<chat_id>/
    
    Поддерживает:
      1. Приём входящих событий в реальном времени ("new_message", "message_status")
      2. Отправку исходящих сообщений через сокет ({ "action": "send_message", "lead_id": "...", "text": "..." })
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

        # Дополнительная группа для конкретного чата / консалтинга
        self.consalting_company_group = f"consalting_company_{company_id}"
        await self.channel_layer.group_add(self.consalting_company_group, self.channel_name)

        if self.chat_id:
            # Имя группы Channels не допускает "+" и иные не-[a-zA-Z0-9_.-] символы,
            # а рассылка использует только цифры номера (см. _chat_group в
            # apps/consalting/funnel/wazzup.py) — санитизируем, чтобы подписка совпала.
            chat_digits = "".join(filter(str.isdigit, str(self.chat_id))) or self.chat_id
            self.chat_group = f"wazzup_chat_{chat_digits}"
            await self.channel_layer.group_add(self.chat_group, self.channel_name)
        else:
            self.chat_group = None

        await self.accept()
        logger.info(f"WebSocket connected for user {self.user} in group {self.company_group}")

    async def disconnect(self, close_code):
        if hasattr(self, "company_group") and self.company_group:
            await self.channel_layer.group_discard(self.company_group, self.channel_name)
        if hasattr(self, "consalting_company_group") and self.consalting_company_group:
            await self.channel_layer.group_discard(self.consalting_company_group, self.channel_name)
        if hasattr(self, "chat_group") and self.chat_group:
            await self.channel_layer.group_discard(self.chat_group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
            action = data.get("action")

            # 1. Пинг/Понг для поддержания соединения
            if action == "ping":
                await self.send(text_data=json.dumps({"action": "pong"}))
                return

            # 2. Отправка исходящего сообщения прямо через WebSocket
            if action == "send_message":
                try:
                    result = await _handle_ws_send_message(self.user, data)
                    await self.send(text_data=json.dumps({
                        "action": "send_message_ack",
                        "status": "success",
                        "data": result
                    }))
                except Exception as err:
                    await self.send(text_data=json.dumps({
                        "action": "send_message_ack",
                        "status": "error",
                        "detail": str(err)
                    }))

        except Exception as e:
            logger.error(f"Error handling WS message: {e}")

    async def wazzup_event(self, event):
        """
        Пересылка события клиенту.

        Исходящее сообщение НЕ отправляем обратно его же автору — у отправителя
        уже есть локальное эхо (ack), иначе он увидит своё сообщение дважды.
        """
        origin = event.get("origin_user_id")
        if origin and str(origin) == str(getattr(self.user, "id", "")):
            return
        await self.send(text_data=json.dumps(event.get("event", {})))

    async def consalting_event(self, event):
        """
        Пересылка событий карточек и чата воронки консалтинга
        """
        await self.send(text_data=json.dumps({
            "type": event.get("event"),
            "data": event.get("payload")
        }))
