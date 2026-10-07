import re
import urllib.parse
from decimal import Decimal
from datetime import timedelta
import html
import logging

from django.utils import timezone
from django.db.models import Sum, Count, Q, Value, DecimalField, F
from django.db.models.functions import Coalesce
from django.core.cache import cache

from apps.main.telegram_bot.services import telegram_api, ai_service
from apps.main.telegram_bot.services.photo_service import format_amount, format_qty

logger = logging.getLogger("telegram_bot.owner")

ZERO_MONEY = Decimal("0.00")
MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


def _fmt_money(val) -> str:
    # ТЗ ч.9, 1.1: без лишних нулей — 660 066, 45,50
    return format_amount(val)


def _clean_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "996" + digits[1:]
    return digits


# =========================================================================
# Команды и отчёты
# =========================================================================

def get_owner_main_menu_keyboard():
    """ТЗ-09 п. 2.5: Меню владельца внизу экрана."""
    return {
        "keyboard": [
            [{"text": "📊 Сегодня"}, {"text": "💰 Касса"}],
            [{"text": "🧾 Долги"}, {"text": "📦 Остатки"}],
            [{"text": "🛒 Заказы"}, {"text": "🔔 Прокат"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def get_today_report_markup():
    """ТЗ-09 п. 2.5: Кнопки под отчётом 'Сегодня'."""
    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Обновить", "callback_data": "or:today_refresh"},
                {"text": "📈 Неделя", "callback_data": "or:week"},
            ]
        ]
    }

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
        lines.append(f"{i}. <b>{name}</b> — {rev} сом ({format_qty(qty)} шт)")

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
            lines.append(f"• <b>{name}</b>: {format_qty(c)} шт (было {format_qty(p)} шт)")
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
        lines.append(f"• <b>{p.name}</b>\n  Остаток: {format_qty(cur_q)} шт | Мин: {min_q} | Рекомендуем: {format_qty(suggested)} шт")

    return "\n".join(lines)


def get_report_ostatki(company) -> str:
    """Товары и размеры с малым остатком (<= 3). ТЗ ч. 10 п. 3.4."""
    import html
    from apps.main.models import Product, ProductVariant

    qs = Product.objects.filter(company=company, quantity__lte=3).exclude(status=Product.Status.ARCHIVED).order_by("quantity")[:15]
    lines = [
        "⚠️ <b>Заканчивающиеся товары (≤ 3 шт)</b>",
        "──────────────",
    ]
    if qs.exists():
        for p in qs:
            lines.append(f"• <b>{html.escape(p.name)}</b>: {format_qty(p.quantity or 0)} шт (цена: {_fmt_money(p.price)} сом)")

    # Варианты одежды (размер/цвет) с малым остатком
    low_vars = ProductVariant.objects.filter(
        company=company,
        is_active=True,
        quantity__gt=0,
        quantity__lte=3,
    ).select_related("product")[:15]

    if low_vars.exists():
        lines.append("──────────────")
        lines.append("👗 <b>Заканчивающиеся размеры и цвета:</b>")
        for v in low_vars:
            desc = f"{v.size} {v.color}".strip()
            lines.append(f"• {html.escape(v.product.name)} {html.escape(desc)} — <b>{format_qty(v.quantity)} шт</b>")

    if len(lines) == 2:
        lines.append("Товаров с критическим остатком нет.")

    return "\n".join(lines)


def get_clothing_sizes_report(company, search_query: str):
    """ТЗ ч. 10 п. 3.4: Таблица остатков одежды по размерам и цветам для владельца."""
    import html
    from apps.main.models import Product, ProductVariant
    from apps.main.variant_utils import sort_variants

    clean = (search_query or "").strip()
    if not clean:
        return None

    prods = Product.objects.filter(company=company, name__icontains=clean).exclude(status=Product.Status.ARCHIVED)
    prod = None
    for p in prods:
        if p.variants.filter(is_active=True).exists():
            prod = p
            break
    if not prod:
        return None

    active_vars = sort_variants(prod.variants.filter(is_active=True))
    lines = [f"📦 <b>Остатки по размерам: {html.escape(prod.name)}</b>", "──────────────"]
    total_var_qty = Decimal("0.00")
    for v in active_vars:
        qty = Decimal(str(v.quantity or 0))
        total_var_qty += qty
        size_lbl = v.size or "Без размера"
        color_lbl = v.color or "Без цвета"
        lines.append(f"• {html.escape(size_lbl)} / {html.escape(color_lbl)} — <b>{format_qty(qty)} шт.</b>")

    lines.append("──────────────")
    lines.append(f"Всего по размерам: <b>{format_qty(total_var_qty)} шт.</b> (в карточке товара: {format_qty(Decimal(str(prod.quantity or 0)))} шт.)")
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
    """Поиск цены и остатка товара для владельца с размерами и цветами."""
    from apps.main.models import Product
    from apps.main.variant_utils import active_variants, variant_prices

    qs = Product.objects.filter(company=company, name__icontains=term).exclude(status=Product.Status.ARCHIVED).prefetch_related("variants")[:5]
    if not qs:
        return f"🔍 По запросу «{term}» товаров не найдено."

    lines = [f"🔍 <b>Результаты поиска «{term}»:</b>", "──────────────"]
    for p in qs:
        barcode_str = f" [штрихкод: {p.barcode}]" if p.barcode else ""
        lines.append(f"• <b>{p.name}</b>{barcode_str}\n  Цена: {_fmt_money(p.price)} сом | Общий остаток: {format_qty(p.quantity or 0)} шт")
        active_vars = active_variants(p)
        if active_vars:
            lines.append("  <i>Размеры и цвета:</i>")
            for v in active_vars:
                parts = []
                if v.size:
                    parts.append(f"размер: {v.size}")
                if v.color:
                    parts.append(f"цвет: {v.color}")
                var_desc = ", ".join(parts) if parts else "вариант"
                price, old_price = variant_prices(v, p)
                var_price = f"{_fmt_money(price)} сом"
                if old_price is not None:
                    var_price = f"{_fmt_money(price)} сом (акция, обычная {_fmt_money(old_price)} сом)"
                lines.append(f"    - {var_desc}: остаток {format_qty(v.quantity)} шт, {var_price}")
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


def get_command_pribyl(company) -> str:
    """/pribyl: P&L за месяц (F2)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_pnl
    today = timezone.localdate()
    first_day = today.replace(day=1)
    data = fn_get_pnl(company, date_from=first_day.isoformat(), date_to=today.isoformat())
    if "error" in data:
        return f"❌ Ошибка получения P&L: {data['error']}"

    rev = _fmt_money(data.get("revenue"))
    cogs = _fmt_money(data.get("cogs"))
    gp = _fmt_money(data.get("gross_profit"))
    margin = data.get("gross_margin_percent")
    margin_str = f"{margin}%" if margin is not None else "0%"
    opex = _fmt_money(data.get("operating_expenses_total"))
    net_profit = _fmt_money(data.get("net_profit"))

    lines = [
        "📊 <b>Отчёт о прибылях и убытках (P&L) за месяц</b>",
        f"Период: {first_day.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}",
        "──────────────",
        f"💰 <b>Выручка:</b> {rev} сом",
        f"📦 <b>Себестоимость (COGS):</b> {cogs} сом",
        f"📈 <b>Валовая прибыль:</b> {gp} сом (маржа {margin_str})",
        f"📉 <b>Операционные расходы:</b> {opex} сом",
    ]
    cats = data.get("operating_expenses_by_category") or []
    for c in cats[:5]:
        lines.append(f"  • {c.get('category') or 'Прочее'}: {_fmt_money(c.get('amount'))} сом")
    lines.append("──────────────")
    lines.append(f"💵 <b>Чистая прибыль:</b> {net_profit} сом")
    return "\n".join(lines)


def get_command_dengi(company) -> str:
    """/dengi: Cash Flow за месяц (F3)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_cashflow
    today = timezone.localdate()
    first_day = today.replace(day=1)
    data = fn_get_cashflow(company, date_from=first_day.isoformat(), date_to=today.isoformat())
    if "error" in data:
        return f"❌ Ошибка получения Cash Flow: {data['error']}"

    inflow = data.get("inflow", {})
    outflow = data.get("outflow", {})
    net_cf = _fmt_money(data.get("net_cashflow"))

    lines = [
        "💸 <b>Движение денег (Cash Flow) за месяц</b>",
        f"Период: {first_day.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}",
        "──────────────",
        f"📥 <b>Всего приход:</b> {_fmt_money(inflow.get('total'))} сом",
        f"  • Продажи (наличные): {_fmt_money(inflow.get('cash_sales'))} сом",
        f"  • Продажи (безнал): {_fmt_money(inflow.get('non_cash_sales'))} сом",
        f"  • Оплата долгов: {_fmt_money(inflow.get('debt_repayments'))} сом",
        f"  • Прочее: {_fmt_money(inflow.get('other_inflow'))} сом",
        "──────────────",
        f"📤 <b>Всего расход:</b> {_fmt_money(outflow.get('total'))} сом",
        f"  • Поставщикам: {_fmt_money(outflow.get('supplier_payments'))} сом",
        f"  • Зарплаты: {_fmt_money(outflow.get('salary_payments'))} сом",
        f"  • Прочее: {_fmt_money(outflow.get('other_outflow'))} сом",
        "──────────────",
        f"⚖️ <b>Чистый денежный поток:</b> {net_cf} сом",
    ]
    return "\n".join(lines)


def get_command_sklad(company) -> str:
    """/sklad: склад: позиций, стоимость по закупке и продаже, мало на складе (F6)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_stock
    data = fn_get_stock(company, limit=10)
    if "error" in data:
        return f"❌ Ошибка получения склада: {data['error']}"

    cnt = data.get("total_products_count", 0)
    cost_val = _fmt_money(data.get("inventory_cost_value"))
    retail_val = _fmt_money(data.get("inventory_retail_value"))
    low_cnt = data.get("low_stock_count", 0)
    rule = data.get("low_stock_rule", "quantity <= 3")

    lines = [
        "📦 <b>Складской учёт и остатки</b>",
        "──────────────",
        f"🏷 <b>Всего позиций в каталоге:</b> {cnt}",
        f"💰 <b>Стоимость склада (в закупке):</b> {cost_val} сом",
        f"🏷 <b>Стоимость склада (в продаже):</b> {retail_val} сом",
        f"⚠️ <b>Заканчивается товаров ({rule}):</b> {low_cnt} шт",
    ]
    prods = data.get("products", [])
    if prods:
        lines.append("──────────────")
        lines.append("<b>Примеры позиций:</b>")
        for p in prods[:5]:
            lines.append(f"• <b>{p.get('name')}</b>: {format_qty(p.get('quantity'))} {p.get('unit')} (цена: {_fmt_money(p.get('price'))} сом, закуп: {_fmt_money(p.get('purchase_price'))} сом)")
    return "\n".join(lines)


def get_command_mertvyi(company) -> str:
    """/mertvyi: товары без продаж 30 дней (F7)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_dead_stock
    data = fn_get_dead_stock(company, days=30, limit=10)
    if "error" in data:
        return f"❌ Ошибка: {data['error']}"

    cnt = data.get("dead_products_count", 0)
    frozen = _fmt_money(data.get("frozen_money_cost"))
    days = data.get("days_without_sales", 30)

    lines = [
        f"🕸 <b>Неликвидные товары (без продаж {days} дней)</b>",
        "──────────────",
        f"📦 <b>Товаров без движения:</b> {cnt}",
        f"🧊 <b>Заморожено денег (в закупке):</b> {frozen} сом",
    ]
    prods = data.get("products", [])
    if prods:
        lines.append("──────────────")
        for p in prods[:8]:
            lines.append(f"• <b>{p.get('name')}</b>: {format_qty(p.get('quantity'))} {p.get('unit')} (закуп: {_fmt_money(p.get('frozen_cost'))} сом)")
    else:
        lines.append("Зависших товаров не обнаружено — все товары продаются!")
    return "\n".join(lines)


def get_command_smena(company) -> str:
    """/smena: текущая смена (F11)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_shift
    data = fn_get_shift(company, which="current")
    if "error" in data:
        return f"❌ Ошибка: {data['error']}"

    status_str = "🟢 Открыта" if data.get("status") == "open" else f"⚪ Закрыта ({data.get('status')})"
    cashier = data.get("cashier") or "Не указан"
    rev = _fmt_money(data.get("revenue"))
    exp_cash = _fmt_money(data.get("expected_cash"))

    lines = [
        "🧾 <b>Отчёт кассовой смены</b>",
        f"Статус: {status_str} | Кассир: {cashier}",
        "──────────────",
        f"💰 <b>Выручка за смену:</b> {rev} сом",
        f"💵 Наличные продажи: {_fmt_money(data.get('cash_sales'))} сом",
        f"💳 Безнал: {_fmt_money(data.get('card_sales'))} сом",
        f"📝 В долг: {_fmt_money(data.get('debt_sales'))} сом",
        "──────────────",
        f"📥 Внесения в кассу: {_fmt_money(data.get('cash_in'))} сом",
        f"📤 Изъятия из кассы: {_fmt_money(data.get('cash_out'))} сом",
        f"💵 Оплата долгов налом: {_fmt_money(data.get('debt_repayments_cash'))} сом",
        "──────────────",
        f"🎯 <b>Ожидаемая наличность в кассе:</b> {exp_cash} сом",
    ]
    if data.get("actual_cash") is not None:
        lines.append(f"💵 Фактическая наличность: {_fmt_money(data.get('actual_cash'))} сом")
        diff = _fmt_money(data.get("difference"))
        lines.append(f"⚖️ Расхождение: {diff} сом")
    return "\n".join(lines)


def get_command_vozvraty(company) -> str:
    """/vozvraty: возвраты за неделю (F12)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_returns
    today = timezone.localdate()
    week_ago = today - timedelta(days=7)
    data = fn_get_returns(company, date_from=week_ago.isoformat(), date_to=today.isoformat())
    if "error" in data:
        return f"❌ Ошибка: {data['error']}"

    amt = _fmt_money(data.get("total_returns_amount"))
    cnt = data.get("total_returns_count", 0)

    lines = [
        "↩️ <b>Возвраты за последние 7 дней</b>",
        f"Период: {week_ago.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}",
        "──────────────",
        f"💸 <b>Сумма возвратов:</b> {amt} сом",
        f"🧾 <b>Количество возвратов:</b> {cnt}",
    ]
    top = data.get("top_returned_products") or []
    if top:
        lines.append("──────────────")
        lines.append("<b>Часто возвращаемые товары:</b>")
        for it in top[:5]:
            lines.append(f"• <b>{it.get('name')}</b>: {format_qty(it.get('quantity_returned'))} шт на сумму {_fmt_money(it.get('amount_returned'))} сом")
    else:
        lines.append("За выбранный период возвратов не было.")
    return "\n".join(lines)


def get_command_zakupki(company) -> str:
    """/zakupki: закупки за месяц по поставщикам (F13)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_purchases
    today = timezone.localdate()
    first_day = today.replace(day=1)
    data = fn_get_purchases(company, date_from=first_day.isoformat(), date_to=today.isoformat())
    if "error" in data:
        return f"❌ Ошибка: {data['error']}"

    total = _fmt_money(data.get("total_purchases_amount"))
    cnt = data.get("total_purchases_count", 0)
    debt = _fmt_money(data.get("total_debt_to_suppliers"))

    lines = [
        "🚚 <b>Закупки за текущий месяц</b>",
        f"Период: {first_day.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}",
        "──────────────",
        f"📦 <b>Сумма закупок:</b> {total} сом ({cnt} поставок)",
        f"💳 <b>Текущий долг поставщикам:</b> {debt} сом",
    ]
    suppliers = data.get("by_supplier") or []
    if suppliers:
        lines.append("──────────────")
        lines.append("<b>По поставщикам:</b>")
        for s in suppliers[:6]:
            lines.append(f"• <b>{s.get('supplier')}</b>: {_fmt_money(s.get('amount'))} сом (долг: {_fmt_money(s.get('debt'))} сом)")
    return "\n".join(lines)


def get_command_sverka(company) -> str:
    """/sverka: сверка за месяц (F17)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_reconcile
    today = timezone.localdate()
    first_day = today.replace(day=1)
    data = fn_get_reconcile(company, date_from=first_day.isoformat(), date_to=today.isoformat())
    if "error" in data:
        return f"❌ Ошибка: {data['error']}"

    all_ok = data.get("all_checks_ok", False)
    status_icon = "✅ Все проверки сошлись!" if all_ok else "⚠️ Обнаружены расхождения!"

    lines = [
        "🔍 <b>Сверка данных магазина (7 проверок)</b>",
        f"Период: {first_day.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}",
        f"Результат: <b>{status_icon}</b>",
        "──────────────",
    ]
    for chk in data.get("checks", []):
        icon = "✅" if chk.get("ok") else "❌"
        name = chk.get("name")
        diff = _fmt_money(chk.get("difference"))
        lines.append(f"{icon} <b>{name}</b>: {chk.get('description', '')}")
        if not chk.get("ok") and diff != "0.00":
            lines.append(f"   <i>Разница: {diff} сом</i>")
    return "\n".join(lines)


def get_command_zakazy(company) -> str:
    """/zakazy: заказы за неделю (F18)."""
    from apps.main.telegram_bot.services.ai_analytics_functions import fn_get_orders
    today = timezone.localdate()
    week_ago = today - timedelta(days=7)
    data = fn_get_orders(company, date_from=week_ago.isoformat(), date_to=today.isoformat())
    if "error" in data:
        return f"❌ Ошибка: {data['error']}"

    total = _fmt_money(data.get("total_amount"))
    cnt = data.get("total_orders", 0)

    lines = [
        "🛒 <b>Заказы покупателей за 7 дней</b>",
        f"Период: {week_ago.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}",
        "──────────────",
        f"📦 <b>Всего заказов:</b> {cnt}",
        f"💰 <b>На сумму:</b> {total} сом",
    ]
    sources = data.get("by_source") or []
    if sources:
        lines.append("──────────────")
        lines.append("<b>По источникам:</b>")
        for s in sources:
            src_name = s.get("source") or "Витрина"
            lines.append(f"• {src_name}: {s.get('count')} заказов ({_fmt_money(s.get('total'))} сом)")

    statuses = data.get("by_status") or []
    if statuses:
        lines.append("──────────────")
        lines.append("<b>По статусам:</b>")
        for st in statuses:
            lines.append(f"• {st.get('status')}: {st.get('count')} заказов")
    return "\n".join(lines)


def get_start_message() -> str:
    """ТЗ ч.9, 2.7: /start — один экран: что умеет бот и как спросить; полный список — в /help."""
    return (
        "👋 <b>Бот магазина на связи.</b>\n"
        "Отчёты — кнопками внизу экрана.\n\n"
        "💬 Или спросите обычными словами:\n"
        "• «Сколько заработали вчера?»\n"
        "• «Кто больше всех должен?»\n"
        "• «Что заказать у поставщиков?»\n\n"
        "Все команды — /help"
    )


def get_help_message() -> str:
    """Справка по командам (ТЗ ч.9, 2.7 — без длинного списка примеров)."""
    return (
        "🤖 <b>Команды</b>\n"
        "/segodnya — выручка и касса за сегодня\n"
        "/nedelya — продажи за 7 дней\n"
        "/top — топ товаров за 7 дней\n"
        "/abc — ABC анализ за 30 дней\n"
        "/sezon — сезонные товары\n"
        "/soveti — рекомендации\n"
        "/zakaz — что заказать\n"
        "/ostatki — малый остаток\n"
        "/dolgi — должники\n"
        "/pribyl — прибыль за месяц\n"
        "/dengi — движение денег\n"
        "/sklad — стоимость склада\n"
        "/mertvyi — неликвид\n"
        "/smena — текущая смена\n"
        "/vozvraty — возвраты\n"
        "/zakupki — закупки\n"
        "/sverka — сверка данных\n"
        "/zakazy — заказы покупателей\n\n"
        "💬 Можно писать и говорить обычными словами."
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
    low_text = ", ".join(f"{p['name']} ({format_qty(p['quantity'])} шт)" for p in low_stock) or "нет критических остатков"

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
                matched_products.append(f"{p.name}: цена {_fmt_money(p.price)} сом, остаток {format_qty(p.quantity or 0)} шт")

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
# ТЗ ч.15: язык, голосовой ответ, подтверждения, фото накладной
# =========================================================================

def _owner_language(settings, text: str) -> str:
    forced = getattr(settings, "voice_language", "auto") or "auto"
    if forced in ("ru", "ky"):
        return forced
    return ai_service.detect_language(text)


def _reply_owner(settings, chat_id: str, reply_html: str, *, is_voice: bool = False, language: str = "ru") -> None:
    """
    Текстовый ответ, а если вопрос был голосовым и «Ответы голосом» включены — сначала голосовое
    (короткий итог), затем подробности текстом (ТЗ ч.15, п. 2.2). Нет озвучки — отвечаем текстом.
    """
    token = settings.token
    reply_html = reply_html or ""
    if not (is_voice and getattr(settings, "voice_replies_enabled", True)):
        if reply_html:
            telegram_api.send_message(token, chat_id, ai_service.normalize_ai_output_for_telegram(reply_html) or reply_html)
        return

    voice_text, details = ai_service.split_voice_and_text(reply_html)
    sent_voice = False
    api_key = ai_service.get_effective_ai_key(settings.ai_key)
    if voice_text and api_key:
        try:
            audio = ai_service.synthesize_voice(api_key, voice_text, language=language)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS failed for company %s: %s", settings.company_id, exc)
            audio = b""
        if audio:
            res = telegram_api.send_voice(token, chat_id, audio)
            sent_voice = bool(res.get("ok"))
    if sent_voice:
        if details:
            telegram_api.send_message(token, chat_id, details)
    else:
        # Озвучить не вышло — весь ответ текстом без служебных меток
        full = reply_html.replace(ai_service.OWNER_VOICE_MARK, "").replace(ai_service.OWNER_TEXT_MARK, "\n")
        telegram_api.send_message(token, chat_id, ai_service.normalize_ai_output_for_telegram(full) or full)


def _announce_pending(settings, chat_id: str, pending: dict) -> None:
    from apps.main.telegram_bot.services import owner_actions

    text, markup = owner_actions.build_confirmation_message(pending)
    res = telegram_api.send_message(settings.token, chat_id, text, parse_mode="HTML", reply_markup=markup)
    pending["announced"] = True
    pending["message_id"] = (res.get("result") or {}).get("message_id") if isinstance(res, dict) else None
    owner_actions.save_pending(settings.company_id, chat_id, pending)


def handle_owner_photo(settings, chat_id: str, image_bytes: bytes, mime_type: str = "image/jpeg", caption: str = "") -> None:
    """ТЗ ч.15, п. 5: фото накладной/чека/прайса → список прихода с наценкой и кнопками."""
    from apps.main.telegram_bot.services import owner_actions

    token = settings.token
    company = settings.company
    if not getattr(settings, "ai_enabled", True) or not getattr(settings, "ai_owner_actions_enabled", True):
        telegram_api.send_message(token, chat_id, "Разбор накладных по фото отключён в настройках бота.")
        return
    api_key = ai_service.get_effective_ai_key(settings.ai_key)
    if not api_key:
        telegram_api.send_message(token, chat_id, "Ключ Google Gemini не настроен — не могу прочитать фото.")
        return
    telegram_api.send_chat_action(token, chat_id, "typing")
    parsed = owner_actions.parse_invoice_photo(api_key, image_bytes, mime_type)
    if parsed.get("error") or (not parsed.get("rows") and not parsed.get("unparsed")):
        telegram_api.send_message(token, chat_id, "Не смог разобрать документ на фото. Сфотографируйте ближе и ровнее, чтобы были видны названия, количество и цены.")
        return
    min_markup = Decimal(str(getattr(settings, "ai_min_markup_percent", 20) or 20))
    cap_markup = owner_actions.parse_markup_request(caption or "")
    if cap_markup is not None and cap_markup >= min_markup:
        min_markup = cap_markup
    pending = owner_actions.build_invoice_pending(company, chat_id, parsed, min_markup=min_markup)
    if not pending["changes"]:
        lines = ["Ни одной строки с названием, количеством и ценой не разобрал."]
        if pending.get("unparsed"):
            lines.append("Что увидел:")
            lines += [f"• {html.escape(str(u))}" for u in pending["unparsed"][:15]]
        telegram_api.send_message(token, chat_id, "\n".join(lines), parse_mode="HTML")
        owner_actions.clear_pending(company.id, chat_id)
        return
    telegram_api.send_message(
        token, chat_id,
        f"Наценка {owner_actions.fmt_money(min_markup)} % — оставить или другую? (например: «поставь 25 %»)",
    )
    _announce_pending(settings, chat_id, pending)


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
    language = _owner_language(settings, text)

    # 0. ТЗ ч.15, п. 4–5: ожидающие подтверждения изменения («да»/«нет», «поставь 25 %»)
    from apps.main.telegram_bot.services import owner_actions

    pending = owner_actions.get_pending(company.id, chat_id)
    if pending:
        if owner_actions.is_yes(norm):
            result = owner_actions.execute_pending(company, chat_id, pending, user=getattr(company, "owner", None))
            _reply_owner(settings, chat_id, owner_actions.build_result_message(result), is_voice=is_voice, language=language)
            return
        if owner_actions.is_no(norm):
            owner_actions.clear_pending(company.id, chat_id)
            _reply_owner(settings, chat_id, "Отменено, ничего не менял.", is_voice=is_voice, language=language)
            return
        if pending.get("kind") == "invoice":
            new_markup = owner_actions.parse_markup_request(norm)
            if new_markup is not None:
                pending = owner_actions.apply_markup_to_pending(pending, new_markup)
                owner_actions.save_pending(company.id, chat_id, pending)
                _announce_pending(settings, chat_id, pending)
                return

    # ТЗ ч.15, п. 6: «напомни должникам» — список со ссылками WhatsApp (бот сам должникам не пишет)
    if re.search(r"напомни(ть)?\s+(всем\s+)?должник|напоминани[ея]\s+должник|карыздарга\s+эскерт", norm) and "разослать" not in norm:
        reply = owner_actions.build_debt_reminders_message(company, owner_actions.extract_debtor_names(norm))
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    # 1. Команды с слэшем и кнопки постоянного меню владельца (ТЗ-09 п. 2.5)
    owner_menu = get_owner_main_menu_keyboard()
    if norm in ("/start", "/help", "помощь", "жардам"):
        reply = get_start_message() if norm == "/start" else get_help_message()
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML", reply_markup=owner_menu)
        return

    if norm in ("/segodnya", "📊 сегодня", "сегодня"):
        reply = get_report_today(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML", reply_markup=get_today_report_markup())
        return

    if norm in ("💰 касса", "/kassa"):
        reply = get_report_today(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML", reply_markup=get_today_report_markup())
        return

    if norm in ("/dolgi", "🧾 долги"):
        reply = get_report_dolgi(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML", reply_markup=owner_menu)
        return

    if norm in ("/ostatki", "📦 остатки") and len(norm.split()) <= 2 and not any(w in norm for w in ("размер", "размеры")):
        reply = get_report_ostatki(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML", reply_markup=owner_menu)
        return

    if norm in ("/zakaz", "/zakazy", "🛒 заказы"):
        reply = get_report_zakaz(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML", reply_markup=owner_menu)
        return

    if norm in ("/prokat", "🔔 прокат"):
        reply = get_command_prokat(company) if "get_command_prokat" in globals() else get_report_zakaz(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML", reply_markup=owner_menu)
        return

    # Запрос фото товара (ТЗ-11 п. 3.1): "фото <товар>", "покажи фото <товар>"
    m_photo = re.search(r"(?:покажи\s+фото|фото)\s+(.+)", norm)
    if m_photo:
        search_term = m_photo.group(1).strip()
        from apps.main.models import Product
        from apps.main.telegram_bot.services.photo_service import send_single_product_photo
        prod = Product.objects.filter(company=company, name__icontains=search_term).first()
        if prod:
            ok = send_single_product_photo(settings, chat_id, prod)
            if not ok:
                telegram_api.send_message(token, chat_id, f"У товара «{prod.name}» нет фотографии или не удалось её отправить.")
        else:
            telegram_api.send_message(token, chat_id, f"Товар по запросу «{search_term}» не найден в каталоге.")
        return

    # Запрос остатков одежды по размерам: "худи остатки", "остатки худи", "размеры" (ТЗ ч. 10 п. 3.4)
    if any(w in norm for w in ("размер", "размеры", "өлчөм")) or ("остатки" in norm and len(norm.split()) >= 2):
        cleaned_term = re.sub(r"\b(остатки|остаток|размеры|размер|какие|есть|по|в|наличии|на|складе|детские|мужские|женские)\b", "", norm).strip()
        if cleaned_term and len(cleaned_term) >= 2:
            sizes_rep = get_clothing_sizes_report(company, cleaned_term)
            if sizes_rep:
                telegram_api.send_message(token, chat_id, sizes_rep, parse_mode="HTML")
                return

    # Проверка кастомных сценариев по ключевым словам для владельца (ТЗ-11 п. 1.4)
    from apps.main.telegram_bot.views import match_scenario
    from apps.main.telegram_bot.models import TelegramBotScenario
    from apps.main.telegram_bot.tasks import _execute_scenario

    sc = match_scenario(company, text, audience="owner")
    if sc and sc.kind == TelegramBotScenario.Kind.KEYWORDS:
        _execute_scenario(settings, chat_id, sc, {"username": "owner"}, text, is_voice)
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

    if norm == "/pribyl":
        reply = get_command_pribyl(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/dengi":
        reply = get_command_dengi(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/sklad":
        reply = get_command_sklad(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/mertvyi":
        reply = get_command_mertvyi(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/smena":
        reply = get_command_smena(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/vozvraty":
        reply = get_command_vozvraty(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/zakupki":
        reply = get_command_zakupki(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/sverka":
        reply = get_command_sverka(company)
        telegram_api.send_message(token, chat_id, reply, parse_mode="HTML")
        return

    if norm == "/zakazy":
        reply = get_command_zakazy(company)
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

    # ТЗ ч.15, п. 2.3: пока бот думает — «записывает голосовое» / «печатает»
    voice_reply = bool(is_voice and getattr(settings, "voice_replies_enabled", True))
    telegram_api.send_chat_action(token, chat_id, "record_voice" if voice_reply else "typing")

    try:
        if getattr(settings, "ai_functions_enabled", True):
            ai_reply, _model, functions_called = ai_service.generate_owner_ai_response(
                company=company,
                settings=settings,
                user_question=text,
                history=history,
                is_voice=voice_reply,
                chat_id=chat_id,
                language=language,
            )
        else:
            system_instruction = build_owner_system_summary(company, user_question=text)
            user_contents = list(history[-6:])
            user_contents.append({"role": "user", "parts": [{"text": text}]})
            ai_reply, _model = ai_service.generate_chat_response(
                api_key=ai_key,
                system_instruction=system_instruction,
                contents=user_contents,
                temperature=0.4,
                max_tokens=800,
            )

        if not ai_reply:
            ai_reply = "Не удалось сгенерировать ответ. Попробуйте сформулировать иначе или используйте /help."

        # Сохраняем в память
        history.append({"role": "user", "parts": [{"text": text}]})
        history.append({"role": "model", "parts": [{"text": ai_reply}]})
        cache.set(cache_key, history[-8:], timeout=86400)

        # Ответ: голосом + подробности текстом, либо просто текстом
        _reply_owner(settings, chat_id, ai_reply, is_voice=is_voice, language=language)

        # ИИ предложил изменения товаров — показываем список с кнопками (ТЗ ч.15, п. 4.2)
        pending = owner_actions.get_pending(company.id, chat_id)
        if pending and not pending.get("announced"):
            _announce_pending(settings, chat_id, pending)

    except Exception as exc:
        logger.error("Owner AI conversation failed: %s", exc)
        telegram_api.send_message(
            token, chat_id,
            "В данный момент ИИ недоступен. Воспользуйтесь командами из списка /help."
        )
