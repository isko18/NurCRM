from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from django.apps import apps
from django.core.cache import cache
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger("telegram_bot.ai_functions")
ZERO_MONEY = Decimal("0.00")
MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


def _fmt(val) -> str:
    # ТЗ ч.9, 1.1: ИИ повторяет цифры дословно — отдаём без лишних нулей
    from apps.main.telegram_bot.services.photo_service import format_amount
    try:
        return format_amount(Decimal(str(val or 0)).quantize(Decimal("0.01")))
    except Exception:
        return str(val)


def _money_str(val) -> str:
    if val is None:
        return "0.00"
    try:
        return str(Decimal(str(val)).quantize(Decimal("0.01")))
    except Exception:
        return "0.00"


class DummyRequest:
    def __init__(self, user, company, query_params=None):
        self.user = user
        self.company = company
        self.query_params = query_params or {}
        self.data = {}
        self.method = "GET"


def _parse_period_dates(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    tz=None,
) -> Tuple[datetime, datetime]:
    """Преобразует строковые даты ГГГГ-ММ-ДД в границы datetime (start, end exclusive)."""
    if not tz:
        tz = timezone.get_current_timezone()
    today = timezone.localdate()

    df: Optional[date] = None
    dt: Optional[date] = None

    if date_from:
        try:
            df = datetime.strptime(str(date_from).strip()[:10], "%Y-%m-%d").date()
        except Exception:
            df = today

    if date_to:
        try:
            dt = datetime.strptime(str(date_to).strip()[:10], "%Y-%m-%d").date()
        except Exception:
            dt = df or today

    if not df and not dt:
        df = today
        dt = today
    elif not df:
        df = dt
    elif not dt:
        dt = df

    if df > dt:
        df, dt = dt, df

    start_dt = timezone.make_aware(datetime.combine(df, datetime.min.time()), tz)
    end_dt = timezone.make_aware(datetime.combine(dt + timedelta(days=1), datetime.min.time()), tz)
    return start_dt, end_dt


def _resolve_branch(company, branch_arg: Optional[str] = None):
    if not branch_arg:
        return None
    from apps.users.models import Branch

    b_str = str(branch_arg).strip()
    try:
        return Branch.objects.filter(company=company, id=b_str).first()
    except Exception:
        return Branch.objects.filter(company=company, name__icontains=b_str).first()


def _get_analytics_view():
    from apps.main.analytics_market import AnalyticsView, Period

    return AnalyticsView(), Period


# =========================================================================
# 20 Функций Аналитики для ИИ (F1 - F20)
# =========================================================================

def fn_get_sales_summary(company, branch=None, date_from=None, date_to=None) -> dict:
    """F1: Выручка, чеки, средний чек, валовая прибыль, маржа %, возвраты, способы оплаты, продажи по дням."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._sales(req, company, branch_obj, period)
    cards = data.get("cards", {})
    charts = data.get("charts", {})

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "revenue": cards.get("revenue", "0.00"),
        "checks_count": cards.get("transactions", 0),
        "average_check": cards.get("avg_check", "0.00"),
        "gross_profit": cards.get("gross_profit") or "0.00",
        "margin_percent": cards.get("margin_percent"),
        "returns_amount": cards.get("returns_total", "0.00"),
        "returns_count": cards.get("returns_count", 0),
        "payment_methods": charts.get("payment_methods", []),
        "daily_sales": charts.get("sales_dynamics", [])[-31:],
    }


def fn_get_pnl(company, branch=None, date_from=None, date_to=None) -> dict:
    """F2: P&L: выручка, себестоимость, валовая прибыль, маржа %, опер. расходы, опер. прибыль."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._pnl(req, company, branch_obj, period)
    rev = data.get("revenue", {})
    cogs = data.get("cogs", {})
    gp = data.get("gross_profit", {})
    op_exp = data.get("operating_expenses", {})
    op_profit = data.get("operating_profit", {})
    net_profit = data.get("net_profit", {})

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "revenue": rev.get("total", "0.00"),
        "cogs": cogs.get("total", "0.00"),
        "gross_profit": gp.get("total", "0.00"),
        "margin_percent": gp.get("margin_percent"),
        "operating_expenses_total": op_exp.get("total", "0.00"),
        "operating_expenses": op_exp.get("by_category", [])[:15],
        "operating_profit": op_profit.get("total", "0.00"),
        "net_profit": net_profit.get("total", "0.00"),
    }


def fn_get_cashflow(company, branch=None, date_from=None, date_to=None) -> dict:
    """F3: Cash Flow: приход и расход по статьям, чистое движение денег."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._cashflow(req, company, branch_obj, period)
    inflow = data.get("inflow", {})
    outflow = data.get("outflow", {})
    net_flow = data.get("net_cashflow", "0.00")

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "inflow": inflow,
        "outflow": outflow,
        "net_flow": net_flow,
    }


def fn_get_top_products(
    company,
    branch=None,
    date_from=None,
    date_to=None,
    sort="revenue",
    limit=10,
    order="desc",
) -> dict:
    """F4: Топ товаров по выручке, прибыли или количеству продаж."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    lim = min(max(int(limit or 10), 1), 30)
    req = DummyRequest(company.owner, company, query_params={"limit": "200"})

    data = view._products_analytics(req, company, branch_obj, period)
    tables = data.get("tables", {})

    sort_mode = str(sort or "revenue").lower()
    if "qty" in sort_mode or "quantity" in sort_mode:
        raw_items = tables.get("top_by_quantity", [])
    else:
        raw_items = tables.get("top_by_revenue", [])

    is_asc = str(order).lower() == "asc"
    if is_asc:
        raw_items = list(reversed(raw_items))

    sliced = raw_items[:lim]
    formatted = []
    for item in sliced:
        formatted.append({
            "name": item.get("name") or "Товар",
            "quantity_sold": item.get("quantity") or item.get("sold") or 0,
            "revenue": item.get("revenue") or "0.00",
            "profit": item.get("profit") or item.get("gross_profit"),
        })

    return {
        "sort": sort_mode,
        "total_count": len(raw_items),
        "products": formatted,
    }


def fn_get_abc(company, branch=None, date_from=None, date_to=None, slice="revenue") -> dict:
    """F5: ABC-анализ ассортимента (группы A/B/C по выручке/прибыли/кол-ву)."""
    cache_key = f"tg_ai_fn:{company.id}:abc:{branch}:{date_from}:{date_to}:{slice}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company, query_params={"slice": slice or "revenue"})

    data = view._abc(req, company, branch_obj, period)
    groups_data = data.get("groups", {})
    res = {
        "slice": slice,
        "total_revenue": data.get("total_revenue", "0.00"),
        "groups": {
            k: {
                "count": v.get("count", 0),
                "sum": v.get("sum", "0.00"),
                "share": v.get("share", 0),
                "top_products": [
                    {"name": p.get("name"), "value": p.get("value"), "share": p.get("share")}
                    for p in (v.get("products") or [])[:10]
                ],
            }
            for k, v in groups_data.items()
        },
    }
    cache.set(cache_key, res, timeout=300)
    return res


