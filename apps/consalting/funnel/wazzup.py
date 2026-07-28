import logging
import uuid
import requests
from django.utils import timezone
from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.main.models import Company
from apps.users.models import User, Branch
from ..models import (
    LeadConsalting,
    InboundLeadConsalting,
    WhatsAppMessageConsalting,
    WazzupAccountConsalting,
    LeadActivityConsalting,
    FunnelConsalting,
    FunnelStageConsalting,
)
from .activity import ActivityLogger
from . import events
from . import realtime

logger = logging.getLogger(__name__)


def _media_placeholder(text, media_type, content_uri):
    """Подставляет человекочитаемую метку для медиа, если текст пустой."""
    if text:
        return text
    if media_type in ("image", "photo"):
        return "📷 [Фотография]"
    if media_type in ("video",):
        return "🎥 [Видеозапись]"
    if media_type in ("audio", "voice", "ptt"):
        return "🎙 [Голосовое сообщение]"
    if media_type in ("document", "file"):
        return "📄 [Документ]"
    if content_uri:
        return "📎 [Вложение]"
    return "[Сообщение]"


def chat_events_group(company_id) -> str:
    """ЕДИНСТВЕННАЯ группа для чат-событий (new_message / message_status).

    Раньше каждое сообщение слалось в три группы (``consalting_company_``,
    ``wazzup_company_``, ``wazzup_chat_``), а чат-консьюмер подписан на все три —
    и получал ОДНО сообщение ТРИЖДЫ (проверено: 3 копии на кадр). Это давало
    тройной трафик и тройной ре-рендер на фронте, из-за чего переписка «тормозила».

    Теперь чат-события идут ровно в одну группу, на которую подписаны оба
    консьюмера (чат и канбан-воронка) → каждый сокет получает ровно одну копию.
    """
    return f"wazzup_company_{company_id}"


def _normalize_phone(chat_id):
    """Нормализует номер к формату 7XXXXXXXXXX (только цифры)."""
    clean_phone = "".join(filter(str.isdigit, chat_id or ""))
    if clean_phone.startswith("8") and len(clean_phone) == 11:
        clean_phone = "7" + clean_phone[1:]
    elif not clean_phone.startswith("7") and len(clean_phone) == 10:
        clean_phone = "7" + clean_phone
    return clean_phone


def _broadcast_message_status(company_id, wa_message, phone):
    """Лёгкая трансляция смены статуса исходящего сообщения (pending→sent/failed).

    Матчится фронтом по ``id`` = uuid строки сообщения (стабилен, в отличие от
    ``message_id``, который после ответа Wazzup меняется на серверный id).
    """
    event_envelope = {
        "type": "wazzup_event",
        "event": {
            "type": "message_status",
            "data": {
                "id": str(wa_message.id),
                "message_id": wa_message.message_id,
                "lead_id": str(wa_message.lead_id) if wa_message.lead_id else "",
                "status": wa_message.status,
                "timestamp": timezone.now().isoformat(),
            },
        },
    }

    realtime.reliable_group_send([(chat_events_group(company_id), event_envelope)])


