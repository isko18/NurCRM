from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Sum, Count, Value as V, F, DecimalField
from django.db.models.functions import Coalesce, TruncDate, TruncWeek, TruncMonth
from django.utils import timezone

from apps.users.models import User
from .models import ManufactureSubreal, Acceptance, Sale, SaleItem


# ─────────────────────────────────────────────────────────────
# typed zeros (важно: mixed types fix)
# ─────────────────────────────────────────────────────────────
MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)
ZERO_MONEY = V(Decimal("0.00"), output_field=MONEY_FIELD)

# quantity у SaleItem часто DecimalField → нужен Decimal-ноль
QTY_FIELD = DecimalField(max_digits=14, decimal_places=3)
ZERO_QTY = V(Decimal("0.000"), output_field=QTY_FIELD)


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
def _trunc_by_group(field_name: str, group_by: str):
    gb = (group_by or "day").strip().lower()
    if gb == "week":
        return TruncWeek(field_name)
    if gb == "month":
        return TruncMonth(field_name)
    return TruncDate(field_name)


def _dt_range(date_from: date, date_to: date):
    """
    (inclusive) date_from 00:00  -> (exclusive) (date_to+1) 00:00
    """
    tz = timezone.get_current_timezone()
    dt_from = timezone.make_aware(datetime.combine(date_from, datetime.min.time()), tz)
    dt_to_excl = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), datetime.min.time()),
        tz,
    )
    return dt_from, dt_to_excl


def _money_str(x) -> str:
    if x is None:
        return "0.00"
    if isinstance(x, Decimal):
        return str(x)
    try:
        return str(Decimal(str(x)))
    except Exception:
        return str(x)