def fn_get_stock(company, branch=None, query=None, only_low=False, limit=10) -> dict:
    """F6: Остатки на складе: стоимость по закупке/продаже, заканчивающиеся товары, поиск по названию/штрихкоду."""
    from apps.main.models import Product

    lim = min(max(int(limit or 10), 1), 30)
    branch_obj = _resolve_branch(company, branch)
    qs = Product.objects.filter(company=company).exclude(status=Product.Status.ARCHIVED)
    if branch_obj:
        qs = qs.filter(branch=branch_obj)

    if query:
        q_str = str(query).strip()
        qs = qs.filter(Q(name__icontains=q_str) | Q(barcode__icontains=q_str) | Q(code__icontains=q_str))

    min_field = None
    for f in ("min_quantity", "min_stock", "reorder_level", "minimum_quantity"):
        if hasattr(Product, f):
            min_field = f
            break

    if only_low:
        if min_field:
            qs = qs.filter(quantity__lte=F(min_field))
        else:
            qs = qs.filter(quantity__lte=5)

    total_count = qs.count()
    items = []
    for p in qs.order_by("quantity")[:lim]:
        items.append({
            "name": p.name,
            "quantity": float(p.quantity or 0),
            "unit": getattr(p, "unit", "") or "шт",
            "price": _money_str(p.price),
            "purchase_price": _money_str(p.purchase_price),
            "stock_value": _money_str(Decimal(str(p.quantity or 0)) * Decimal(str(p.purchase_price or 0))),
            "status": "critical" if (p.quantity or 0) <= 2 else "normal",
        })

    # Общие итоги по складу компании
    all_qs = Product.objects.filter(company=company).exclude(status=Product.Status.ARCHIVED)
    if branch_obj:
        all_qs = all_qs.filter(branch=branch_obj)

    agg = all_qs.aggregate(
        tot_qty=Coalesce(Sum("quantity"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        tot_cost=Coalesce(Sum(F("quantity") * F("purchase_price")), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        tot_retail=Coalesce(Sum(F("quantity") * F("price")), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
    )
    low_count = (
        all_qs.filter(quantity__lte=F(min_field)).count()
        if min_field
        else all_qs.filter(quantity__lte=5).count()
    )

    return {
        "summary": {
            "total_products": all_qs.count(),
            "total_quantity": float(agg["tot_qty"] or 0),
            "cost_value": _money_str(agg["tot_cost"]),
            "retail_value": _money_str(agg["tot_retail"]),
            "low_stock_count": low_count,
            "low_stock_rule": "current_stock <= minimum_stock (default 5)",
        },
        "total_count": total_count,
        "products": items,
    }


def fn_get_dead_stock(company, branch=None, days=30, limit=10) -> dict:
    """F7: Мёртвый склад (неликвид): товары с остатком > 0 без продаж за N дней, замороженные деньги."""
    cache_key = f"tg_ai_fn:{company.id}:dead_stock:{branch}:{days}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    from apps.main.models import Product, Sale, SaleItem

    lim = min(max(int(limit or 10), 1), 30)
    d = int(days or 30)
    cutoff = timezone.now() - timedelta(days=d)
    branch_obj = _resolve_branch(company, branch)

    # Товары, которые продавались за последние N дней
    active_sale_pids = set(
        SaleItem.objects.filter(
            sale__company=company,
            sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
            sale__paid_at__gte=cutoff,
            product__isnull=False,
        ).values_list("product_id", flat=True)
    )

    pqs = Product.objects.filter(company=company, quantity__gt=0).exclude(status=Product.Status.ARCHIVED)
    if branch_obj:
        pqs = pqs.filter(branch=branch_obj)

    dead_qs = pqs.exclude(id__in=active_sale_pids)
    total_count = dead_qs.count()

    total_frozen = dead_qs.aggregate(
        s=Coalesce(Sum(F("quantity") * F("purchase_price")), Value(ZERO_MONEY, output_field=MONEY_FIELD))
    )["s"] or ZERO_MONEY

    items = []
    for p in dead_qs.order_by("-purchase_price")[:lim]:
        frozen = Decimal(str(p.quantity or 0)) * Decimal(str(p.purchase_price or 0))
        items.append({
            "name": p.name,
            "quantity": float(p.quantity or 0),
            "unit": getattr(p, "unit", "") or "шт",
            "purchase_price": _money_str(p.purchase_price),
            "frozen_cost": _money_str(frozen),
        })

    res = {
        "days": d,
        "total_frozen_cost": _money_str(total_frozen),
        "total_dead_stock_count": total_count,
        "total_count": total_count,
        "products": items,
    }
    cache.set(cache_key, res, timeout=300)
    return res


def fn_get_product(company, query, branch=None) -> dict:
    """F8: Карточка товара: цена, закупка, остаток, продажи за 7/30 дней, последняя закупка, акции."""
    from apps.main.models import Product, Sale, SaleItem

    q_str = str(query or "").strip()
    branch_obj = _resolve_branch(company, branch)
    pqs = Product.objects.filter(company=company).exclude(status=Product.Status.ARCHIVED)
    if branch_obj:
        pqs = pqs.filter(branch=branch_obj)

    prod = (
        pqs.filter(Q(name__iexact=q_str) | Q(barcode=q_str) | Q(code=q_str) | Q(article=q_str)).first()
        or pqs.filter(name__icontains=q_str).first()
    )
    if not prod:
        return {"found": False, "query": q_str, "detail": f"Товар «{q_str}» не найден в каталоге."}

    now = timezone.now()
    sales_7 = SaleItem.objects.filter(
        product=prod,
        sale__company=company,
        sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
        sale__paid_at__gte=now - timedelta(days=7),
    ).aggregate(
        qty=Coalesce(Sum("quantity"), Value(Decimal("0.00"), output_field=MONEY_FIELD)),
        rev=Coalesce(
            Sum(F("unit_price") * F("quantity") - F("line_discount"), output_field=MONEY_FIELD),
            Value(ZERO_MONEY, output_field=MONEY_FIELD),
        ),
    )

    sales_30 = SaleItem.objects.filter(
        product=prod,
        sale__company=company,
        sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
        sale__paid_at__gte=now - timedelta(days=30),
    ).aggregate(
        qty=Coalesce(Sum("quantity"), Value(Decimal("0.00"), output_field=MONEY_FIELD)),
        rev=Coalesce(
            Sum(F("unit_price") * F("quantity") - F("line_discount"), output_field=MONEY_FIELD),
            Value(ZERO_MONEY, output_field=MONEY_FIELD),
        ),
    )

    last_purchase_info = None
    try:
        Acceptance = apps.get_model("main.Acceptance")
        acc = Acceptance.objects.filter(company=company, product=prod).order_by("-created_at").first()
        if acc:
            last_purchase_info = {
                "date": acc.created_at.date().isoformat(),
                "quantity": float(acc.quantity or 0),
                "purchase_price": _money_str(acc.price or prod.purchase_price),
                "supplier": getattr(acc.supplier, "name", None) or "Поставщик",
            }
    except Exception:
        pass

    from apps.main.variant_utils import sort_variants, variant_prices

    variants_list = []
    for v in sort_variants(prod.variants.filter(is_active=True)):
        price, old_price = variant_prices(v, prod)
        variants_list.append({
            "id": str(v.id),
            "size": v.size,
            "color": v.color,
            "quantity": float(v.quantity or 0),
            "in_stock": (v.quantity or 0) > 0,
            "price": _money_str(price),
            "promo": old_price is not None,
            "regular_price": _money_str(old_price) if old_price is not None else None,
        })

    return {
        "found": True,
        "id": str(prod.id),
        "name": prod.name,
        "barcode": prod.barcode,
        "article": getattr(prod, "article", None),
        "price": _money_str(prod.price),
        "purchase_price": _money_str(prod.purchase_price),
        "quantity": float(prod.quantity or 0),
        "unit": getattr(prod, "unit", "") or "шт",
        "category": prod.category.name if getattr(prod, "category", None) else None,
        "variants": variants_list,
        "sales_last_7_days": {"quantity": float(sales_7["qty"] or 0), "revenue": _money_str(sales_7["rev"])},
        "sales_last_30_days": {"quantity": float(sales_30["qty"] or 0), "revenue": _money_str(sales_30["rev"])},
        "last_purchase": last_purchase_info,
    }


def fn_get_debtors(company, branch=None, limit=10, min_amount=None) -> dict:
    """F9: Список должников, сумма долга, дата последней покупки и оплаты, общий долг дебиторки."""
    from apps.main.kassa_views import _open_debts
    from apps.main.models import Client

    lim = min(max(int(limit or 10), 1), 30)
    min_amt = Decimal(str(min_amount or 0)) if min_amount is not None else Decimal("0.00")

    clients_qs = Client.objects.filter(company=company)
    branch_obj = _resolve_branch(company, branch)
    if branch_obj:
        clients_qs = clients_qs.filter(branch=branch_obj)

    from collections import defaultdict
    agg = defaultdict(lambda: {"debt_total": ZERO_MONEY, "sales_count": 0, "oldest": None})
    for client_id, _sale_id, remaining, created_at in _open_debts([company.id], clients_qs.values("id")):
        a = agg[client_id]
        a["debt_total"] += remaining
        a["sales_count"] += 1
        if a["oldest"] is None or created_at < a["oldest"]:
            a["oldest"] = created_at

    clients_dict = {c.id: c for c in Client.objects.filter(id__in=list(agg)).only("id", "full_name", "phone")}
    debtors_list = []
    total_debt = ZERO_MONEY

    for cid, a in agg.items():
        if a["debt_total"] < min_amt or cid not in clients_dict:
            continue
        c = clients_dict[cid]
        total_debt += a["debt_total"]
        debtors_list.append({
            "name": c.full_name or "Без имени",
            "phone": c.phone or "",
            "debt_amount": _money_str(a["debt_total"]),
            "oldest_debt_date": a["oldest"].date().isoformat() if a["oldest"] else None,
        })

    debtors_list.sort(key=lambda r: Decimal(r["debt_amount"]), reverse=True)
    return {
        "total_debt": _money_str(total_debt),
        "debtors_count": len(debtors_list),
        "total_count": len(debtors_list),
        "debtors": debtors_list[:lim],
    }


def fn_get_client(company, query, date_from=None, date_to=None) -> dict:
    """F10: Данные клиента: покупки за период, текущий долг, бонусы."""
    from apps.main.models import Client, Sale

    q_str = str(query or "").strip()
    client = (
        Client.objects.filter(company=company, phone__icontains=q_str).first()
        or Client.objects.filter(company=company, full_name__icontains=q_str).first()
    )
    if not client:
        return {"found": False, "query": q_str, "detail": f"Клиент «{q_str}» не найден."}

    start, end = _parse_period_dates(date_from, date_to) if (date_from or date_to) else (None, None)
    sqs = Sale.objects.filter(company=company, client=client, status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED])
    if start and end:
        sqs = sqs.filter(paid_at__gte=start, paid_at__lt=end)

    agg = sqs.aggregate(
        tot=Coalesce(Sum("total"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        cnt=Count("id"),
        debt=Coalesce(Sum("debt_remaining"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
    )
    last_sale = sqs.order_by("-paid_at").first()

    return {
        "found": True,
        "name": client.full_name,
        "phone": client.phone,
        "total_debt": _money_str(agg["debt"]),
        "bonus_balance": str(getattr(client, "bonus_balance", 0) or 0),
        "purchases_count": agg["cnt"] or 0,
        "purchases_total": _money_str(agg["tot"]),
        "last_purchase_date": last_sale.paid_at.date().isoformat() if (last_sale and last_sale.paid_at) else None,
    }


def fn_get_shift(company, which="current", date=None, shift_id=None, branch=None) -> dict:
    """F11: Отчёт кассовой смены: кассир, продажи, наличные/безнал/долг, ожидаемая наличность, расхождение."""
    from apps.construction.models import CashShift
    from apps.construction.views import build_shift_report

    branch_obj = _resolve_branch(company, branch)
    qs = CashShift.objects.filter(company=company)
    if branch_obj:
        qs = qs.filter(branch=branch_obj)

    shift = None
    if shift_id:
        shift = qs.filter(id=shift_id).first()
    elif date:
        try:
            d = datetime.strptime(str(date).strip()[:10], "%Y-%m-%d").date()
            shift = qs.filter(opened_at__date=d).order_by("-opened_at").first()
        except Exception:
            pass

    if not shift:
        w = str(which or "current").lower()
        if w == "current":
            shift = qs.filter(status=CashShift.Status.OPEN).order_by("-opened_at").first()
            if not shift:
                shift = qs.order_by("-opened_at").first()
        else:
            shift = qs.order_by("-opened_at").first()

    if not shift:
        return {"found": False, "detail": "Кассовые смены не найдены."}

    rep = build_shift_report(shift)
    return {
        "found": True,
        "shift_id": str(shift.id),
        "cashbox": rep.get("cashbox_name"),
        "cashier": rep.get("cashier_name"),
        "status": rep.get("status"),
        "opened_at": rep.get("opened_at"),
        "closed_at": rep.get("closed_at"),
        "sales_total": rep.get("sales_total", "0.00"),
        "sales_cash": rep.get("sales_cash", "0.00"),
        "sales_card": rep.get("sales_card", "0.00"),
        "sales_debt": rep.get("sales_debt", "0.00"),
        "debt_repayment_cash": rep.get("debt_repayment_cash", "0.00"),
        "debt_payment_cash": rep.get("debt_payment_cash", "0.00"),
        "deposits_total": rep.get("deposits_total", "0.00"),
        "withdrawals_total": rep.get("withdrawals_total", "0.00"),
        "expected_cash": rep.get("expected_cash", "0.00"),
        "actual_cash": rep.get("actual_cash"),
        "discrepancy": rep.get("discrepancy", "0.00"),
    }


def fn_get_returns(company, branch=None, date_from=None, date_to=None) -> dict:
    """F12: Возвраты за период: сумма, количество, топ возвращаемых товаров."""
    from apps.main.models import SaleReturn

    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    rqs = SaleReturn.objects.filter(company=company, created_at__gte=start, created_at__lt=end)
    if branch_obj:
        rqs = rqs.filter(sale__branch=branch_obj)

    agg = rqs.aggregate(
        tot=Coalesce(Sum("returned_amount"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        cnt=Count("id"),
        defects=Count("id", filter=Q(is_defect=True)),
    )

    top_items: Dict[str, dict] = {}
    for r in rqs.iterator():
        for it in r.returned_items or []:
            name = it.get("product_name") or it.get("name") or "Товар"
            qty = float(it.get("quantity") or it.get("qty") or 1)
            amt = Decimal(str(it.get("amount") or it.get("total") or 0))
            if name not in top_items:
                top_items[name] = {"name": name, "quantity": 0.0, "total": Decimal("0.00")}
            top_items[name]["quantity"] += qty
            top_items[name]["total"] += amt

    sorted_items = sorted(top_items.values(), key=lambda x: x["total"], reverse=True)[:10]
    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "returns_total": _money_str(agg["tot"]),
        "returns_count": agg["cnt"] or 0,
        "defects_count": agg["defects"] or 0,
        "top_returned_products": [
            {"name": it["name"], "quantity": it["quantity"], "total": _money_str(it["total"])}
            for it in sorted_items
        ],
    }


def fn_get_purchases(company, branch=None, date_from=None, date_to=None, supplier=None) -> dict:
    """F13: Закупки за период: общая сумма, по поставщикам, долг перед поставщиками."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._procurement(req, company, branch_obj, period)
    cards = data.get("cards", {})
    tables = data.get("tables", {})

    suppliers = tables.get("suppliers", [])
    if supplier:
        sup_str = str(supplier).strip().lower()
        suppliers = [s for s in suppliers if sup_str in (s.get("name") or "").lower()]

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "total_purchases_amount": cards.get("total_procurement_cost", "0.00"),
        "total_items_count": cards.get("total_items_procured", 0),
        "suppliers": [
            {
                "name": s.get("name"),
                "purchases_amount": s.get("total_cost", "0.00"),
                "debt": s.get("debt", "0.00"),
            }
            for s in suppliers[:15]
        ],
    }


def fn_get_finance_operations(company, branch=None, date_from=None, date_to=None, type="all") -> dict:
    """F14: Финансовые операции: доходы и расходы по статьям без задвоений."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._finance(req, company, branch_obj, period)
    cards = data.get("cards", {})
    breakdown = data.get("breakdown", {})

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "income_total": cards.get("income_total", "0.00"),
        "expense_total": cards.get("expense_total", "0.00"),
        "net_flow": cards.get("net_flow", "0.00"),
        "top_income_categories": breakdown.get("income", [])[:10],
        "top_expense_categories": breakdown.get("expense", [])[:10],
    }


def fn_get_salary(company, branch=None, date_from=None, date_to=None) -> dict:
    """F15: Начисления заработной платы сотрудникам (оклад, проценты, консультанты)."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._salary(req, company, branch_obj, period)
    cards = data.get("cards", {})

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "total_payroll": cards.get("total_payroll", "0.00"),
        "employees_count": cards.get("employees_with_profile", 0),
        "employees": [
            {
                "name": e.get("employee_label"),
                "pay_scheme": e.get("pay_scheme_label"),
                "total_payable": e.get("total", "0.00"),
            }
            for e in data.get("rows", [])[:15]
        ],
    }


def fn_get_cashiers(company, branch=None, date_from=None, date_to=None) -> dict:
    """F16: Продажи по кассирам и сотрудникам: выручка, чеки, скидки, возвраты."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._users_analytics(req, company, branch_obj, period)
    tables = data.get("tables", {})

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "cashiers": [
            {
                "name": u.get("name"),
                "checks_count": u.get("transactions", 0),
                "revenue": u.get("revenue", "0.00"),
                "average_check": u.get("avg_check", "0.00"),
            }
            for u in tables.get("users", [])[:15]
        ],
    }


def fn_get_reconcile(company, branch=None, date_from=None, date_to=None) -> dict:
    """F17: Сверка магазина (7 контрольных проверок: ок/ошибка, расхождение)."""
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _parse_period_dates(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    data = view._reconcile(req, company, branch_obj, period)
    checks = data.get("checks", [])
    all_ok = all(bool(c.get("ok")) for c in checks)

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "all_ok": all_ok,
        "checks": checks,
    }


def fn_get_orders(company, branch=None, date_from=None, date_to=None, source=None) -> dict:
    """F18: Заказы (Telegram-бот, витрина): количество, сумма, статусы."""
    from apps.main.models import ShowcaseOrder

    start, end = _parse_period_dates(date_from, date_to)
    qs = ShowcaseOrder.objects.filter(company=company, created_at__gte=start, created_at__lt=end)
    if source:
        qs = qs.filter(source__icontains=str(source).strip())

    agg = qs.aggregate(
        tot=Coalesce(Sum("total"), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        cnt=Count("id"),
    )
    by_status = dict(qs.values("status").annotate(c=Count("id")).values_list("status", "c"))

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "orders_count": agg["cnt"] or 0,
        "total_amount": _money_str(agg["tot"]),
        "by_status": by_status,
    }


def fn_get_bot_stats(company, date_from=None, date_to=None) -> dict:
    """F19: Статистика Telegram-бота: обращения покупателей, уникальные люди, заказы через бота."""
    from apps.main.telegram_bot.models import TelegramInquiry

    start, end = _parse_period_dates(date_from, date_to)
    qs = TelegramInquiry.objects.filter(company=company, created_at__gte=start, created_at__lt=end)

    agg = qs.aggregate(
        total_msgs=Count("id"),
        total_people=Count("chat_id", distinct=True),
        total_orders=Count("order_id", distinct=True, filter=Q(order__isnull=False)),
    )
    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "total_messages": agg["total_msgs"] or 0,
        "unique_customers": agg["total_people"] or 0,
        "orders_created": agg["total_orders"] or 0,
    }


def fn_compare_periods(
    company,
    metric="revenue",
    period1_from=None,
    period1_to=None,
    period2_from=None,
    period2_to=None,
    branch=None,
) -> dict:
    """F20: Сравнение двух периодов по выбранной метрике (выручка, прибыль, чеки, средний чек)."""
    m = str(metric or "revenue").lower()
    p1 = fn_get_sales_summary(company, branch=branch, date_from=period1_from, date_to=period1_to)
    p2 = fn_get_sales_summary(company, branch=branch, date_from=period2_from, date_to=period2_to)

    key_map = {
        "revenue": "revenue",
        "выручка": "revenue",
        "profit": "gross_profit",
        "прибыль": "gross_profit",
        "checks": "checks_count",
        "чеки": "checks_count",
        "average_check": "average_check",
        "средний чек": "average_check",
    }
    extracted_key = key_map.get(m, "revenue")

    v1_raw = p1.get(extracted_key, 0)
    v2_raw = p2.get(extracted_key, 0)
    v1 = Decimal(str(v1_raw))
    v2 = Decimal(str(v2_raw))

    diff = v2 - v1
    pct = round(float((diff / v1) * 100), 1) if v1 != 0 else None

    return {
        "metric": extracted_key,
        "period_1": {"dates": p1["period"], "value": _money_str(v1)},
        "period_2": {"dates": p2["period"], "value": _money_str(v2)},
        "difference": _money_str(diff),
        "percent_change": pct,
    }


# =========================================================================
# ТЗ ч.15, п. 3: данные как у ИИ-советника программы
# =========================================================================

def _period_from_question(date_from, date_to):
    """Период из вопроса; без дат — с начала месяца (ТЗ ч.15, п. 3)."""
    if not date_from and not date_to:
        today = timezone.localdate()
        return _parse_period_dates(today.replace(day=1).isoformat(), today.isoformat())
    return _parse_period_dates(date_from, date_to)


def _user_name(u) -> str:
    if u is None:
        return "—"
    full = f"{(getattr(u, 'first_name', '') or '').strip()} {(getattr(u, 'last_name', '') or '').strip()}".strip()
    return full or getattr(u, "email", "") or str(getattr(u, "pk", ""))


def _timesheet(company, start, end, branch_obj=None) -> Dict[str, dict]:
    """Табель по кассирам из закрытых смен: смен, дней, часов, средняя смена, выручка; кто сейчас на смене."""
    from apps.construction.models import CashShift

    qs = CashShift.objects.filter(company=company).select_related("cashier")
    if branch_obj:
        qs = qs.filter(branch=branch_obj)
    closed = qs.filter(status=CashShift.Status.CLOSED, opened_at__gte=start, opened_at__lt=end, closed_at__isnull=False)
    rows: Dict[str, dict] = {}
    for sh in closed:
        key = str(sh.cashier_id)
        r = rows.setdefault(key, {
            "name": _user_name(sh.cashier), "shifts": 0, "days": set(), "hours": 0.0,
            "shift_revenue": ZERO_MONEY, "checks": 0, "on_shift_now": False,
        })
        r["shifts"] += 1
        r["days"].add(timezone.localtime(sh.opened_at).date())
        r["hours"] += max((sh.closed_at - sh.opened_at).total_seconds(), 0) / 3600.0
        r["shift_revenue"] += Decimal(str(sh.sales_total or 0))
        r["checks"] += int(sh.sales_count or 0)
    for sh in qs.filter(status=CashShift.Status.OPEN):
        key = str(sh.cashier_id)
        r = rows.setdefault(key, {
            "name": _user_name(sh.cashier), "shifts": 0, "days": set(), "hours": 0.0,
            "shift_revenue": ZERO_MONEY, "checks": 0, "on_shift_now": False,
        })
        r["on_shift_now"] = True
        r["open_since"] = timezone.localtime(sh.opened_at).strftime("%d.%m %H:%M")
    out = {}
    for key, r in rows.items():
        hours = round(r["hours"], 1)
        out[key] = {
            "name": r["name"],
            "shifts": r["shifts"],
            "days": len(r["days"]),
            "hours": hours,
            "avg_shift_hours": round(hours / r["shifts"], 1) if r["shifts"] else 0.0,
            "shift_revenue": _money_str(r["shift_revenue"]),
            "shift_checks": r["checks"],
            "on_shift_now": r["on_shift_now"],
            "open_since": r.get("open_since"),
        }
    return out


def fn_get_staff(company, branch=None, date_from=None, date_to=None, employee=None) -> dict:
    """
    Сотрудники: схема оплаты, начислено, продажи, чеки, продано штук + табель (смены, дни, часы, кто на смене).
    Период — из вопроса, иначе с начала месяца.
    """
    view, PeriodCls = _get_analytics_view()
    branch_obj = _resolve_branch(company, branch)
    start, end = _period_from_question(date_from, date_to)
    period = PeriodCls(start=start, end=end)
    req = DummyRequest(company.owner, company)

    salary = view._salary(req, company, branch_obj, period)
    rows = salary.get("rows") or []
    sheet = _timesheet(company, start, end, branch_obj)

    employees = []
    seen = set()
    for r in rows:
        uid = str(r.get("user_id"))
        seen.add(uid)
        ts = sheet.get(uid) or {}
        employees.append({
            "name": r.get("employee_label"),
            "pay_scheme": r.get("pay_scheme_label"),
            "monthly_base_salary": r.get("monthly_base_salary"),
            "sales_percent": r.get("sales_percent"),
            "per_item_amount": r.get("per_item_amount"),
            "accrued_total": r.get("total"),
            "accrued_base": r.get("base_prorated"),
            "accrued_percent_bonus": r.get("percent_bonus"),
            "accrued_per_item_bonus": r.get("per_item_bonus"),
            "sales_total": r.get("cashier_sales_period"),
            "checks": r.get("cashier_sales_count", 0),
            "items_sold": r.get("items_sold_period"),
            "timesheet": {
                "shifts": ts.get("shifts", 0), "days": ts.get("days", 0), "hours": ts.get("hours", 0.0),
                "avg_shift_hours": ts.get("avg_shift_hours", 0.0), "shift_revenue": ts.get("shift_revenue", "0.00"),
                "on_shift_now": ts.get("on_shift_now", False), "open_since": ts.get("open_since"),
            },
        })
    # кассиры без профиля зарплаты, но со сменами
    for uid, ts in sheet.items():
        if uid in seen:
            continue
        employees.append({
            "name": ts["name"], "pay_scheme": "не настроена", "accrued_total": "0.00",
            "sales_total": ts.get("shift_revenue"), "checks": ts.get("shift_checks", 0),
            "timesheet": {k: ts.get(k) for k in ("shifts", "days", "hours", "avg_shift_hours", "shift_revenue", "on_shift_now", "open_since")},
        })
    if employee:
        e_str = str(employee).strip().lower()
        employees = [e for e in employees if e_str in (e.get("name") or "").lower()] or employees

    on_shift = [e["name"] for e in employees if (e.get("timesheet") or {}).get("on_shift_now")]
    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "total_payroll": (salary.get("cards") or {}).get("total_payroll", "0.00"),
        "employees_count": len(employees),
        "on_shift_now": on_shift,
        "employees": employees[:20],
    }


def fn_get_shift_archive(company, branch=None, limit=10, date_from=None, date_to=None) -> dict:
    """Архив смен (Z-отчёты): последние закрытые смены + открытые сейчас."""
    from apps.construction.models import CashShift

    lim = min(max(int(limit or 10), 1), 30)
    branch_obj = _resolve_branch(company, branch)
    qs = CashShift.objects.filter(company=company).select_related("cashier", "cashbox")
    if branch_obj:
        qs = qs.filter(branch=branch_obj)
    if date_from or date_to:
        start, end = _parse_period_dates(date_from, date_to)
        qs = qs.filter(opened_at__gte=start, opened_at__lt=end)

    def _row(sh, live=False):
        row = {
            "shift_id": str(sh.id),
            "cashbox": getattr(sh.cashbox, "name", None),
            "cashier": _user_name(sh.cashier),
            "status": sh.status,
            "opened_at": timezone.localtime(sh.opened_at).strftime("%d.%m.%Y %H:%M") if sh.opened_at else None,
            "closed_at": timezone.localtime(sh.closed_at).strftime("%d.%m.%Y %H:%M") if sh.closed_at else None,
            "opening_cash": _money_str(sh.opening_cash),
            "closing_cash": _money_str(sh.closing_cash) if sh.closing_cash is not None else None,
        }
        if live:
            try:
                t = sh.calc_live_totals()
                row.update({
                    "sales_total": _money_str(t.get("sales_total")), "sales_cash": _money_str(t.get("cash_sales_total")),
                    "sales_card": _money_str(t.get("noncash_sales_total")), "checks": int(t.get("sales_count") or 0),
                    "expected_cash": _money_str(t.get("expected_cash")),
                })
            except Exception:
                row.update({"sales_total": _money_str(sh.sales_total), "checks": int(sh.sales_count or 0)})
        else:
            row.update({
                "sales_total": _money_str(sh.sales_total), "sales_cash": _money_str(sh.cash_sales_total),
                "sales_card": _money_str(sh.noncash_sales_total), "checks": int(sh.sales_count or 0),
            })
        return row

    closed = [_row(sh) for sh in qs.filter(status=CashShift.Status.CLOSED).order_by("-closed_at")[:lim]]
    for n, row in enumerate(closed, 1):
        row["number"] = n  # 1 — самая свежая
    open_now = [_row(sh, live=True) for sh in qs.filter(status=CashShift.Status.OPEN).order_by("-opened_at")[:10]]
    return {"closed_shifts": closed, "open_shifts": open_now, "closed_count": len(closed), "open_count": len(open_now)}


def fn_get_stock_alerts(company, branch=None, kind="all", days=30, limit=15, min_markup=None) -> dict:
    """
    Товары к заказу (ТЗ ч.15, п. 3): «хорошо продавались, но закончились», «популярные, но осталось мало»
    (меньше 7 дней продаж), не продаются N дней, затоварено, низкая наценка.
    """
    from apps.main.models import Product, SaleItem, Sale

    lim = min(max(int(limit or 15), 1), 40)
    days = min(max(int(days or 30), 7), 180)
    since = timezone.now() - timedelta(days=days)
    branch_obj = _resolve_branch(company, branch)

    items = SaleItem.objects.filter(
        sale__company=company, sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED],
        sale__paid_at__gte=since,
    )
    if branch_obj:
        items = items.filter(sale__branch=branch_obj)
    sold = {
        r["product_id"]: (Decimal(str(r["qty"] or 0)), r["rev"] or ZERO_MONEY)
        for r in items.values("product_id").annotate(
            qty=Sum("quantity"),
            rev=Coalesce(Sum(F("unit_price") * F("quantity") - F("line_discount")), Value(ZERO_MONEY, output_field=MONEY_FIELD)),
        )
        if r["product_id"]
    }
    prods = Product.objects.filter(company=company, kind=Product.Kind.PRODUCT).exclude(status=Product.Status.ARCHIVED)
    if branch_obj:
        prods = prods.filter(Q(branch=branch_obj) | Q(branch__isnull=True))
    prods = prods.only("id", "name", "quantity", "price", "purchase_price", "markup_percent", "minimum_quantity")

    sold_out, running_low, not_selling, overstock, low_markup = [], [], [], [], []
    min_mk = Decimal(str(min_markup)) if min_markup is not None else Decimal("10")
    for p in prods:
        qty = Decimal(str(p.quantity or 0))
        s_qty, s_rev = sold.get(p.id, (Decimal("0"), ZERO_MONEY))
        daily = s_qty / Decimal(days)
        price = Decimal(str(p.price or 0))
        purchase = Decimal(str(p.purchase_price or 0))
        base = {"name": p.name, "stock": _fmt(qty), "sold_period": _fmt(s_qty), "price": _money_str(price)}
        if s_qty > 0 and qty <= 0:
            sold_out.append({
                **base, "lost_revenue_per_day": _money_str(daily * price),
                "order_qty_14d": int(math.ceil(float(daily) * 14)) or 1,
            })
        elif s_qty > 0 and qty > 0 and daily > 0 and qty / daily < 7:
            days_left = float(qty / daily)
            running_low.append({
                **base, "days_left": round(days_left, 1),
                "order_qty_14d": max(int(math.ceil(float(daily) * 14 - float(qty))), 1),
            })
        elif s_qty == 0 and qty > 0:
            not_selling.append({**base, "frozen_money": _money_str(qty * (purchase or price))})
        elif s_qty > 0 and daily > 0 and qty / daily > 90:
            overstock.append({**base, "days_of_stock": int(qty / daily), "frozen_money": _money_str(qty * (purchase or price))})
        if purchase > 0 and price > 0:
            mk = (price - purchase) / purchase * Decimal("100")
            if mk < min_mk:
                low_markup.append({**base, "purchase_price": _money_str(purchase), "markup_percent": float(mk.quantize(Decimal("0.1")))})

    sold_out.sort(key=lambda r: Decimal(r["lost_revenue_per_day"]), reverse=True)
    running_low.sort(key=lambda r: r["days_left"])
    not_selling.sort(key=lambda r: Decimal(r["frozen_money"]), reverse=True)
    overstock.sort(key=lambda r: Decimal(r["frozen_money"]), reverse=True)
    low_markup.sort(key=lambda r: r["markup_percent"])

    out = {"period_days": days, "counts": {
        "sold_out_bestsellers": len(sold_out), "running_low": len(running_low), "not_selling": len(not_selling),
        "overstock": len(overstock), "low_markup": len(low_markup),
    }}
    k = str(kind or "all").lower()
    if k in ("all", "sold_out"):
        out["sold_out_bestsellers"] = sold_out[:lim]
    if k in ("all", "running_low", "low"):
        out["running_low"] = running_low[:lim]
    if k in ("all", "not_selling", "dead"):
        out["not_selling"] = not_selling[:lim]
    if k in ("all", "overstock"):
        out["overstock"] = overstock[:lim]
    if k in ("all", "low_markup"):
        out["low_markup"] = low_markup[:lim]
    return out


def fn_get_expiring(company, branch=None, days=14, limit=20) -> dict:
    """Сроки годности: просроченные и истекающие в ближайшие N дней."""
    from apps.main.models import Product

    lim = min(max(int(limit or 20), 1), 50)
    horizon = timezone.localdate() + timedelta(days=min(max(int(days or 14), 1), 365))
    today = timezone.localdate()
    qs = (
        Product.objects.filter(company=company, expiration_date__isnull=False, expiration_date__lte=horizon, quantity__gt=0)
        .exclude(status=Product.Status.ARCHIVED)
        .order_by("expiration_date")
    )
    branch_obj = _resolve_branch(company, branch)
    if branch_obj:
        qs = qs.filter(Q(branch=branch_obj) | Q(branch__isnull=True))
    expired, soon = [], []
    for p in qs[:200]:
        left = (p.expiration_date - today).days
        row = {"name": p.name, "stock": _fmt(p.quantity), "expiration_date": p.expiration_date.strftime("%d.%m.%Y"),
               "days_left": left, "stock_value": _money_str(Decimal(str(p.quantity or 0)) * Decimal(str(p.purchase_price or p.price or 0)))}
        (expired if left < 0 else soon).append(row)
    return {"expired_count": len(expired), "expiring_count": len(soon), "expired": expired[:lim], "expiring": soon[:lim]}


def fn_propose_product_changes(company, changes=None, ctx=None, **_ignored) -> dict:
    """
    ТЗ ч.15, п. 4: ИИ предлагает изменения товаров; выполняются только после «Выполнить».
    Кладёт список в ожидание подтверждения и возвращает, что разобрано, а что не найдено.
    """
    from apps.main.telegram_bot.services import owner_actions

    settings_obj = (ctx or {}).get("settings")
    chat_id = (ctx or {}).get("chat_id")
    if not chat_id or settings_obj is None or not getattr(settings_obj, "ai_owner_actions_enabled", True):
        return {"error": "Изменения товаров через бота отключены в настройках.", "total_count": 0}
    min_markup = Decimal(str(getattr(settings_obj, "ai_min_markup_percent", 20) or 20))
    norm, not_found, errors = owner_actions.normalize_changes(company, changes or [], min_markup=min_markup)
    if not norm:
        return {"pending": False, "resolved": [], "not_found": not_found, "errors": errors}
    pending = owner_actions.create_pending(company, chat_id, norm, kind="product_changes", source="ai")
    return {
        "pending": True,
        "pending_id": pending["id"],
        "resolved": [owner_actions.describe_change(c) for c in norm],
        "not_found": not_found,
        "errors": errors,
        "note": "Бот уже показал владельцу список с кнопками «Выполнить»/«Отмена». Попроси подтвердить.",
    }


# =========================================================================
# Диспетчер вызова функций
# =========================================================================

FUNCTION_MAP = {
    "get_sales_summary": fn_get_sales_summary,
    "get_pnl": fn_get_pnl,
    "get_cashflow": fn_get_cashflow,
    "get_top_products": fn_get_top_products,
    "get_abc": fn_get_abc,
    "get_stock": fn_get_stock,
    "get_dead_stock": fn_get_dead_stock,
    "get_product": fn_get_product,
    "get_debtors": fn_get_debtors,
    "get_client": fn_get_client,
    "get_shift": fn_get_shift,
    "get_returns": fn_get_returns,
    "get_purchases": fn_get_purchases,
    "get_finance_operations": fn_get_finance_operations,
    "get_salary": fn_get_salary,
    "get_cashiers": fn_get_cashiers,
    "get_reconcile": fn_get_reconcile,
    "get_orders": fn_get_orders,
    "get_bot_stats": fn_get_bot_stats,
    "compare_periods": fn_compare_periods,
    # ТЗ ч.15 — только чат владельца
    "get_staff": fn_get_staff,
    "get_shift_archive": fn_get_shift_archive,
    "get_stock_alerts": fn_get_stock_alerts,
    "get_expiring": fn_get_expiring,
    "propose_product_changes": fn_propose_product_changes,
}
CONTEXT_FUNCTIONS = {"propose_product_changes"}


def execute_ai_function(company, function_name: str, args: dict, ctx: dict = None) -> dict:
    """Выполняет запрошенную ИИ функцию и возвращает структурированный JSON."""
    handler = FUNCTION_MAP.get(function_name)
    if not handler:
        logger.warning("Unknown function requested by AI: %s", function_name)
        return {"error": f"Функция {function_name} не поддерживается.", "total_count": 0}

    try:
        clean_args = {k: v for k, v in (args or {}).items() if v is not None}
        if function_name in CONTEXT_FUNCTIONS:
            clean_args["ctx"] = ctx or {}
        return handler(company=company, **clean_args)
    except Exception as exc:
        logger.exception("Error executing AI function %s: %s", function_name, exc)
        return {"error": f"Ошибка выполнения {function_name}: {exc}", "total_count": 0}


# =========================================================================
# Gemini Tool Declarations (functionDeclarations)
# =========================================================================

AI_TOOL_DECLARATIONS = [
    {
        "name": "get_sales_summary",
        "description": "Выручка, число чеков, средний чек, валовая прибыль, маржа %, возвраты, способы оплаты и динамика продаж по дням.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "branch": {"type": "STRING", "description": "Филиал (необязательно)"},
            },
        },
    },
    {
        "name": "get_pnl",
        "description": "Отчёт о прибылях и убытках (P&L): выручка, себестоимость (COGS), валовая прибыль, маржа %, опер. расходы по статьям, опер. прибыль, чистая прибыль.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "branch": {"type": "STRING", "description": "Филиал (необязательно)"},
            },
        },
    },
    {
        "name": "get_cashflow",
        "description": "Отчёт о движении денег (Cash Flow): приход (наличные, безнал, оплата долгов, прочее) и расход (поставщики, зарплаты, аренда, налоги), чистое движение.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "branch": {"type": "STRING", "description": "Филиал (необязательно)"},
            },
        },
    },
    {
        "name": "get_top_products",
        "description": "Рейтинг товаров за период: по выручке, по прибыли или по проданному количеству (штук).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "sort": {"type": "STRING", "description": "Сортировка: 'revenue' (выручка), 'profit' (прибыль), 'qty' (количество)"},
                "limit": {"type": "INTEGER", "description": "Количество товаров (до 30, по умолчанию 10)"},
                "order": {"type": "STRING", "description": "'desc' (лидеры) или 'asc' (аутсайдеры)"},
            },
        },
    },
    {
        "name": "get_abc",
        "description": "ABC-анализ ассортимента (группы A, B, C: число позиций, сумма, доля выручки и ключевые товары).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "slice": {"type": "STRING", "description": "'revenue' (по выручке), 'profit' (по прибыли), 'qty' (по кол-ву)"},
            },
        },
    },
    {
        "name": "get_stock",
        "description": "Складские остатки: общая стоимость склада по закупке и по продаже, заканчивающиеся товары, поиск остатка конкретного товара.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Название товара или штрихкод (необязательно)"},
                "only_low": {"type": "BOOLEAN", "description": "true — только заканчивающиеся товары"},
                "limit": {"type": "INTEGER", "description": "Количество позиций (до 30)"},
                "branch": {"type": "STRING", "description": "Филиал (необязательно)"},
            },
        },
    },
    {
        "name": "get_dead_stock",
        "description": "Мёртвый склад (неликвид): товары с остатком больше нуля без единой продажи за N дней и сумма замороженных денег.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {"type": "INTEGER", "description": "Период без продаж в днях (по умолчанию 30)"},
                "limit": {"type": "INTEGER", "description": "Количество позиций (до 30)"},
            },
        },
    },
    {
        "name": "get_product",
        "description": "Полная карточка товара: цена продажи, закупка, текущий остаток, продажи за 7 и 30 дней, последняя закупка (дата, поставщик, цена), варианты размеров и цветов с остатком и акционной ценой.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Название товара, штрихкод или артикул"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_debtors",
        "description": "Список клиентов-должников магазина, суммы долгов, дата последней покупки и общая дебиторская задолженность.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {"type": "INTEGER", "description": "Количество должников (по умолчанию 10)"},
                "min_amount": {"type": "NUMBER", "description": "Минимальная сумма долга"},
            },
        },
    },
    {
        "name": "get_client",
        "description": "Информация о клиенте: телефон, текущий долг, сумма и количество покупок, бонусы.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Имя или номер телефона клиента"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_shift",
        "description": "Отчёт кассовой смены: кассир, продажи (наличные/безнал/долг), внесения/изъятия, ожидаемая наличность в кассе и расхождение.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "which": {"type": "STRING", "description": "'current' (текущая смена) или 'last' (последняя закрытая)"},
                "date": {"type": "STRING", "description": "Дата смены (ГГГГ-ММ-ДД)"},
            },
        },
    },
    {
        "name": "get_returns",
        "description": "Возвраты от покупателей: общая сумма возвратов, число возвратов, топ возвращаемых товаров.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
            },
        },
    },
    {
        "name": "get_purchases",
        "description": "Закупки у поставщиков: сумма поставок, разбивка по поставщикам и долги перед ними.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "supplier": {"type": "STRING", "description": "Имя конкретного поставщика (необязательно)"},
            },
        },
    },
    {
        "name": "get_finance_operations",
        "description": "Финансовые статьи доходов и расходов магазина.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "type": {"type": "STRING", "description": "'income', 'expense' или 'all'"},
            },
        },
    },
    {
        "name": "get_salary",
        "description": "Начисления заработной платы сотрудникам (оклад, процент с продаж, комиссии).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
            },
        },
    },
    {
        "name": "get_cashiers",
        "description": "Продажи по сотрудникам и кассирам: выручка, число чеков, средний чек, скидки.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
            },
        },
    },
    {
        "name": "get_reconcile",
        "description": "Сверка магазина (7 проверок сходимости: выручка, кассы, остатки, возвраты, оплаты).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
            },
        },
    },
    {
        "name": "get_orders",
        "description": "Заказы покупателей через витрину и Telegram-бота: количество, сумма и статусы.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "source": {"type": "STRING", "description": "'telegram', 'showcase' или пусто (все)"},
            },
        },
    },
    {
        "name": "get_bot_stats",
        "description": "Статистика работы Telegram-бота: сколько покупателей обратилось, число сообщений и заказов.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
            },
        },
    },
    {
        "name": "compare_periods",
        "description": "Сравнить два периода между собой (например сентябрь и август, или прошлая неделя и эта) по выручке, прибыли или чекам.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "metric": {"type": "STRING", "description": "'revenue' (выручка), 'profit' (прибыль), 'checks' (чеки)"},
                "period1_from": {"type": "STRING", "description": "Период 1: начало (ГГГГ-ММ-ДД)"},
                "period1_to": {"type": "STRING", "description": "Период 1: конец (ГГГГ-ММ-ДД)"},
                "period2_from": {"type": "STRING", "description": "Период 2: начало (ГГГГ-ММ-ДД)"},
                "period2_to": {"type": "STRING", "description": "Период 2: конец (ГГГГ-ММ-ДД)"},
            },
            "required": ["metric", "period1_from", "period1_to", "period2_from", "period2_to"],
        },
    },
    {
        "name": "get_staff",
        "description": "Сотрудники и зарплата + табель: по каждому — схема оплаты (оклад, процент, за товар), начислено, продажи, чеки, продано штук; смен, дней, часов по закрытым сменам, средняя смена, выручка смен, кто сейчас на смене. Без дат — с начала месяца.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
                "employee": {"type": "STRING", "description": "Имя сотрудника (необязательно)"},
            },
        },
    },
    {
        "name": "get_shift_archive",
        "description": "Архив смен (Z-отчёты): последние закрытые смены — кассир, открыта, закрыта, выручка наличными и безналом, чеков, касса на начало и при закрытии; плюс открытые сейчас смены.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {"type": "INTEGER", "description": "Сколько закрытых смен (по умолчанию 10, до 30)"},
                "date_from": {"type": "STRING", "description": "Начало периода (ГГГГ-ММ-ДД)"},
                "date_to": {"type": "STRING", "description": "Конец периода (ГГГГ-ММ-ДД)"},
            },
        },
    },
    {
        "name": "get_stock_alerts",
        "description": "Товары к заказу: хорошо продавались, но закончились (теряемая выручка в день, сколько заказать на 14 дней); популярные, но осталось мало (на сколько дней хватит, сколько заказать); не продаются 30 дней; затоварено; низкая наценка.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "kind": {"type": "STRING", "description": "'all', 'sold_out', 'running_low', 'not_selling', 'overstock', 'low_markup'"},
                "days": {"type": "INTEGER", "description": "Период продаж в днях (по умолчанию 30)"},
                "limit": {"type": "INTEGER", "description": "Позиций в каждом списке (до 40)"},
            },
        },
    },
    {
        "name": "get_expiring",
        "description": "Сроки годности: просроченные товары и те, что истекают в ближайшие N дней (остаток, дата, сумма по закупке).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {"type": "INTEGER", "description": "Горизонт в днях (по умолчанию 14)"},
                "limit": {"type": "INTEGER", "description": "Позиций (до 50)"},
            },
        },
    },
]

