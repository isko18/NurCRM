from __future__ import annotations

import logging
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
    tables = data.get("tables", {})

    return {
        "period": {"date_from": start.date().isoformat(), "date_to": (end - timedelta(days=1)).date().isoformat()},
        "total_payroll": cards.get("total_payroll", "0.00"),
        "employees_count": cards.get("employees_count", 0),
        "employees": [
            {
                "name": e.get("name"),
                "role": e.get("role"),
                "total_payable": e.get("total_payable", "0.00"),
            }
            for e in tables.get("employees", [])[:15]
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
}


def execute_ai_function(company, function_name: str, args: dict) -> dict:
    """Выполняет запрошенную ИИ функцию и возвращает структурированный JSON."""
    handler = FUNCTION_MAP.get(function_name)
    if not handler:
        logger.warning("Unknown function requested by AI: %s", function_name)
        return {"error": f"Функция {function_name} не поддерживается.", "total_count": 0}

    try:
        clean_args = {k: v for k, v in (args or {}).items() if v is not None}
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
]
