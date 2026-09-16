from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
import requests

from .models import WazzupAccountConsalting, LeadConsalting
from .funnel.wazzup import WazzupConsaltingService
from .serializers import WazzupAccountConsaltingSerializer, WhatsAppMessageConsaltingSerializer
from .voice_notes import is_voice, public_uri, transcode_voice_bytes, transcode_local_voice_uri


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

        media_type = request.data.get("media_type") or request.data.get("type") or ""
        content_type = request.data.get("content_type") or request.data.get("mimetype") or file_obj.content_type or ""
        raw_file = file_obj.read()
        is_voice_note = is_voice(media_type, content_type, file_obj.name)
        saved_path = transcode_voice_bytes(raw_file) if is_voice_note else None
        # A document fallback is intentional: it is deliverable even on hosts
        # without ffmpeg, unlike incorrectly-labelled WebM voice notes.
        final_media_type = "voice" if saved_path else ("document" if is_voice_note else media_type)
        if not saved_path:
            filename = f"wazzup/uploads/{uuid.uuid4().hex[:12]}_{clean_filename}"
            saved_path = default_storage.save(filename, ContentFile(raw_file))

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
            "media_type": final_media_type,
            "type": final_media_type,
            "content_type": "audio/ogg" if final_media_type == "voice" else content_type,
            "mimetype": "audio/ogg" if final_media_type == "voice" else content_type,
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
        media_type = request.data.get("media_type") or request.data.get("type") or ""
        content_type = request.data.get("content_type") or request.data.get("mimetype") or ""

        if not media_url and request.FILES:
            upload_resp = self._handle_file_upload(request)
            if upload_resp.status_code == status.HTTP_201_CREATED:
                media_url = upload_resp.data.get('url')
                media_type = upload_resp.data.get("media_type") or media_type
                content_type = upload_resp.data.get("content_type") or content_type

        if media_url and is_voice(media_type, content_type, media_url) and not media_url.lower().split("?", 1)[0].endswith(".ogg"):
            converted_path = transcode_local_voice_uri(media_url)
            if converted_path:
                media_url = public_uri(request, converted_path)
                media_type, content_type = "voice", "audio/ogg"
            else:
                media_type = "document"

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
                content_uri=media_url,
                media_type=media_type,
                content_type=content_type,
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
            # 1. Быстрый синхронный путь прямо в HTTP-запросе (сохранение в БД + WS на transaction.on_commit)
            realtime_res = WazzupConsaltingService.handle_wazzup_webhook_realtime(payload)
            # 2. Постановка тяжелых сайд-эффектов в Celery
            WazzupConsaltingService.enqueue_webhook_side_effects(payload, realtime_res)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WhatsAppMessageConsaltingViewSet(viewsets.ModelViewSet):
    """
    Просмотр сообщений Wazzup/WhatsApp воронки консалтинга.
    Поддерживает фильтрацию по ?lead=<lead_id> или ?lead_id=<lead_id>
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WhatsAppMessageConsaltingSerializer
    pagination_class = None

    def get_queryset(self):
        from .models import WhatsAppMessageConsalting, LeadConsalting
        company = getattr(self.request.user, "company", None) or getattr(self.request.user, "owned_company", None)
        qs = WhatsAppMessageConsalting.objects.filter(company=company)

        lead_id = self.request.query_params.get('lead') or self.request.query_params.get('lead_id')
        phone = self.request.query_params.get('phone') or self.request.query_params.get('chat_id')

        if lead_id and str(lead_id).startswith("phone_"):
            p_digits = "".join(filter(str.isdigit, str(lead_id)))
            if p_digits:
                return qs.filter(lead__phone__icontains=p_digits[-9:]).order_by('created_at')

        # История отдаётся по ДИАЛОГУ (номеру), а не по конкретному лиду.
        if lead_id and not phone:
            lead = LeadConsalting.objects.filter(company=company, id=lead_id).only("phone").first()
            phone = lead.phone if lead else None
            if not phone:
                return qs.filter(lead_id=lead_id).order_by('created_at')

        if phone:
            digits = "".join(filter(str.isdigit, str(phone)))
            if len(digits) >= 9:
                return qs.filter(lead__phone__icontains=digits[-9:]).order_by('created_at')
            if digits:
                return qs.filter(lead__phone__contains=digits).order_by('created_at')

        if lead_id:
            qs = qs.filter(lead_id=lead_id)
        return qs.order_by('created_at')

    def partial_update(self, request, pk=None):
        return self.update(request, pk=pk, partial=True)

    def update(self, request, pk=None, partial=True):
        new_text = request.data.get('text') or request.data.get('message') or ""
        try:
            msg = WazzupConsaltingService.edit_message(
                message_id=pk,
                new_text=new_text,
                user=request.user,
                company_id=getattr(request.user, "company_id", None)
            )
            serializer = self.get_serializer(msg)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, pk=None):
        try:
            WazzupConsaltingService.delete_message(
                message_id=pk,
                user=request.user,
                company_id=getattr(request.user, "company_id", None)
            )
            return Response({"status": "deleted", "id": pk}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WazzupChatListView(APIView):
    """
    Полный список чатов/диалогов WhatsApp компании (как в мобильном WhatsApp).
    Объединяет все диалоги из WhatsAppMessageConsalting, LeadConsalting и InboundLeadConsalting.
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get(self, request):
        from .access import is_owner_like
        from .models import ChatReadStateConsalting, LeadConsalting, InboundLeadConsalting, WhatsAppMessageConsalting
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
        all_msgs = list(WhatsAppMessageConsalting.objects.filter(company=company).order_by("created_at"))
        last_msg_map = {}
        for m in all_msgs:
            if m.lead_id:
                last_msg_map[str(m.lead_id)] = m
            if m.lead and m.lead.phone:
                cp = "".join(filter(str.isdigit, m.lead.phone))
                if cp:
                    last_msg_map[cp] = m

        # Непрочитанное — персональное состояние сотрудника, а не глобальный
        # статус сообщения. Это позволяет каждому сотруднику иметь свой бейдж.
        read_at_by_lead = {
            str(state.lead_id): state.last_read_at
            for state in ChatReadStateConsalting.objects.filter(
                employee=user, lead_id__in=[lead.id for lead in leads_by_phone.values()]
            ).only("lead_id", "last_read_at")
        }
        unread_map = {}
        for message in all_msgs:
            if message.direction != WhatsAppMessageConsalting.Direction.INBOUND or not message.lead_id:
                continue
            lead_key = str(message.lead_id)
            last_read_at = read_at_by_lead.get(lead_key)
            if last_read_at is None or message.created_at > last_read_at:
                unread_map[lead_key] = unread_map.get(lead_key, 0) + 1

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
        if request.query_params.get("unread", "").strip().lower() in {"1", "true", "yes"}:
            chats = [chat for chat in chats if chat["has_unread"]]
        return Response(chats, status=status.HTTP_200_OK)