def _broadcast_consalting_message(company_id, lead, wa_message, is_inbound, text, phone, origin_user_id=None, event_ts=None):
    """
    Мгновенная трансляция события сообщения по WebSocket без задержек.

    ``origin_user_id`` — id сотрудника, отправившего сообщение из CRM. Его
    собственные соединения НЕ должны получить этот broadcast: у отправителя уже
    есть локальное эхо (ack сокета / ответ REST), иначе он видит сообщение дважды.
    Консьюмер отфильтровывает по этому полю (см. wazzup_event).

    ``event_ts`` — истинное время события (для входящих — ``dateTime`` из Wazzup),
    чтобы фронт сортировал по нему и порядок не зависел от порядка обработки
    (при concurrency несколько вебхуков обрабатываются параллельно). По умолчанию —
    ``created_at`` записи (стабильнее, чем «сейчас» в момент рассылки).
    """
    content_uri = getattr(wa_message, "content_uri", None)
    media_type = getattr(wa_message, "media_type", None)

    ts = event_ts or (
        wa_message.created_at.isoformat() if getattr(wa_message, "created_at", None)
        else timezone.now().isoformat()
    )

    # id == uuid строки для ВСЕХ сообщений (входящих и исходящих) — совпадает с
    # `id` из REST-истории (сериализатор отдаёт pk) и с последующим
    # message_status, чтобы фронт мёржил сокет и REST по одному ключу без дублей.
    # (Wazzup message_id идёт отдельным полем `message_id`.)
    dedup_id = str(wa_message.id)
    msg_payload = {
        "id": dedup_id,
        "message_id": wa_message.message_id,
        "lead_id": str(lead.id),
        "chat_id": phone,
        "text": text,
        "content_uri": content_uri,
        "contentUri": content_uri,
        "media_type": media_type,
        "type": media_type or "text",
        "is_incoming": is_inbound,
        "direction": "inbound" if is_inbound else "outbound",
        "status": wa_message.status,
        "timestamp": ts,
        "created_at": ts,
        "contact_name": lead.full_name,
    }

    event_envelope = {
        "type": "wazzup_event",
        "origin_user_id": str(origin_user_id) if origin_user_id else None,
        "event": {
            "type": "new_message",
            "data": msg_payload
        }
    }

    realtime.reliable_group_send([(chat_events_group(company_id), event_envelope)])


