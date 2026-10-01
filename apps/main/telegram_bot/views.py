import logging
from decimal import Decimal
from django.utils.dateparse import parse_date
from django.db.models import Sum, Count, Q, Value, DecimalField
from django.db.models.functions import Coalesce, TruncDate
from rest_framework import permissions, status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError, NotFound
from rest_framework.pagination import PageNumberPagination

from apps.main.telegram_bot.models import (
    TelegramBotSettings,
    TelegramInquiry,
    TelegramCustomerProfile,
    TelegramMessageLog,
)
from apps.main.telegram_bot.serializers import (
    TelegramBotSettingsSerializer,
    TelegramInquirySerializer,
    TelegramCustomerSerializer,
)
from apps.main.telegram_bot.services import telegram_api, ai_service, events_handler
from apps.main.models import Client

logger = logging.getLogger("telegram_bot.views")
ZERO_MONEY = Decimal("0.00")
MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


def _get_company(request):
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        raise PermissionDenied("Требуется авторизация.")
    company = getattr(user, "owned_company", None) or getattr(user, "company", None)
    if not company:
        raise PermissionDenied("У пользователя не найдена компания.")
    return company


class TelegramInquiryPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


# =========================================================================
# 4.1 Настройки бота
# =========================================================================

class TelegramBotSettingsView(APIView):
    """
    GET /api/main/telegram-bot/settings/
    PATCH /api/main/telegram-bot/settings/
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = _get_company(request)
        settings, _ = TelegramBotSettings.objects.get_or_create(company=company)
        serializer = TelegramBotSettingsSerializer(settings)
        return Response(serializer.data)

    def patch(self, request):
        company = _get_company(request)
        settings, _ = TelegramBotSettings.objects.get_or_create(company=company)
        serializer = TelegramBotSettingsSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class TelegramBotDetectOwnerChatView(APIView):
    """
    POST /api/main/telegram-bot/detect-owner-chat/
    Берёт последний чат, где написали /start, сохраняет как owner_chat_id.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        company = _get_company(request)
        settings, _ = TelegramBotSettings.objects.get_or_create(company=company)

        # Ищем последнее сообщение со словом /start
        last_log = (
            TelegramMessageLog.objects.filter(bot_settings=settings, text__icontains="/start")
            .order_by("-created_at")
            .first()
        )
        if not last_log:
            # Попробуем самое последнее сообщение вообще, если /start не найден
            last_log = (
                TelegramMessageLog.objects.filter(bot_settings=settings)
                .order_by("-created_at")
                .first()
            )

        if not last_log:
            return Response(
                {"detail": "Напишите боту /start и повторите"},
                status=status.HTTP_404_NOT_FOUND,
            )

        settings.owner_chat_id = str(last_log.chat_id)
        settings.owner_chat_title = last_log.chat_title or last_log.sender_name or "Nur"
        settings.save(update_fields=["owner_chat_id", "owner_chat_title"])

        return Response({
            "owner_chat_id": settings.owner_chat_id,
            "owner_chat_title": settings.owner_chat_title,
        })


