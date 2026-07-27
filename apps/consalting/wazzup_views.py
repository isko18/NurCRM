from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
import requests

from .models import WazzupAccountConsalting, LeadConsalting
from .funnel.wazzup import WazzupConsaltingService
from .serializers import WazzupAccountConsaltingSerializer, WhatsAppMessageConsaltingSerializer


class WazzupAccountConsaltingViewSet(viewsets.ModelViewSet):
    """
    Управление аккаунтами Wazzup в воронке консалтинга
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WazzupAccountConsaltingSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]


    def get_queryset(self):
        return WazzupAccountConsalting.objects.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)

    @action(detail=True, methods=['post'], url_path='upload')
    def upload_media_detail(self, request, pk=None):
        return self._handle_file_upload(request)

    @action(detail=False, methods=['post'], url_path='upload')
    def upload_media_list(self, request):
        return self._handle_file_upload(request)

    def _handle_file_upload(self, request):
        """
        Загрузка медиафайла/изображения менеджером для отправки клиенту в Wazzup.
        Принимает multipart/form-data файл (поле 'file', 'image', 'media', 'document').
        Сохраняет в MEDIA_ROOT/wazzup/uploads/ и возвращает публичную ссылку.
        """
        import os
        import uuid
        from django.core.files.storage import default_storage
        from django.core.files.base import ContentFile
        from django.conf import settings

        file_obj = None
        for key in ['file', 'image', 'media', 'document', 'content', 'attachment']:
            if key in request.FILES:
                file_obj = request.FILES[key]
                break

        if not file_obj and request.FILES:
            file_obj = list(request.FILES.values())[0]

        if not file_obj:
            return Response({"detail": "Файл не передан (ожидается поле file)"}, status=status.HTTP_400_BAD_REQUEST)

        clean_filename = "".join(c for c in file_obj.name if c.isalnum() or c in "._-")
        if not clean_filename:
            clean_filename = "upload.jpg"

        filename = f"wazzup/uploads/{uuid.uuid4().hex[:12]}_{clean_filename}"
        saved_path = default_storage.save(filename, ContentFile(file_obj.read()))

        media_url_prefix = getattr(settings, 'MEDIA_URL', '/media/')
        relative_url = f"{media_url_prefix.rstrip('/')}/{saved_path.lstrip('/')}"

        absolute_url = request.build_absolute_uri(relative_url)
        if "localhost" in absolute_url or "127.0.0.1" in absolute_url or absolute_url.startswith("http://"):
            domain = "app.nurcrm.kg"
            try:
                host_header = request.get_host().split(":")[0]
                if host_header and host_header not in ["127.0.0.1", "localhost"]:
                    domain = host_header
            except Exception:
                pass
            absolute_url = f"https://{domain}{relative_url}"

        return Response({
            "url": absolute_url,
            "content_uri": absolute_url,
            "contentUri": absolute_url,
            "media_url": absolute_url,
            "file_url": absolute_url,
            "name": file_obj.name,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='setup-webhook')
    def setup_webhook(self, request, pk=None):
        """
        Регистрация Webhook в Wazzup API v3
        """
        account = self.get_object()
        webhook_url = request.data.get('webhook_url') or "https://app.nurcrm.kg/api/consalting/wazzup/webhook/"

        url = f"{account.api_url.rstrip('/')}/v3/webhooks"
        headers = {
            "Authorization": f"Bearer {account.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "webhooksUri": webhook_url,
            "subscriptions": {
                "messagesAndStatuses": True
            }
        }
        try:
            res = requests.patch(url, json=payload, headers=headers, timeout=12.0)
            res.raise_for_status()
            account.is_connected = True
            account.save(update_fields=['is_connected'])
            return Response({"detail": "Webhook успешно привязан", "response": res.json()})
        except Exception as e:
            return Response({"detail": f"Ошибка регистрации Webhook: {e}"}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='send-message')
    def send_message(self, request, pk=None):
        """
        Отправка сообщения из воронки консалтинга по лиду
        """
        account = self.get_object()
        lead_id = request.data.get('lead_id')
        text = request.data.get('message') or request.data.get('text') or ""
        media_url = request.data.get('content_uri') or request.data.get('contentUri') or request.data.get('media_url') or request.data.get('file_url')

        if not media_url and request.FILES:
            upload_resp = self._handle_file_upload(request)
            if upload_resp.status_code == status.HTTP_201_CREATED:
                media_url = upload_resp.data.get('url')

        if not lead_id:
            return Response({"detail": "Укажите lead_id"}, status=status.HTTP_400_BAD_REQUEST)

        lead = LeadConsalting.objects.filter(company=self.request.user.company, id=lead_id).first()
        if not lead:
            return Response({"detail": "Лид не найден"}, status=status.HTTP_404_NOT_FOUND)

        try:
            wa_msg = WazzupConsaltingService.send_message(
                account=account,
                lead=lead,
                text=text,
                user=request.user,
                content_uri=media_url
            )
            return Response({
                "id": str(wa_msg.id),
                "message_id": wa_msg.message_id,
                "status": wa_msg.status,
                "text": wa_msg.text,
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WazzupWebhookConsaltingView(APIView):
    """
    Приемник исходящих событий (сообщений и статусов) от Wazzup Webhook.
    POST /api/consalting/wazzup/webhook/
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        payload = request.data
        try:
            # Мгновенные сокеты + тяжёлая обработка в Celery: отдаём 200 сразу,
            # чтобы Wazzup не придерживал следующие вебхуки.
            WazzupConsaltingService.enqueue_webhook(payload)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WhatsAppMessageConsaltingViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Просмотр сообщений Wazzup/WhatsApp воронки консалтинга.
    Поддерживает фильтрацию по ?lead=<lead_id> или ?lead_id=<lead_id>
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WhatsAppMessageConsaltingSerializer
    pagination_class = None

    def get_queryset(self):
        from .models import WhatsAppMessageConsalting, LeadConsalting
        company = self.request.user.company
        qs = WhatsAppMessageConsalting.objects.filter(company=company)

        lead_id = self.request.query_params.get('lead') or self.request.query_params.get('lead_id')
        phone = self.request.query_params.get('phone') or self.request.query_params.get('chat_id')

        # История отдаётся по ДИАЛОГУ (номеру), а не по конкретному лиду.
        # У одного номера может быть несколько лидов (повторные обращения,
        # закрытые сделки), и сообщения оказываются раскиданы по ним: чат,
        # открытый на «пустом» лиде, показывал 0 сообщений, хотя переписка есть.
        if lead_id and not phone:
            lead = LeadConsalting.objects.filter(company=company, id=lead_id).only("phone").first()
            phone = lead.phone if lead else None
            if not phone:
                return qs.filter(lead_id=lead_id).order_by('created_at')

        if phone:
            digits = "".join(filter(str.isdigit, str(phone)))
            if len(digits) >= 10:
                return qs.filter(lead__phone__endswith=digits[-10:]).order_by('created_at')
            if digits:
                return qs.filter(lead__phone__contains=digits).order_by('created_at')

        if lead_id:
            qs = qs.filter(lead_id=lead_id)
        return qs.order_by('created_at')