class WazzupConsaltingService:
    """
    Интеграционный сервис Wazzup API v3 для воронки консалтинга.
    https://api.wazzup24.com/v3
    """

    @staticmethod
    def mark_chat_read(account: WazzupAccountConsalting, phone: str):
        """
        Сброс непрочитанных в Wazzup API (PATCH /v3/chats unread=0).
        Проставляет синие галочки в WhatsApp и снимает счётчик непрочитанных на телефоне.
        """
        if not phone:
            return
        clean_phone = "".join(filter(str.isdigit, phone))
        url = f"{account.api_url.rstrip('/')}/v3/chats"
        headers = {
            "Authorization": f"Bearer {account.api_key}",
            "Content-Type": "application/json"
        }
        payload = [
            {
                "chatId": clean_phone,
                "chatType": account.integration_type or "whatsapp",
                "unread": 0
            }
        ]
        try:
            res = requests.patch(url, json=payload, headers=headers, timeout=5.0)
            logger.info(f"Wazzup chat {clean_phone} marked read (unread=0): status={res.status_code}")
        except Exception as e:
            logger.warning(f"Failed to mark chat read in Wazzup: {e}")

    @staticmethod
    def send_message(account: WazzupAccountConsalting, lead: LeadConsalting, text: str, user: User = None, content_uri: str = None) -> WhatsAppMessageConsalting:
        """
        Отправка исходящего сообщения в Wazzup API (POST /v3/message)
        """
        if not lead.phone:
            raise ValueError("У лида не указан номер телефона.")

        if user:
            from apps.consalting.access import is_owner_like
            if lead.owner_id and lead.owner_id != user.id and not is_owner_like(user):
                raise ValueError("Отправлять сообщения лиду может только назначенный сотрудник или руководитель.")

        clean_phone = "".join(filter(str.isdigit, lead.phone))
        message_id = f"wz_out_{uuid.uuid4().hex[:12]}_{int(timezone.now().timestamp())}"

        effective_text = text or ""
        if not effective_text and content_uri:
            effective_text = "📎 [Медиа-файл]"

        with transaction.atomic():
            wa_message = WhatsAppMessageConsalting.objects.create(
                company_id=lead.company_id,
                branch_id=lead.branch_id,
                lead=lead,
                message_id=message_id,
                direction=WhatsAppMessageConsalting.Direction.OUTBOUND,
                text=effective_text,
                content_uri=content_uri,
                status=WhatsAppMessageConsalting.Status.PENDING
            )

            # Трансляция по WebSocket после коммита транзакции всем, КРОМЕ самого отправителя.
            acc_company_id = account.company_id
            lead_obj = lead
            wa_msg_obj = wa_message
            out_text = text
            c_phone = clean_phone
            orig_user_id = user.id if user else None

            transaction.on_commit(
                lambda: _broadcast_consalting_message(
                    acc_company_id, lead_obj, wa_msg_obj, False, out_text, c_phone,
                    origin_user_id=orig_user_id,
                )
            )

            ActivityLogger.log(
                lead=lead,
                activity_type=LeadActivityConsalting.Type.MESSAGE,
                actor=user,
                title="Wazzup (исходящее)",
                body=text,
                payload={
                    "direction": "outbound",
                    "message_id": message_id,
                    "status": "pending",
                    "channel_id": account.channel_id,
                }
            )

            # Перевод статуса в работу ("in_work") при ответе менеджера
            from apps.consalting.models import InboundLeadConsalting
            clean_phone_10 = clean_phone[-10:] if len(clean_phone) >= 10 else clean_phone
            inbound_lead = InboundLeadConsalting.objects.filter(
                company_id=lead.company_id,
                phone__icontains=clean_phone_10
            ).exclude(
                status__in=[InboundLeadConsalting.Status.CONVERTED, InboundLeadConsalting.Status.REJECTED]
            ).first()

            if inbound_lead and inbound_lead.status in [InboundLeadConsalting.Status.NEW, InboundLeadConsalting.Status.ASSIGNED]:
                inbound_lead.status = InboundLeadConsalting.Status.IN_WORK
                inbound_lead.save(update_fields=["status", "updated_at"])

            if lead.status in ["new", "NEW"]:
                lead.status = "in_work"
                lead.save(update_fields=["status", "updated_at"])

            # Обновляем канбан воронку через WebSocket после коммита
            transaction.on_commit(lambda: realtime.lead_updated(lead_obj))

            # Реальная отправка в Wazzup — в фоне, после коммита транзакции.
            from apps.consalting.tasks import send_wazzup_message
            wa_id = str(wa_message.id)
            acc_id = str(account.id)
            out_t = text or ""
            out_u = content_uri or ""
            transaction.on_commit(
                lambda: send_wazzup_message.delay(wa_id, acc_id, out_t, out_u)
            )

        return wa_message

    @staticmethod
    def enqueue_webhook(payload: dict):
        """Быстрый приём вебхука: вызов realtime-обработки в HTTP и постановка фоновых задач."""
        realtime_res = WazzupConsaltingService.handle_wazzup_webhook_realtime(payload)
        WazzupConsaltingService.enqueue_webhook_side_effects(payload, realtime_res)

    @staticmethod
    def enqueue_webhook_side_effects(payload: dict, realtime_result: dict = None):
        """Постановка тяжелых сайд-эффектов вебхука в Celery."""
        from apps.consalting.tasks import process_wazzup_webhook_side_effects
        process_wazzup_webhook_side_effects.delay(payload, realtime_result or {})

    @staticmethod
    def handle_wazzup_webhook_realtime(payload: dict) -> dict:
        """
        Быстрый синхронный путь Wazzup webhook прямо в HTTP-запросе:
        1. Распарсить messages и statuses
        2. Идемпотентно создать/найти Lead и WhatsAppMessageConsalting
        3. Зарегистрировать WebSocket broadcast (new_message / message_status) на transaction.on_commit
        4. Вернуть данные для фоновых сайд-эффектов
        """
        messages = payload.get("messages") or []
        statuses = payload.get("statuses") or []

        processed_messages = []
        processed_statuses = []

        for item in messages:
            channel_id = item.get("channelId")
            account = WazzupAccountConsalting.objects.filter(channel_id=channel_id, is_active=True).first()
            if not account:
                logger.warning(f"Wazzup account not found or inactive for channelId={channel_id}")
                continue

            message_id = str(item.get("messageId") or "").strip()
            chat_id = item.get("chatId") or item.get("author") or ""
            text = item.get("text") or ""
            content_uri = item.get("contentUri") or item.get("content_uri") or ""
            media_type = item.get("type") or ""
            is_inbound = item.get("isInbound", True)
            author_name = item.get("authorName") or ""
            event_dt = item.get("dateTime") or item.get("dateTimeUtc") or ""

            text = _media_placeholder(text, media_type, content_uri)

            # Защита от дублей по message_id
            if message_id and WhatsAppMessageConsalting.objects.filter(message_id=message_id).exists():
                logger.info(f"Wazzup duplicate webhook ignored for message_id={message_id}")
                continue

            clean_phone = _normalize_phone(chat_id)
            phone = f"+{clean_phone}" if not clean_phone.startswith("+") else clean_phone
            source_name = f"Wazzup ({account.integration_type})"

            with transaction.atomic():
                clean_phone_10 = clean_phone[-10:] if len(clean_phone) >= 10 else clean_phone
                inbound_lead = InboundLeadConsalting.objects.filter(
                    company=account.company,
                    phone=phone
                ).exclude(
                    status__in=[InboundLeadConsalting.Status.CONVERTED, InboundLeadConsalting.Status.REJECTED]
                ).order_by("-created_at").first()

                if not inbound_lead and len(clean_phone_10) >= 10:
                    inbound_lead = InboundLeadConsalting.objects.filter(
                        company=account.company,
                        phone__icontains=clean_phone_10
                    ).exclude(
                        status__in=[InboundLeadConsalting.Status.CONVERTED, InboundLeadConsalting.Status.REJECTED]
                    ).order_by("-created_at").first()

                inbound_created = False
                if inbound_lead:
                    inbound_lead.message = text
                    inbound_lead.updated_at = timezone.now()
                    if author_name and author_name != phone:
                        inbound_lead.full_name = author_name
                    if message_id:
                        inbound_lead.external_id = message_id
                    inbound_lead.save(update_fields=["message", "full_name", "external_id", "updated_at"])
                else:
                    inbound_lead = InboundLeadConsalting.objects.create(
                        company=account.company,
                        external_id=message_id if message_id else f"wz_{uuid.uuid4().hex[:12]}",
                        full_name=author_name or phone or "Wazzup Клиент",
                        phone=phone,
                        source=source_name,
                        message=text,
                        status=InboundLeadConsalting.Status.NEW,
                    )
                    inbound_created = True

                lead = LeadConsalting.objects.filter(
                    company_id=account.company_id,
                    phone=phone
                ).exclude(
                    stage__stage_type__in=[
                        FunnelStageConsalting.StageType.WON,
                        FunnelStageConsalting.StageType.COMPLETED,
                        FunnelStageConsalting.StageType.LOST
                    ]
                ).order_by("-updated_at").first()

                if not lead and len(clean_phone) >= 10:
                    lead = LeadConsalting.objects.filter(
                        company_id=account.company_id,
                        phone__endswith=clean_phone[-10:]
                    ).exclude(
                        stage__stage_type__in=[
                            FunnelStageConsalting.StageType.WON,
                            FunnelStageConsalting.StageType.COMPLETED,
                            FunnelStageConsalting.StageType.LOST
                        ]
                    ).order_by("-updated_at").first()

                created_lead = False
                if not lead:
                    funnel = FunnelConsalting.objects.filter(company_id=account.company_id).first()
                    if not funnel:
                        funnel = FunnelConsalting.objects.create(
                            company_id=account.company_id,
                            branch_id=account.branch_id,
                            name="Воронка консалтинга"
                        )

                    stage = FunnelStageConsalting.objects.filter(funnel=funnel).order_by("order").first()
                    if not stage:
                        stage = FunnelStageConsalting.objects.create(
                            company_id=account.company_id,
                            funnel=funnel,
                            name="Новый лид",
                            stage_type=FunnelStageConsalting.StageType.NEW_LEAD,
                            order=100
                        )

                    lead = LeadConsalting.objects.create(
                        company_id=account.company_id,
                        branch_id=account.branch_id,
                        funnel=funnel,
                        stage=stage,
                        title=f"Заявка из {account.get_integration_type_display()} ({phone})",
                        phone=phone,
                        full_name=author_name or f"Клиент {phone}",
                        status=LeadConsalting.Status.NEW
                    )
                    created_lead = True

                direction = (
                    WhatsAppMessageConsalting.Direction.INBOUND
                    if is_inbound else
                    WhatsAppMessageConsalting.Direction.OUTBOUND
                )

                # Связываем входящую заявку с карточкой воронки. Без этого поле
                # InboundLeadConsalting.lead оставалось пустым у всех заявок, и
                # конверсия «источник → лид → сделка» не считалась в принципе.
                if inbound_lead and not inbound_lead.lead_id:
                    inbound_lead.lead = lead
                    inbound_lead.save(update_fields=["lead", "updated_at"])

                effective_msg_id = message_id if message_id else f"msg_{uuid.uuid4().hex[:12]}"
                try:
                    wa_message, msg_created = WhatsAppMessageConsalting.objects.get_or_create(
                        message_id=effective_msg_id,
                        defaults={
                            "company_id": lead.company_id,
                            "branch_id": lead.branch_id,
                            "lead": lead,
                            "direction": direction,
                            "text": text,
                            "content_uri": content_uri,
                            "media_type": media_type,
                            "status": WhatsAppMessageConsalting.Status.READ if is_inbound else WhatsAppMessageConsalting.Status.SENT
                        }
                    )
                except Exception as exc:
                    logger.warning("WhatsAppMessageConsalting create duplicate exception: %s", exc)
                    wa_message = WhatsAppMessageConsalting.objects.filter(message_id=effective_msg_id).first()
                    msg_created = False

                if wa_message and msg_created:
                    # Трансляция по WebSocket мгновенно ПОСЛЕ коммита транзакции
                    acc_cid = account.company_id
                    l_obj = lead
                    wa_m = wa_message
                    ib_val = is_inbound
                    txt_val = text
                    ph_val = phone
                    dt_val = event_dt or None

                    transaction.on_commit(
                        lambda c_id=acc_cid, l=l_obj, m=wa_m, ib=ib_val, t=txt_val, p=ph_val, d=dt_val: _broadcast_consalting_message(
                            c_id, l, m, ib, t, p, event_ts=d
                        )
                    )

                    processed_messages.append({
                        "account_id": str(account.id),
                        "lead_id": str(lead.id),
                        "inbound_lead_id": str(inbound_lead.id) if inbound_lead else None,
                        "inbound_created": inbound_created,
                        "created_lead": created_lead,
                        "message_id": effective_msg_id,
                        "wa_message_id": str(wa_message.id),
                        "text": text,
                        "is_inbound": is_inbound,
                        "phone": phone,
                        "channel_id": channel_id,
                    })

        for item in statuses:
            message_id = item.get("messageId")
            status_str = item.get("status")
            if message_id and status_str:
                status_map = {
                    "sent": WhatsAppMessageConsalting.Status.SENT,
                    "delivered": WhatsAppMessageConsalting.Status.DELIVERED,
                    "read": WhatsAppMessageConsalting.Status.READ,
                    "failed": WhatsAppMessageConsalting.Status.FAILED,
                }
                st = status_map.get(status_str.lower())
                if st:
                    with transaction.atomic():
                        msg = WhatsAppMessageConsalting.objects.filter(message_id=message_id).first()
                        if not msg:
                            try:
                                msg = WhatsAppMessageConsalting.objects.filter(id=message_id).first()
                            except Exception:
                                msg = None
                        if msg:
                            msg.status = st
                            msg.save(update_fields=["status"])

                            acc_cid = msg.company_id
                            wa_m = msg
                            ph_val = _normalize_phone(msg.lead.phone) if msg.lead else ""
                            transaction.on_commit(
                                lambda c_id=acc_cid, m=wa_m, p=ph_val: _broadcast_message_status(c_id, m, p)
                            )
                            processed_statuses.append({
                                "message_id": message_id,
                                "wa_message_id": str(msg.id),
                                "lead_id": str(msg.lead_id) if msg.lead_id else None,
                                "status": st,
                            })

        return {
            "processed_messages": processed_messages,
            "processed_statuses": processed_statuses,
        }

    @staticmethod
    def handle_wazzup_webhook_side_effects(payload: dict, realtime_result: dict = None):
        """
        Фоновое выполнение тяжелых сайд-эффектов Wazzup webhook:
        - Автораспределение лидов (Round-Robin)
        - Запись логов активности ActivityLogger
        - Вызов событий воронки events.emit и realtime
        - Создание системных уведомлений менеджерам
        """
        from apps.consalting.views import distribute_inbound_lead
        from apps.main.realtime import create_and_publish_notification

        processed_messages = (realtime_result or {}).get("processed_messages") or []
        processed_statuses = (realtime_result or {}).get("processed_statuses") or []

        # 1. Тяжелая обработка для созданных/обновленных сообщений
        for item in processed_messages:
            lead = LeadConsalting.objects.filter(id=item["lead_id"]).first()
            if not lead:
                continue

            inbound_lead = None
            if item.get("inbound_lead_id"):
                inbound_lead = InboundLeadConsalting.objects.filter(id=item["inbound_lead_id"]).first()

            inbound_created = item.get("inbound_created", False)
            created_lead = item.get("created_lead", False)
            effective_msg_id = item["message_id"]
            text = item["text"]
            is_inbound = item["is_inbound"]
            phone = item["phone"]

            assigned_owner = None
            if inbound_created and inbound_lead:
                distributed_lead = distribute_inbound_lead(inbound_lead)
                if distributed_lead and distributed_lead.owner:
                    assigned_owner = distributed_lead.owner
                    if lead and not lead.owner:
                        lead.owner = assigned_owner
                        lead.save(update_fields=["owner", "updated_at"])

            ActivityLogger.log(
                lead=lead,
                activity_type=LeadActivityConsalting.Type.MESSAGE,
                actor=None,
                title=f"Wazzup ({'входящее' if is_inbound else 'исходящее'})",
                body=text,
                payload={
                    "direction": "inbound" if is_inbound else "outbound",
                    "message_id": effective_msg_id,
                    "channel_id": item.get("channel_id"),
                }
            )

            events.emit(
                trigger="activity_added" if not created_lead else "stage_changed",
                lead=lead,
                actor=None,
                ctx={"is_wazzup": True, "message_id": effective_msg_id}
            )

            if created_lead:
                realtime.lead_created(lead)
            else:
                realtime.lead_updated(lead)

            # Персональные системные уведомления — отдельной задачей.
            # Для нераспределённого лида здесь создаётся Notification + WS-рассылка
            # НА КАЖДОГО сотрудника компании; в горячем пути это занимало воркер и
            # задерживало обработку следующих входящих сообщений.
            if is_inbound:
                from apps.consalting.tasks import notify_inbound_message
                target_owner = assigned_owner or lead.owner
                notify_inbound_message.delay(
                    str(lead.id),
                    str(target_owner.id) if target_owner else None,
                    (text or "")[:120],
                    phone,
                )

    @staticmethod
    def handle_wazzup_webhook(payload: dict):
        """Совместимый метод: полная обработка realtime + side_effects."""
        realtime_res = WazzupConsaltingService.handle_wazzup_webhook_realtime(payload)
        WazzupConsaltingService.handle_wazzup_webhook_side_effects(payload, realtime_res)
