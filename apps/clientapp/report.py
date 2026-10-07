"""
Отчёт по неделям для решения, когда заканчивать бесплатный период приложения клиентов:
сколько магазинов, сколько покупателей, сколько продаж через приложение.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.main.models import Client, Sale

from .models import AppCustomer, AppShopSettings

SALE_STATUSES = (Sale.Status.PAID, Sale.Status.PARTIALLY_RETURNED, Sale.Status.DEBT)


def weekly_report(weeks: int = 12):
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    bounds = [
        (d, timezone.make_aware(datetime.combine(d, time.min), tz),
         timezone.make_aware(datetime.combine(d + timedelta(days=7), time.min), tz))
        for d in starts
    ]
    first_start = bounds[0][1]

    shops = list(AppShopSettings.objects.filter(branch__isnull=True, show_in_app=True).values_list("created_at", flat=True))
    customers = list(AppCustomer.objects.values_list("phone", "created_at", "deleted_at"))
    reg_at = {phone: created for phone, created, _d in customers if phone}

    client_phone = dict(
        Client.objects.filter(phone_normalized__in=list(reg_at)).values_list("id", "phone_normalized")
    )
    sales = Sale.objects.filter(
        client_id__in=list(client_phone), status__in=SALE_STATUSES, created_at__gte=first_start
    ).values_list("client_id", "created_at", "total")

    by_week = defaultdict(lambda: {"count": 0, "total": Decimal("0.00"), "buyers": set()})
    for client_id, created_at, total in sales:
        phone = client_phone.get(client_id)
        if phone is None or created_at < reg_at[phone]:
            continue  # покупка до регистрации в приложении — не через приложение
        for d, start, end in bounds:
            if start <= created_at < end:
                w = by_week[d]
                w["count"] += 1
                w["total"] += total or Decimal("0.00")
                w["buyers"].add(phone)
                break

    rows = []
    for d, start, end in bounds:
        w = by_week[d]
        rows.append({
            "week_start": d.isoformat(),
            "week_end": (d + timedelta(days=6)).isoformat(),
            "shops_enabled": sum(1 for c in shops if c < end),
            "shops_new": sum(1 for c in shops if start <= c < end),
            "customers_total": sum(1 for _p, c, dl in customers if c < end and (dl is None or dl >= end)),
            "customers_new": sum(1 for _p, c, _d in customers if start <= c < end),
            "app_sales_count": w["count"],
            "app_sales_total": str(w["total"].quantize(Decimal("0.01"))),
            "app_buyers": len(w["buyers"]),
        })

    from .services import app_free_until, get_shops_cached

    free, until = app_free_until()
    visible, _etag = get_shops_cached()
    return {
        "free_period": {"active": free, "free_until": until.isoformat() if until else None},
        "now": {
            "shops_on_map": len(visible),
            "companies_on_map": len({s["companyId"] for s in visible}),
            "customers": AppCustomer.objects.filter(deleted_at__isnull=True).count(),
        },
        "weeks": rows,
    }