class WazzupChatListView(APIView):
    """
    Полный список чатов/диалогов WhatsApp компании (как в мобильном WhatsApp).
    Объединяет все диалоги из WhatsAppMessageConsalting, LeadConsalting и InboundLeadConsalting.
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get(self, request):
        from .access import is_owner_like
        from .models import LeadConsalting, InboundLeadConsalting, WhatsAppMessageConsalting
        from django.db.models import Q

        user = request.user
        company = getattr(user, "company", None)
        if not company:
            return Response([], status=status.HTTP_200_OK)

        # Берём абсолютно ВСЕ лиды компании без каких-либо ограничений.
        # Порядок важен: у одного номера может быть несколько лидов, и чат должен
        # указывать на ТОТ ЖЕ лид, к которому вебхук привязывает новые сообщения
        # (см. handle_wazzup_webhook — там выбор идёт по "-updated_at"). Иначе чат
        # открывается на «пустом» лиде.
        leads_qs = LeadConsalting.objects.filter(company=company).select_related("owner").order_by("-updated_at")
        leads_by_phone = {}
        for lead in leads_qs:
            if lead.phone:
                clean_phone = "".join(filter(str.isdigit, lead.phone))
                if clean_phone and clean_phone not in leads_by_phone:
                    leads_by_phone[clean_phone] = lead

        # Берём абсолютно ВСЕ входящие заявки компании
        inbound_qs = InboundLeadConsalting.objects.filter(company=company).select_related("owner").order_by("-updated_at")
        inbounds_by_phone = {}
        for ib in inbound_qs:
            if ib.phone:
                clean_phone = "".join(filter(str.isdigit, ib.phone))
                if clean_phone and clean_phone not in inbounds_by_phone:
                    inbounds_by_phone[clean_phone] = ib

        all_phones = set(leads_by_phone.keys()) | set(inbounds_by_phone.keys())

        msg_qs = WhatsAppMessageConsalting.objects.filter(company=company)
        raw_msg_phones = msg_qs.values_list("lead__phone", flat=True).distinct()
        for p in raw_msg_phones:
            if p:
                cp = "".join(filter(str.isdigit, p))
                if cp:
                    all_phones.add(cp)

        # Пакетная загрузка всех последних сообщений в 1 запрос (убираем N+1)
        all_msgs = WhatsAppMessageConsalting.objects.filter(company=company).order_by("created_at")
        last_msg_map = {}
        for m in all_msgs:
            if m.lead_id:
                last_msg_map[str(m.lead_id)] = m
            if m.lead and m.lead.phone:
                cp = "".join(filter(str.isdigit, m.lead.phone))
                if cp:
                    last_msg_map[cp] = m

        # Пакетный подсчёт непрочитанных сообщений по всем лидам в 1 запрос (убираем N+1)
        from django.db.models import Count
        unread_counts = WhatsAppMessageConsalting.objects.filter(
            company=company,
            direction=WhatsAppMessageConsalting.Direction.INBOUND
        ).exclude(
            status=WhatsAppMessageConsalting.Status.READ
        ).values("lead_id").annotate(cnt=Count("id"))

        unread_map = {str(item["lead_id"]): item["cnt"] for item in unread_counts if item["lead_id"]}

        chats = []
        for cp in all_phones:
            lead = leads_by_phone.get(cp)
            inbound = inbounds_by_phone.get(cp)

            lead_id = str(lead.id) if lead else None
            last_msg = None
            if lead_id and lead_id in last_msg_map:
                last_msg = last_msg_map[lead_id]
            elif cp in last_msg_map:
                last_msg = last_msg_map[cp]

            unread_cnt = unread_map.get(lead_id, 0) if lead_id else 0

            contact_name = None
            phone_num = None
            owner_data = None

            if lead:
                phone_num = lead.phone
                contact_name = lead.full_name or lead.title or lead.phone
                if lead.owner:
                    owner_name = f"{(lead.owner.first_name or '').strip()} {(lead.owner.last_name or '').strip()}".strip() or getattr(lead.owner, "email", "")
                    owner_data = {"id": str(lead.owner.id), "name": owner_name}
            elif inbound:
                phone_num = inbound.phone
                contact_name = inbound.full_name or inbound.phone
                if inbound.owner:
                    owner_name = f"{(inbound.owner.first_name or '').strip()} {(inbound.owner.last_name or '').strip()}".strip() or getattr(inbound.owner, "email", "")
                    owner_data = {"id": str(inbound.owner.id), "name": owner_name}
            else:
                phone_num = f"+{cp}"
                contact_name = f"+{cp}"

            last_msg_data = None
            last_msg_text = ""
            last_msg_time = None

            if last_msg:
                last_msg_data = {
                    "id": str(last_msg.id),
                    "message_id": last_msg.message_id,
                    "text": last_msg.text,
                    "direction": last_msg.direction,
                    "status": last_msg.status,
                    "is_incoming": last_msg.direction == "inbound",
                    "created_at": last_msg.created_at.isoformat() if last_msg.created_at else None,
                }
                last_msg_text = last_msg.text
                last_msg_time = last_msg.created_at.isoformat() if last_msg.created_at else None
            elif inbound:
                last_msg_text = inbound.message or ""
                last_msg_time = inbound.updated_at.isoformat() if inbound.updated_at else inbound.created_at.isoformat()

            chats.append({
                "id": lead_id or f"phone_{cp}",
                "lead_id": lead_id,
                "chat_id": phone_num or f"+{cp}",
                "name": contact_name,
                "full_name": contact_name,
                "phone": phone_num or f"+{cp}",
                "owner": owner_data,
                "last_message": last_msg_data,
                "last_message_text": last_msg_text,
                "last_message_time": last_msg_time,
                "unread_count": unread_cnt,
                "has_unread": unread_cnt > 0,
                "updated_at": last_msg_time,
            })

        chats.sort(key=lambda c: c["last_message_time"] or "", reverse=True)
        return Response(chats, status=status.HTTP_200_OK)


class WazzupCredentialsView(APIView):
    """
    Эндпоинт получения ключей интеграций Wazzup (WhatsApp, Instagram, Telegram) для фронтенда.
    Возвращает МАССИВ объектов всех каналов компании, настроенных в админке Django.
    GET /api/consalting/wazzup/credentials/
    GET /api/consalting/wazzup-credentials/
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = getattr(request.user, "company", None)
        if not company:
            return Response([], status=status.HTTP_200_OK)

        accounts = WazzupAccountConsalting.objects.filter(company=company)
        result = []
        for acc in accounts:
            result.append({
                "id": str(acc.id),
                "api_key": acc.api_key or "",
                "channel_id": acc.channel_id or "",
                "integration_type": acc.integration_type or "whatsapp",
                "api_url": acc.api_url or "https://api.wazzup24.com",
                "is_active": acc.is_active,
            })

        return Response(result, status=status.HTTP_200_OK)