# Действия — отдельный список: подключается только в чате владельца при ai_owner_actions_enabled.
OWNER_ACTION_TOOL_DECLARATIONS = [
    {
        "name": "propose_product_changes",
        "description": (
            "Предложить изменения товаров (выполняются ТОЛЬКО после подтверждения владельца кнопкой). "
            "Действия: add — приход (qty, при желании purchase_price и price); writeoff — списание/брак (qty, reason); "
            "set — точный остаток после ревизии (qty); update — изменить поле (field: price, purchase_price, minimum_quantity, "
            "expiration_date, shelf_life_days, description, country, brand, category, barcode, article; value); "
            "create — новый товар (name, qty, purchase_price, price, barcode)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "changes": {
                    "type": "ARRAY",
                    "description": "Список изменений",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "action": {"type": "STRING", "description": "add | writeoff | set | update | create"},
                            "product": {"type": "STRING", "description": "Название товара (или штрихкод) как сказал владелец"},
                            "barcode": {"type": "STRING", "description": "Штрихкод, если известен"},
                            "qty": {"type": "NUMBER", "description": "Количество для add/writeoff/set/create"},
                            "field": {"type": "STRING", "description": "Поле для update"},
                            "value": {"type": "STRING", "description": "Новое значение для update"},
                            "purchase_price": {"type": "NUMBER", "description": "Закупочная цена (add/create)"},
                            "price": {"type": "NUMBER", "description": "Цена продажи (add/create)"},
                            "reason": {"type": "STRING", "description": "Причина (например, просрочка, брак)"},
                        },
                        "required": ["action"],
                    },
                }
            },
            "required": ["changes"],
        },
    },
]
