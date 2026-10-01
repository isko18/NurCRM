import re
import urllib.parse
from decimal import Decimal
from datetime import timedelta
import logging

from django.utils import timezone
from django.db.models import Sum, Count, Q, Value, DecimalField, F
from django.db.models.functions import Coalesce
from django.core.cache import cache

from apps.main.telegram_bot.services import telegram_api, ai_service

logger = logging.getLogger("telegram_bot.owner")

ZERO_MONEY = Decimal("0.00")
MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


def _fmt_money(val) -> str:
    if val is None:
        return "0.00"
    try:
        return f"{Decimal(str(val)):,.2f}".replace(",", " ")
    except Exception:
        return str(val)


def _clean_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "996" + digits[1:]
    return digits


# =========================================================================
# Команды и отчёты
# =========================================================================

def get_report_today(company) -> str:
    """Выручка за сегодня: сумма, количество чеков, средний чек, виды оплат."""
    from apps.main.models import Sale, SalePayment

    today = timezone.localdate()
    sales_qs = Sale.objects.filter(
        company=company,
        status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
        paid_at__date=today,
    )

    agg = sales_qs.aggregate(
        total_rev=Coalesce(Sum("total"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        total_count=Count("id"),
        cash_amt=Coalesce(Sum("cash_amount"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        card_amt=Coalesce(Sum("card_amount"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        debt_amt=Coalesce(Sum("debt_initial"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
    )

    rev = agg["total_rev"] or ZERO_MONEY
    cnt = agg["total_count"] or 0
    avg_check = (rev / cnt) if cnt > 0 else ZERO_MONEY

    lines = [
        f"📊 <b>Выручка за сегодня ({today.strftime('%d.%m.%Y')})</b>",
        f"💰 <b>Итого выручка:</b> {_fmt_money(rev)} сом",
        f"🧾 <b>Количество чеков:</b> {cnt}",
        f"📈 <b>Средний чек:</b> {_fmt_money(avg_check)} сом",
        "──────────────",
        f"💵 Наличные: {_fmt_money(agg['cash_amt'])} сом",
        f"💳 Безналичные (карта/перевод): {_fmt_money(agg['card_amt'])} сом",
        f"📝 В долг: {_fmt_money(agg['debt_amt'])} сом",
    ]
    return "\n".join(lines)


def get_report_week(company) -> str:
    """Выручка за 7 дней с разбивкой по дням."""
    from apps.main.models import Sale
    from django.db.models.functions import TruncDate

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    today = timezone.localdate()

    sales_qs = Sale.objects.filter(
        company=company,
        status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
        paid_at__gte=week_ago,
    )

    total_rev = sales_qs.aggregate(t=Coalesce(Sum("total"), Value(ZERO_MONEY, output_field=MONEY_FIELD)))["t"]
    total_cnt = sales_qs.count()

    daily = (
        sales_qs.annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(
            rev=Coalesce(Sum("total"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
            cnt=Count("id"),
        )
        .order_by("day")
    )

    lines = [
        "📅 <b>Выручка за последние 7 дней</b>",
        f"💰 <b>Всего:</b> {_fmt_money(total_rev)} сом (чеков: {total_cnt})",
        "──────────────",
    ]

    for d in daily:
        day_date = d["day"]
        day_name = day_date.strftime("%d.%m") if day_date else ""
        lines.append(f"• {day_name}: {_fmt_money(d['rev'])} сом ({d['cnt']} чеков)")

    return "\n".join(lines)


def get_report_top(company) -> str:
    """Топ-8 товаров за последние 7 дней."""
    from apps.main.models import SaleItem, Sale

    week_ago = timezone.now() - timedelta(days=7)
    items_qs = (
        SaleItem.objects.filter(
            sale__company=company,
            sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
            sale__paid_at__gte=week_ago,
        )
        .values("name_snapshot")
        .annotate(
            total_rev=Coalesce(Sum(F("unit_price") * F("quantity") - F("line_discount")), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
            total_qty=Coalesce(Sum("quantity"), Value(Decimal("0.00"), output_field=MONEY_FIELD)),
        )
        .order_by("-total_rev")[:8]
    )

    lines = [
        "🏆 <b>Топ-8 товаров за 7 дней (по выручке)</b>",
        "──────────────",
    ]
    if not items_qs:
        lines.append("Нет продаж за этот период.")
        return "\n".join(lines)

    for i, item in enumerate(items_qs, 1):
        name = item.get("name_snapshot") or "Без названия"
        rev = _fmt_money(item["total_rev"])
        qty = item["total_qty"]
        lines.append(f"{i}. <b>{name}</b> — {rev} сом ({qty:g} шт)")

    return "\n".join(lines)


def get_report_abc(company) -> str:
    """ABC анализ за последние 30 дней."""
    from apps.main.models import SaleItem, Sale

    month_ago = timezone.now() - timedelta(days=30)
    items = list(
        SaleItem.objects.filter(
            sale__company=company,
            sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
            sale__paid_at__gte=month_ago,
        )
        .values("name_snapshot")
        .annotate(revenue=Coalesce(Sum(F("unit_price") * F("quantity") - F("line_discount")), Value(ZERO_MONEY, output_field=MONEY_FIELD)))
        .order_by("-revenue")
    )

    if not items:
        return "📊 <b>ABC анализ (30 дней)</b>\nДанных о продажах за последние 30 дней пока нет."

    total_revenue = sum(it["revenue"] for it in items)
    if total_revenue <= Decimal("0.00"):
        return "📊 <b>ABC анализ (30 дней)</b>\nВыручка за 30 дней равна 0."

    group_a, group_b, group_c = [], [], []
    cum = Decimal("0.00")

    for it in items:
        rev = it["revenue"]
        cum += rev
        share = cum / total_revenue
        p_name = it["name_snapshot"] or "Без названия"
        if share <= Decimal("0.80"):
            group_a.append((p_name, rev))
        elif share <= Decimal("0.95"):
            group_b.append((p_name, rev))
        else:
            group_c.append((p_name, rev))

    lines = [
        f"📊 <b>ABC анализ товаров (30 дней, всего {_fmt_money(total_revenue)} сом)</b>",
        "──────────────",
        f"🟢 <b>Группа A (80% выручки, {len(group_a)} товаров):</b>",
    ]
    for name, rev in group_a[:5]:
        lines.append(f"  • {name} ({_fmt_money(rev)} сом)")
    if len(group_a) > 5:
        lines.append(f"  ... и ещё {len(group_a) - 5}")

    lines.append(f"\n🟡 <b>Группа B (15% выручки, {len(group_b)} товаров):</b>")
    for name, rev in group_b[:3]:
        lines.append(f"  • {name} ({_fmt_money(rev)} сом)")

    lines.append(f"\n🔴 <b>Группа C (5% выручки, {len(group_c)} товаров):</b>")
    lines.append(f"  Всего {len(group_c)} наименований со слабыми продажами.")

    return "\n".join(lines)


def get_report_sezon(company) -> str:
    """Анализ динамики (растущие и падающие товары за последние 14 дней)."""
    from apps.main.models import SaleItem, Sale

    now = timezone.now()
    p1_start = now - timedelta(days=7)
    p2_start = now - timedelta(days=14)

    # Продажи за последние 7 дней
    curr_sales = {
        r["name_snapshot"]: r["qty"]
        for r in SaleItem.objects.filter(
            sale__company=company,
            sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
            sale__paid_at__gte=p1_start,
        )
        .values("name_snapshot")
        .annotate(qty=Coalesce(Sum("quantity"), Value(Decimal("0.00"), output_field=MONEY_FIELD)))
    }

    # Продажи за предыдущие 7 дней
    prev_sales = {
        r["name_snapshot"]: r["qty"]
        for r in SaleItem.objects.filter(
            sale__company=company,
            sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
            sale__paid_at__gte=p2_start,
            sale__paid_at__lt=p1_start,
        )
        .values("name_snapshot")
        .annotate(qty=Coalesce(Sum("quantity"), Value(Decimal("0.00"), output_field=MONEY_FIELD)))
    }

    growers = []
    for name, cur_q in curr_sales.items():
        prev_q = prev_sales.get(name, Decimal("0.00"))
        if cur_q > prev_q:
            growers.append((name, cur_q, prev_q))

    growers.sort(key=lambda x: (x[1] - x[2]), reverse=True)

    lines = [
        "📈 <b>Сезонность и тренды (динамика за 2 недели)</b>",
        "──────────────",
        "🔥 <b>Набирают популярность:</b>",
    ]
    if growers:
        for name, c, p in growers[:6]:
            lines.append(f"• <b>{name}</b>: {c:g} шт (было {p:g} шт)")
    else:
        lines.append("Недостаточно данных для выделения растущих трендов.")

    return "\n".join(lines)


def get_report_soveti(company) -> str:
    """Советы владельцу на основе остатков, долгов и продаж."""
    from apps.main.models import Product, Sale, Client

    today = timezone.localdate()
    low_stock_count = Product.objects.filter(company=company, quantity__lte=3).exclude(status=Product.Status.ARCHIVED).count()
    debtors_count = Client.objects.filter(company=company).count()

    lines = [
        "💡 <b>Рекомендации для вашего магазина:</b>",
        "──────────────",
    ]
    if low_stock_count > 0:
        lines.append(f"📦 <b>Склад:</b> У вас заканчивается {low_stock_count} товаров (остаток ≤ 3). Проверьте команду /zakaz и сделайте дозаказ.")
    else:
        lines.append("📦 <b>Склад:</b> Критических остатков нет, запасы в норме.")

    lines.append("💰 <b>Продажи:</b> Обратите внимание на товары группы A из команды /abc — они приносят 80% вашей выручки. Обеспечьте их постоянное наличие.")
    lines.append("📲 <b>Долги:</b> Проверьте список должников через /dolgi и отправьте им напоминания в WhatsApp или Telegram.")

    return "\n".join(lines)


def get_report_zakaz(company) -> str:
    """Товары, которые пора заказать (остаток <= minimum_quantity или <= 3)."""
    from apps.main.models import Product

    qs = Product.objects.filter(company=company).exclude(status=Product.Status.ARCHIVED).filter(
        Q(minimum_quantity__gt=0, quantity__lte=F("minimum_quantity")) | Q(quantity__lte=3)
    ).order_by("quantity")[:15]

    lines = [
        "📝 <b>Что заказать (заканчивающиеся товары)</b>",
        "──────────────",
    ]
    if not qs:
        lines.append("Все товары в достаточном количестве!")
        return "\n".join(lines)

    for p in qs:
        min_q = p.minimum_quantity or 5
        cur_q = p.quantity or Decimal("0.00")
        suggested = max(Decimal("5"), min_q * 2 - cur_q)
        lines.append(f"• <b>{p.name}</b>\n  Остаток: {cur_q:g} шт | Мин: {min_q} | Рекомендуем: {suggested:g} шт")

    return "\n".join(lines)


def get_report_ostatki(company) -> str:
    """Товары с малым остатком (<= 3)."""
    from apps.main.models import Product

    qs = Product.objects.filter(company=company, quantity__lte=3).exclude(status=Product.Status.ARCHIVED).order_by("quantity")[:15]
    lines = [
        "⚠️ <b>Остатки товаров (≤ 3 шт)</b>",
        "──────────────",
    ]
    if not qs:
        lines.append("Товаров с критическим остатком нет.")
        return "\n".join(lines)

    for p in qs:
        lines.append(f"• <b>{p.name}</b>: {p.quantity or 0:g} шт (цена: {_fmt_money(p.price)} сом)")

    return "\n".join(lines)


def get_report_dolgi(company) -> str:
    """Список должников со ссылками на WhatsApp."""
    from apps.main.models import Client, Sale

    # Долги клиентов
    debt_sales = (
        Sale.objects.filter(
            company=company,
            client__isnull=False,
            debt_remaining__gt=ZERO_MONEY,
        )
        .values("client_id", "client__full_name", "client__phone")
        .annotate(total_debt=Sum("debt_remaining"))
        .order_by("-total_debt")[:15]
    )

    lines = [
        "💳 <b>Список должников магазина</b>",
        "──────────────",
    ]
    if not debt_sales:
        lines.append("Должников нет! Все долги погашены.")
        return "\n".join(lines)

    company_name = getattr(company, "name", "магазине")
    for d in debt_sales:
        name = d.get("client__full_name") or "Клиент"
        phone = d.get("client__phone") or ""
        amount = _fmt_money(d["total_debt"])

        clean_ph = _clean_phone(phone)
        wa_text = f"Здравствуйте, {name}! Напоминаем о вашей задолженности в магазине {company_name} на сумму {amount} сом."
        wa_url = f"https://wa.me/{clean_ph}?text={urllib.parse.quote(wa_text)}" if clean_ph else None

        if wa_url:
            lines.append(f"👤 <b>{name}</b>: {amount} сом\n📱 <a href=\"{wa_url}\">Написать в WhatsApp ({phone})</a>\n")
        else:
            lines.append(f"👤 <b>{name}</b>: {amount} сом (нет телефона)\n")

    lines.append("<i>Подсказка: напишите «разослать напоминания о долге», чтобы уведомить подписанных покупателей через Telegram.</i>")
    return "\n".join(lines)


def search_product_price(company, term: str) -> str:
    """Поиск цены и остатка товара для владельца."""
    from apps.main.models import Product

    qs = Product.objects.filter(company=company, name__icontains=term).exclude(status=Product.Status.ARCHIVED)[:5]
    if not qs:
        return f"🔍 По запросу «{term}» товаров не найдено."

    lines = [f"🔍 <b>Результаты поиска «{term}»:</b>", "──────────────"]
    for p in qs:
        barcode_str = f" [штрихкод: {p.barcode}]" if p.barcode else ""
        lines.append(f"• <b>{p.name}</b>{barcode_str}\n  Цена: {_fmt_money(p.price)} сом | Остаток: {p.quantity or 0:g} шт")
    return "\n".join(lines)


def get_inquiries_stats(company) -> str:
    """Статистика обращений покупателей."""
    from apps.main.telegram_bot.models import TelegramInquiry

    today = timezone.localdate()
    today_inq = TelegramInquiry.objects.filter(company=company, created_at__date=today)
    total_inq = TelegramInquiry.objects.filter(company=company)

    t_count = today_inq.count()
    t_people = today_inq.values("chat_id").distinct().count()
    t_orders = today_inq.filter(order__isnull=False).count()

    total_count = total_inq.count()
    total_people = total_inq.values("chat_id").distinct().count()
    total_orders = total_inq.filter(order__isnull=False).count()

    lines = [
        "📩 <b>Статистика обращений покупателей</b>",
        "──────────────",
        f"📅 <b>За сегодня ({today.strftime('%d.%m.%Y')}):</b>",
        f"• Сообщений: {t_count}",
        f"• Покупателей: {t_people}",
        f"• Оформлено заказов: {t_orders}",
        "",
        "📊 <b>За все время:</b>",
        f"• Сообщений: {total_count}",
        f"• Покупателей: {total_people}",
        f"• Заказов: {total_orders}",
    ]
    return "\n".join(lines)


def get_help_message() -> str:
    """Справка по командам."""
    return (
        "🤖 <b>Команды бота для владельца NurCRM:</b>\n"
        "──────────────\n"
        "/segodnya — выручка и касса за сегодня\n"
        "/nedelya — продажи за 7 дней по дням\n"
        "/top — топ-8 товаров за 7 дней\n"
        "/abc — ABC анализ товаров за 30 дней\n"
        "/sezon — сезонные товары и растущие тренды\n"
        "/soveti — рекомендации по продажам\n"
        "/zakaz — что заказать у поставщиков\n"
        "/ostatki — товары с малым остатком (≤ 3 шт)\n"
        "/dolgi — список должников со ссылками на WhatsApp\n"
        "/help — это справочное меню\n\n"
        "💬 <b>Или пишите обычными словами:</b>\n"
        "• «сколько заработали сегодня»\n"
        "• «кто должен» / «ким карыз»\n"
        "• «цена кола» / «баасы кола»\n"
        "• «сколько обращений было»\n"
        "• «разослать напоминания о долге»\n"
        "• свободный вопрос ИИ (например: «почему упала выручка», «как поднять продажи»)"
    )


# =========================================================================
# Рассылка напоминаний о долгах подписанным клиентам (TZ 2)
# =========================================================================

def blast_debt_reminders(settings, company) -> str:
    """Рассылает напоминания о долге клиентам, у которых указан telegram_chat_id."""
    from apps.main.telegram_bot.tasks import send_telegram_debt_reminders
    send_telegram_debt_reminders.delay(str(company.id))
    return "🚀 Рассылка напоминаний о долге запущена в фоновом режиме. Подписанные клиенты с долгом получат сообщение в Telegram."


# =========================================================================
# Построение контекста для ИИ владельца (TZ 5.2)
# =========================================================================

def build_owner_system_summary(company, user_question: str = "") -> str:
    """
    Строит короткую сводку магазина для передачи в systemInstruction ИИ:
    - Выручка сегодня и за 7 дней
    - Топ-8 товаров за 7 дней
    - До 10 товаров с остатком <= 3
    - Товары из вопроса (цена, остаток)
    - Счёт обращений покупателей
    - Правила: коротко, на языке собеседника, строго только цифры из сводки, не выдумывать.
    """
    from apps.main.models import Sale, SaleItem, Product
    from apps.main.telegram_bot.models import TelegramInquiry

    today = timezone.localdate()
    week_ago = timezone.now() - timedelta(days=7)

    # 1. Выручка сегодня
    today_sales = Sale.objects.filter(
        company=company,
        status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
        paid_at__date=today,
    ).aggregate(t=Coalesce(Sum("total"), Value(ZERO_MONEY, output_field=MONEY_FIELD)), c=Count("id"))
    rev_today = today_sales["t"] or ZERO_MONEY
    cnt_today = today_sales["c"] or 0

    # 2. Выручка за 7 дней
    week_sales = Sale.objects.filter(
        company=company,
        status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
        paid_at__gte=week_ago,
    ).aggregate(t=Coalesce(Sum("total"), Value(ZERO_MONEY, output_field=MONEY_FIELD)), c=Count("id"))
    rev_week = week_sales["t"] or ZERO_MONEY
    cnt_week = week_sales["c"] or 0

    # 3. Топ-8 товаров
    top_items = list(
        SaleItem.objects.filter(
            sale__company=company,
            sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
            sale__paid_at__gte=week_ago,
        )
        .values("name_snapshot")
        .annotate(
            rev=Coalesce(
                Sum(F("unit_price") * F("quantity") - F("line_discount")),
                Value(ZERO_MONEY, output_field=MONEY_FIELD),
            )
        )
        .order_by("-rev")[:8]
    )
    top_text = ", ".join(f"{it['name_snapshot']} ({_fmt_money(it['rev'])} сом)" for it in top_items) or "нет данных"

    # 4. До 10 товаров с остатком <= 3
    low_stock = list(
        Product.objects.filter(company=company, quantity__lte=3)
        .exclude(status=Product.Status.ARCHIVED)
        .values("name", "quantity")
        .order_by("quantity")[:10]
    )
    low_text = ", ".join(f"{p['name']} ({p['quantity']:g} шт)" for p in low_stock) or "нет критических остатков"

    # 5. Обращения покупателей сегодня
    inq_count = TelegramInquiry.objects.filter(company=company, created_at__date=today).count()

    # 6. Товары из вопроса
    matched_products = []
    if user_question:
        words = [w for w in re.split(r"[^\w]+", user_question.lower()) if len(w) >= 3]
        if words:
            query = Q()
            for w in words[:4]:
                query |= Q(name__icontains=w)
            for p in Product.objects.filter(company=company).exclude(status=Product.Status.ARCHIVED).filter(query)[:5]:
                matched_products.append(f"{p.name}: цена {_fmt_money(p.price)} сом, остаток {p.quantity or 0:g} шт")

    matched_text = ""
    if matched_products:
        matched_text = f"\nТовары по теме вопроса:\n" + "\n".join(f"- {mp}" for mp in matched_products)

    summary = (
        f"СВОДКА МАГАЗИНА «{getattr(company, 'name', '')}» (на сегодня {today.strftime('%d.%m.%Y')}):\n"
        f"- Выручка сегодня: {_fmt_money(rev_today)} сом (продаж: {cnt_today})\n"
        f"- Выручка за 7 дней: {_fmt_money(rev_week)} сом (продаж: {cnt_week})\n"
        f"- Топ-8 товаров за 7 дней: {top_text}\n"
        f"- Заканчивающиеся товары (остаток <= 3): {low_text}\n"
        f"- Обращений покупателей в бот сегодня: {inq_count}\n"
        f"{matched_text}\n\n"
        "ПРАВИЛА ДЛЯ ИИ:\n"
        "1. Ты персональный бизнес-ассистент владельца магазина NurCRM.\n"
        "2. Отвечай коротко, чётко, уважительно на языке собеседника (русский или кыргызский).\n"
        "3. СТРОГО опирайся ТОЛЬКО на цифры из этой сводки! Никогда не выдумывай показатели.\n"
        "4. При нехватке точных данных вежливо подскажи команду боту (/segodnya, /nedelya, /top, /abc, /dolgi, /ostatki, /zakaz).\n"
    )
    return summary


# =========================================================================
# Главный диспетчер сообщений владельца
# =========================================================================

def handle_owner_message(settings, chat_id: str, text: str, is_voice: bool = False) -> None:
    """Обработка сообщения от владельца."""
    token = settings.token
    if not token:
        logger.error("Bot token not configured for company %s", settings.company_id)
        return

    company = settings.company
    norm = (text or "").strip().lower()

    # 1. Команды с слэшем
    if norm in ("/start", "/help", "помощь", "жардам"):
        reply = get_help_message()
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/segodnya":
        reply = get_report_today(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/nedelya":
        reply = get_report_week(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/top":
        reply = get_report_top(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/abc":
        reply = get_report_abc(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/sezon":
        reply = get_report_sezon(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/soveti":
        reply = get_report_soveti(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/zakaz":
        reply = get_report_zakaz(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/ostatki":
        reply = get_report_ostatki(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/dolgi":
        reply = get_report_dolgi(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # 2. Ключевые слова («TelegramAssistant»)
    # Выручка сегодня
    if any(k in norm for k in ["сколько заработали сегодня", "выручка сегодня", "бүгүн канча түшүм", "касса бүгүн", "сколько сегодня"]):
        reply = get_report_today(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Выручка за неделю
    if any(k in norm for k in ["выручка за неделю", "сколько за неделю", "жумалык түшүм", "за неделю"]):
        reply = get_report_week(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Должники
    if any(k in norm for k in ["кто должен", "должники", "долги", "ким карыз", "карыздар", "карыз"]):
        reply = get_report_dolgi(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Разослать напоминания о долгах
    if any(k in norm for k in ["разослать напоминания о долге", "разослать напоминания", "напомнить о долгах", "карыздарды эскертүү"]):
        reply = blast_debt_reminders(settings, company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Поиск цены: цена X, баасы X, почем X
    m_price = re.search(r"(?:цена|баасы|почем|стоимость|канча турат)\s+(.+)", norm)
    if m_price:
        search_term = m_price.group(1).strip()
        reply = search_product_price(company, search_term)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Топ товаров
    if any(k in norm for k in ["топ товаров", "топ сатуу", "ходовые товары", "лучшие товары", "топ продаж"]):
        reply = get_report_top(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Что заказать
    if any(k in norm for k in ["что заказать", "заказ товаров", "эмне заказ кылуу керек", "что докупить"]):
        reply = get_report_zakaz(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Что заканчивается
    if any(k in norm for k in ["что заканчивается", "остатки", "аз калган товарлар", "мало товара"]):
        reply = get_report_ostatki(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # Обращения
    if any(k in norm for k in ["сколько обращений было", "обращения", "канча кайрылуу болду", "статистика обращений"]):
        reply = get_inquiries_stats(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # 3. Разговорные фразы -> ИИ (Gemini)
    if not settings.ai_enabled:
        telegram_api.send_message(
            token, chat_id,
            "ИИ в настройках отключён. Воспользуйтесь командами из меню /help."
        )
        return

    ai_key = ai_service.get_effective_ai_key(settings.ai_key)
    if not ai_key:
        telegram_api.send_message(
            token, chat_id,
            "Ключ Google Gemini не настроен. Настройте его в кабинете или воспользуйтесь командами из /help."
        )
        return

    # Чат-память: последние 8 реплик из Redis
    cache_key = f"tg_chat_history:{company.id}:{chat_id}"
    history = cache.get(cache_key) or []

    # Добавляем реплику пользователя
    history.append({"role": "user", "parts": [{"text": text}]})
    # Держим не более 8 реплик
    history = history[-8:]

    system_instruction = build_owner_system_summary(company, user_question=text)

    try:
        ai_reply, _model = ai_service.generate_chat_response(
            api_key=ai_key,
            system_instruction=system_instruction,
            contents=history,
            temperature=0.4,
            max_tokens=800,
        )
        if not ai_reply:
            ai_reply = "Не удалось сгенерировать ответ. Попробуйте сформулировать иначе или используйте /help."

        # Сохраняем ответ в память
        history.append({"role": "model", "parts": [{"text": ai_reply}]})
        cache.set(cache_key, history[-8:], timeout=86400)

        # Отправка текстового ответа
        telegram_api.send_message(token, chat_id, ai_reply)

    except Exception as exc:
        logger.error("Owner AI conversation failed: %s", exc)
        telegram_api.send_message(
            token, chat_id,
            f"В данный момент ИИ недоступен. Воспользуйтесь командами из списка /help."
        )