class WazzupCredentialsView(APIView):
    """
    Эндпоинт получения ключей интеграций Wazzup/GreenAPI (WhatsApp, Instagram, Telegram) для фронтенда.
    Возвращает МАССИВ объектов всех каналов компании.
    GET /api/consalting/wazzup/credentials/
    GET /api/consalting/wazzup-credentials/
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = getattr(request.user, "company", None) or getattr(request.user, "owned_company", None)
        if not company:
            return Response([], status=status.HTTP_200_OK)

        accounts = list(WazzupAccountConsalting.objects.filter(company=company, is_active=True))
        if not accounts:
            acc, _ = WazzupAccountConsalting.objects.get_or_create(
                company=company,
                defaults={
                    "channel_id": "greenapi_" + str(company.id)[:8],
                    "api_key": "dummy_wazzup_key",
                    "api_url": "https://api.wazzup24.com",
                    "integration_type": "whatsapp",
                    "is_active": True,
                    "is_connected": True,
                    "green_api_id_instance": "710722733904",
                    "green_api_token_instance": "a425cd0593934fbbb6c7aaa75903fcc6c1430378fa8247b3af",
                    "green_api_url": "https://7107.api.greenapi.com",
                    "green_api_media_url": "https://7107.api.greenapi.com",
                    "green_api_enabled": True,
                }
            )
            accounts = [acc]

        result = []
        for acc in accounts:
            result.append({
                "id": str(acc.id),
                "api_key": acc.api_key or "greenapi_active",
                "channel_id": acc.channel_id or ("greenapi_" + str(company.id)[:8]),
                "integration_type": acc.integration_type or "whatsapp",
                "api_url": acc.api_url or "https://api.wazzup24.com",
                "is_active": True,
                "green_api_id_instance": acc.green_api_id_instance,
                "green_api_enabled": acc.green_api_enabled,
            })

        return Response(result, status=status.HTTP_200_OK)
