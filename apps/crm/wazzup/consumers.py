import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


@database_sync_to_async
def _handle_ws_send_message(user, data):
    from apps.consalting.models import WazzupAccountConsalting, LeadConsalting, FunnelConsalting, FunnelStageConsalting
    from apps.consalting.funnel.wazzup import WazzupConsaltingService
    import uuid as _uuid_mod

    lead_id = data.get("lead_id") or data.get("lead")
    text = data.get("text") or data.get("message") or ""
    media_url = data.get("media_url") or data.get("content_uri") or data.get("contentUri")
    media_type = data.get("media_type") or data.get("type") or ""
    content_type = data.get("content_type") or data.get("mimetype") or ""
    account_id = data.get("account_id")
    phone = data.get("to") or data.get("phone")

    if lead_id and str(lead_id).startswith("phone_"):
        phone = str(lead_id).replace("phone_", "")
        lead_id = None

    lead = None
    if lead_id:
        try:
            _uuid_mod.UUID(str(lead_id))
            lead = LeadConsalting.objects.filter(company=user.company, id=lead_id).first()
        except ValueError:
            digits = "".join(filter(str.isdigit, str(lead_id)))
            if digits:
                phone = digits
            lead_id = None

    if not lead and phone:
        clean_phone = "".join(filter(str.isdigit, str(phone)))
        if len(clean_phone) >= 9:
            core_9 = clean_phone[-9:]
            lead = LeadConsalting.objects.filter(
                company=user.company, phone__icontains=core_9
            ).first()
        if not lead and clean_phone:
            from apps.consalting.funnel.regional_routing import resolve_funnel_and_assignee
            reg_funnel, reg_stage, reg_rule, reg_user = resolve_funnel_and_assignee(
                user.company, phone=f"+{clean_phone}", source="whatsapp"
            )
            funnel = reg_funnel or FunnelConsalting.objects.filter(company=user.company, is_active=True).first() or FunnelConsalting.objects.filter(company=user.company).first()
            stage = reg_stage or (FunnelStageConsalting.objects.filter(funnel=funnel).order_by("order").first() if funnel else None)
            owner = reg_user or user
            lead = LeadConsalting.objects.create(
                company=user.company,
                funnel=funnel,
                stage=stage,
                region_code=reg_rule.region_code if reg_rule else None,
                title=f"+{clean_phone}",
                full_name=f"+{clean_phone}",
                phone=f"+{clean_phone}",
                owner=owner,
                source="Ватсап",
                channel="whatsapp"
            )

    if not lead:
        raise ValueError("Не удалось определить лид для отправки сообщения.")

    if account_id:
        account = WazzupAccountConsalting.objects.filter(company=user.company, id=account_id).first()
    else:
        account = WazzupAccountConsalting.objects.filter(company=user.company, is_active=True).first()

    if not account:
        account = WazzupAccountConsalting.objects.filter(company=user.company).first()

    if not account:
        raise ValueError("Активный аккаунт Wazzup/GREEN-API не найден.")

    wa_msg = WazzupConsaltingService.send_message(
        account=account,
        lead=lead,
        text=text,
        user=user,
        content_uri=media_url,
        media_type=media_type,
        content_type=content_type,
    )
    return {
        "id": str(wa_msg.id),
        "message_id": wa_msg.message_id,
        "status": wa_msg.status,
        "text": wa_msg.text,
        "lead_id": str(lead.id),
    }


@database_sync_to_async
def _handle_ws_edit_message(user, message_id, text):
    from apps.consalting.funnel.wazzup import WazzupConsaltingService
    msg = WazzupConsaltingService.edit_message(
        message_id=message_id,
        new_text=text,
        user=user,
        company_id=getattr(user, "company_id", None)
    )
    return {
        "id": str(msg.id),
        "message_id": msg.message_id,
        "text": msg.text,
        "lead_id": str(msg.lead_id) if msg.lead_id else None,
    }


@database_sync_to_async
def _handle_ws_delete_message(user, message_id):
    from apps.consalting.funnel.wazzup import WazzupConsaltingService
    return WazzupConsaltingService.delete_message(
        message_id=message_id,
        user=user,
        company_id=getattr(user, "company_id", None)
    )

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
                        # 3. Редактирование сообщения через сокет
            if action == "edit_message":
                try:
                    msg_id = data.get("message_id") or data.get("id")
                    new_text = data.get("text") or data.get("message") or ""
                    edited = await _handle_ws_edit_message(self.user, msg_id, new_text)
                    await self.send(text_data=json.dumps({
                        "action": "edit_message_ack",
                        "status": "success",
                        "data": edited
                    }))
                except Exception as err:
                    await self.send(text_data=json.dumps({
                        "action": "edit_message_ack",
                        "status": "error",
                        "detail": str(err)
                    }))

            # 4. Удаление сообщения через сокет
            if action == "delete_message":
                try:
                    msg_id = data.get("message_id") or data.get("id")
                    await _handle_ws_delete_message(self.user, msg_id)
                    await self.send(text_data=json.dumps({
                        "action": "delete_message_ack",
                        "status": "success",
                        "id": msg_id
                    }))
                except Exception as err:
                    await self.send(text_data=json.dumps({
                        "action": "delete_message_ack",
                        "status": "error",
                        "detail": str(err)
                    }))

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