# ─────────────────────────────────────────────────────────────
# main: owner overall analytics (FIXED)
# ─────────────────────────────────────────────────────────────
def build_owner_analytics_payload(*, company, branch, period, date_from, date_to, group_by="day"):
    """
    Общая аналитика владельца по компании.
    Возвращает: period + summary + charts
    """
    dt_from, dt_to_excl = _dt_range(date_from, date_to)

    # ======================================================
    # Transfers (all)
    # ======================================================
    sub_qs = ManufactureSubreal.objects.filter(
        company=company,
        created_at__gte=dt_from,
        created_at__lt=dt_to_excl,
    )
    if branch is not None:
        sub_qs = sub_qs.filter(branch=branch)
    else:
        sub_qs = sub_qs.filter(branch__isnull=True)

    transfers_count = sub_qs.count()
    items_transferred = sub_qs.aggregate(
        s=Coalesce(Sum("qty_transferred"), V(0))
    )["s"] or 0

    trunc_transfers = _trunc_by_group("created_at", group_by)
    transfers_by_period_qs = (
        sub_qs
        .annotate(period=trunc_transfers)
        .values("period")
        .annotate(
            transfers_count=Count("id"),
            items_transferred=Coalesce(Sum("qty_transferred"), V(0)),
        )
        .order_by("period")
    )
    transfers_by_date = [
        {
            "date": row["period"],
            "transfers_count": row["transfers_count"],
            "items_transferred": row["items_transferred"],
        }
        for row in transfers_by_period_qs
    ]

    top_users_by_transfers_qs = (
        sub_qs
        .values("agent_id", "agent__first_name", "agent__last_name", "agent__role")
        .annotate(
            transfers_count=Count("id"),
            items_transferred=Coalesce(Sum("qty_transferred"), V(0)),
        )
        .order_by("-items_transferred")[:10]
    )
    top_users_by_transfers = [
        {
            "user_id": str(r["agent_id"]),
            "user_name": (
                f"{(r['agent__first_name'] or '').strip()} {(r['agent__last_name'] or '').strip()}".strip()
                or "Пользователь"
            ),
            "role": r.get("agent__role"),
            "transfers_count": r["transfers_count"],
            "items_transferred": r["items_transferred"],
        }
        for r in top_users_by_transfers_qs
    ]

    # ======================================================
    # Acceptances (all)
    # ======================================================
    acc_qs = Acceptance.objects.filter(
        company=company,
        accepted_at__gte=dt_from,
        accepted_at__lt=dt_to_excl,
    )
    if branch is not None:
        acc_qs = acc_qs.filter(subreal__branch=branch)
    else:
        acc_qs = acc_qs.filter(subreal__branch__isnull=True)

    acceptances_count = acc_qs.count()

    # ======================================================
    # Sales (paid, all)
    # ======================================================
    sales_qs = Sale.objects.filter(
        company=company,
        status=Sale.Status.PAID,
        created_at__gte=dt_from,
        created_at__lt=dt_to_excl,
    )
    if branch is not None:
        sales_qs = sales_qs.filter(branch=branch)
    else:
        sales_qs = sales_qs.filter(branch__isnull=True)

    sales_count = sales_qs.count()

    # FIX: правильный Decimal-ноль с output_field
    sales_amount_dec = sales_qs.aggregate(
        s=Coalesce(Sum("total"), ZERO_MONEY)
    )["s"] or Decimal("0.00")

    items_qs = SaleItem.objects.filter(sale__in=sales_qs)

    trunc_sales = _trunc_by_group("sale__created_at", group_by)
    sales_by_period_qs = (
        items_qs
        .annotate(period=trunc_sales)
        .values("period")
        .annotate(
            sales_count=Count("sale_id", distinct=True),

            # FIX: quantity может быть DecimalField → Sum(quantity)=Decimal → нужен ZERO_QTY
            items_sold=Coalesce(Sum("quantity", output_field=QTY_FIELD), ZERO_QTY),

            # FIX: Coalesce для Decimal только с ZERO_MONEY
            amount=Coalesce(
                Sum(F("quantity") * F("unit_price"), output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
        )
        .order_by("period")
    )
    sales_by_date = [
        {
            "date": row["period"],
            "sales_count": row["sales_count"],
            "sales_amount": _money_str(row["amount"] or Decimal("0.00")),
        }
        for row in sales_by_period_qs
    ]

    top_products_qs = (
        items_qs
        .values("product_id", "product__name")
        .annotate(
            # FIX: quantity → Decimal
            qty=Coalesce(Sum("quantity", output_field=QTY_FIELD), ZERO_QTY),
            amount=Coalesce(
                Sum(F("quantity") * F("unit_price"), output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
        )
        .order_by("-amount")[:10]
    )
    top_products_by_sales = [
        {
            "product_id": str(r["product_id"]),
            "product_name": r["product__name"],
            # qty может быть Decimal — не убивай точность принудительным int, если она нужна
            # если точно нужна целая — оставь int(...)
            "qty": float(r["qty"] or Decimal("0.000")),
            "amount": _money_str(r["amount"] or Decimal("0.00")),
        }
        for r in top_products_qs
    ]

    top_users_by_sales_qs = (
        sales_qs
        .values("user_id", "user__first_name", "user__last_name", "user__role")
        .annotate(
            sales_count=Count("id"),
            # FIX: Decimal-ноль
            amount=Coalesce(Sum("total"), ZERO_MONEY),
        )
        .order_by("-amount")[:10]
    )
    top_users_by_sales = [
        {
            "user_id": str(r["user_id"]),
            "user_name": (
                f"{(r['user__first_name'] or '').strip()} {(r['user__last_name'] or '').strip()}".strip()
                or "Пользователь"
            ),
            "role": r.get("user__role"),
            "sales_count": r["sales_count"],
            "sales_amount": _money_str(r["amount"] or Decimal("0.00")),
        }
        for r in top_users_by_sales_qs
    ]

    # distribution by products (percent)
    sales_amount_float = float(sales_amount_dec) if sales_amount_dec else 0.0
    sales_distribution_by_product = []
    if sales_amount_float > 0:
        for row in top_products_by_sales:
            amt = float(Decimal(row["amount"]))
            sales_distribution_by_product.append({
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "amount": row["amount"],
                "percent": round(amt * 100.0 / sales_amount_float, 2),
            })

    users_count = User.objects.filter(company=company).count()

    return {
        "period": {
            "type": period,
            "date_from": date_from,
            "date_to": date_to,
            "group_by": group_by,
        },
        "summary": {
            "users_count": users_count,
            "transfers_count": transfers_count,
            "acceptances_count": acceptances_count,
            "items_transferred": items_transferred,
            "sales_count": sales_count,
            "sales_amount": _money_str(sales_amount_dec),
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "transfers_by_date": transfers_by_date,
            "top_products_by_sales": top_products_by_sales,
            "top_users_by_sales": top_users_by_sales,
            "top_users_by_transfers": top_users_by_transfers,
            "sales_distribution_by_product": sales_distribution_by_product,
        },
    }