class TelegramBotTestMessageView(APIView):
    """
    POST /api/main/telegram-bot/test-message/
    Пробное сообщение владельцу.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        company = _get_company(request)
        settings, _ = TelegramBotSettings.objects.get_or_create(company=company)

        if not settings.token:
            return Response({"detail": "Токен бота не настроен"}, status=status.HTTP_400_BAD_REQUEST)

        if not settings.owner_chat_id:
            return Response({"detail": "Чат владельца (owner_chat_id) не настроен"}, status=status.HTTP_400_BAD_REQUEST)

        test_text = (
            "✅ <b>Тестовое сообщение от бота NurCRM!</b>\n"
            f"Связь с магазином «{getattr(company, 'name', '')}» успешно установлена.\n"
            "Вы будете получать сюда сводки смен, уведомления о заказах и заканчивающихся товарах."
        )

        res = telegram_api.send_message(settings.token, settings.owner_chat_id, test_text, parse_mode="HTML")
        if res.get("ok"):
            return Response({"ok": True})
        else:
            desc = res.get("description", "Не удалось отправить сообщение в Telegram")
            return Response({"ok": False, "detail": desc}, status=status.HTTP_400_BAD_REQUEST)


class TelegramBotTestAIView(APIView):
    """
    POST /api/main/telegram-bot/test-ai/
    Короткий запрос к Google Gemini для проверки работоспособности ключа.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        company = _get_company(request)
        settings, _ = TelegramBotSettings.objects.get_or_create(company=company)

        ai_key = ai_service.get_effective_ai_key(settings.ai_key)
        if not ai_key:
            return Response(
                {"detail": "Ключ Google Gemini не указан (ни в компании, ни на сервере)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        res = ai_service.test_ai(ai_key)
        if res.get("ok"):
            return Response({"ok": True, "answer": res.get("answer"), "model": res.get("model")})
        else:
            return Response({"ok": False, "detail": res.get("error")}, status=status.HTTP_400_BAD_REQUEST)


# =========================================================================
# 4.2 Аналитика бота
# =========================================================================

class TelegramBotStatsView(APIView):
    """
    GET /api/main/telegram-bot/stats/?date_from=2026-09-01&date_to=2026-09-30
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = _get_company(request)
        qs = TelegramInquiry.objects.filter(company=company)

        date_from_str = request.query_params.get("date_from")
        if date_from_str:
            df = parse_date(date_from_str)
            if df:
                qs = qs.filter(created_at__date__gte=df)

        date_to_str = request.query_params.get("date_to")
        if date_to_str:
            dt = parse_date(date_to_str)
            if dt:
                qs = qs.filter(created_at__date__lte=dt)

        # Общие агрегации за 1 запрос
        agg = qs.aggregate(
            total_messages=Count("id"),
            total_people=Count("chat_id", distinct=True),
            total_orders=Count("order_id", distinct=True, filter=Q(order__isnull=False)),
            total_orders_sum=Coalesce(
                Sum("order__total", filter=Q(order__isnull=False)),
                Value(ZERO_MONEY, output_field=MONEY_FIELD),
            ),
        )

        messages = agg["total_messages"] or 0
        people = agg["total_people"] or 0
        orders = agg["total_orders"] or 0
        orders_total = agg["total_orders_sum"] or ZERO_MONEY

        # Группировка по дням за 1 запрос
        daily = (
            qs.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(
                d_messages=Count("id"),
                d_people=Count("chat_id", distinct=True),
                d_orders=Count("order_id", distinct=True, filter=Q(order__isnull=False)),
            )
            .order_by("day")
        )

        by_day = [
            {
                "date": d["day"].isoformat() if d["day"] else "",
                "messages": d["d_messages"],
                "people": d["d_people"],
                "orders": d["d_orders"],
            }
            for d in daily
            if d["day"]
        ]

        return Response({
            "messages": messages,
            "people": people,
            "orders": orders,
            "orders_total": f"{orders_total:.2f}",
            "by_day": by_day,
        })


class TelegramBotInquiriesView(generics.ListAPIView):
    """
    GET /api/main/telegram-bot/inquiries/?date_from=&date_to=&chat_id=&page=
    Лента обращений покупателей (новые сверху).
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TelegramInquirySerializer
    pagination_class = TelegramInquiryPagination

    def get_queryset(self):
        company = _get_company(self.request)
        qs = TelegramInquiry.objects.filter(company=company).select_related("order")

        chat_id = self.request.query_params.get("chat_id")
        if chat_id:
            qs = qs.filter(chat_id=chat_id)

        date_from_str = self.request.query_params.get("date_from")
        if date_from_str:
            df = parse_date(date_from_str)
            if df:
                qs = qs.filter(created_at__date__gte=df)

        date_to_str = self.request.query_params.get("date_to")
        if date_to_str:
            dt = parse_date(date_to_str)
            if dt:
                qs = qs.filter(created_at__date__lte=dt)

        return qs.order_by("-created_at")


class TelegramBotCustomersView(APIView):
    """
    GET /api/main/telegram-bot/customers/?date_from=&date_to=
    Список покупателей бота: chat_id, name, username, messages, orders, last_at, client_id.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = _get_company(request)

        date_from_str = request.query_params.get("date_from")
        date_to_str = request.query_params.get("date_to")

        df = parse_date(date_from_str) if date_from_str else None
        dt = parse_date(date_to_str) if date_to_str else None

        # Если есть фильтрация по датам, считаем количество сообщений и заказов в рамках периода
        inq_filter = Q(company=company)
        if df:
            inq_filter &= Q(created_at__date__gte=df)
        if dt:
            inq_filter &= Q(created_at__date__lte=dt)

        profiles = (
            TelegramCustomerProfile.objects.filter(company=company)
            .select_related("client")
            .order_by("-last_at")
        )

        # Вычисляем периодные агрегаты по chat_id если задан фильтр дат
        period_stats = {}
        if df or dt:
            period_rows = (
                TelegramInquiry.objects.filter(inq_filter)
                .values("chat_id")
                .annotate(
                    msg_cnt=Count("id"),
                    ord_cnt=Count("order_id", distinct=True, filter=Q(order__isnull=False)),
                )
            )
            for r in period_rows:
                period_stats[r["chat_id"]] = (r["msg_cnt"], r["ord_cnt"])

        # Карта привязанных клиентов компании
        client_map = {
            c.telegram_chat_id: str(c.id)
            for c in Client.objects.filter(company=company, telegram_chat_id__isnull=False)
            if c.telegram_chat_id
        }

        results = []
        for p in profiles:
            if df or dt:
                if p.chat_id not in period_stats:
                    continue
                msgs, ords = period_stats[p.chat_id]
            else:
                msgs = p.messages_count
                ords = p.orders_count

            cid = str(p.client_id) if p.client_id else client_map.get(p.chat_id)

            results.append({
                "chat_id": p.chat_id,
                "name": p.name,
                "username": p.username,
                "messages": msgs,
                "orders": ords,
                "last_at": p.last_at.isoformat() if p.last_at else None,
                "client_id": cid,
            })

        return Response(results)


# =========================================================================
# 4.3 Уведомление о закрытии смены из кассы
# =========================================================================

class TelegramBotNotifyShiftClosedView(APIView):
    """
    POST /api/main/telegram-bot/notify/shift-closed/
    Тело: {"shift_id": "..."}
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        company = _get_company(request)
        shift_id = request.data.get("shift_id")
        if not shift_id:
            raise ValidationError({"shift_id": "Обязательное поле."})

        ok = events_handler.send_shift_closed_notification(company.id, shift_id)
        return Response({"ok": ok})
