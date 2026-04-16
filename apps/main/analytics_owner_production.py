from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Sum, Count, Value as V, F, DecimalField, Subquery, OuterRef, Q
from django.db.models.functions import Coalesce, TruncDate, TruncWeek, TruncMonth
from django.utils import timezone

from django.db.models.expressions import ExpressionWrapper

from apps.users.models import User
from apps.construction.models import CashFlow
from .models import ManufactureSubreal, Acceptance, Sale, SaleItem, ClientDeal, DealInstallment, Product, ItemMake

try:
    from apps.warehouse.models import Document as WarehouseStockDocument
except Exception:  # pragma: no cover - склад может быть отключён в тестах без миграций
    WarehouseStockDocument = None

try:
    from apps.warehouse.models import MoneyDocument as WarehouseMoneyDocument, Counterparty as WarehouseCounterparty
except Exception:  # pragma: no cover
    WarehouseMoneyDocument = None
    WarehouseCounterparty = None

try:
    from apps.building.models import BuildingDebtLedgerEntry
except Exception:  # pragma: no cover - building может быть отключён в тестах без миграций
    BuildingDebtLedgerEntry = None


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

    # Эффективная закупочная для COGS: снапшот строки, иначе текущая закупка товара (как при создании SaleItem)
    _unit_purchase = Coalesce(
        F("purchase_price_snapshot"),
        F("product__purchase_price"),
        V(Decimal("0"), output_field=MONEY_FIELD),
    )

    # ======================================================
    # Gross profit (валовая прибыль): revenue − COGS; маржа % = прибыль / выручка × 100
    # ======================================================
    # Выручка должна учитывать скидки по строкам (line_discount), иначе валовая прибыль завышается.
    revenue_expr = ExpressionWrapper(
        (F("quantity") * F("unit_price")) - Coalesce(F("line_discount"), ZERO_MONEY),
        output_field=MONEY_FIELD,
    )
    items_agg = items_qs.aggregate(
        revenue=Coalesce(
            Sum(revenue_expr, output_field=MONEY_FIELD),
            ZERO_MONEY,
        ),
        cogs=Coalesce(
            Sum(F("quantity") * _unit_purchase, output_field=MONEY_FIELD),
            ZERO_MONEY,
        ),
    )
    revenue_dec = items_agg["revenue"] or Decimal("0.00")
    cogs_dec = items_agg["cogs"] or Decimal("0.00")
    gross_profit_dec = revenue_dec - cogs_dec
    gross_margin_pct = Decimal("0.00")
    if revenue_dec and revenue_dec > 0:
        gross_margin_pct = (gross_profit_dec / revenue_dec * Decimal("100")).quantize(Decimal("0.01"))

    trunc_gp = _trunc_by_group("sale__created_at", group_by)
    gross_profit_by_period_qs = (
        items_qs.annotate(period=trunc_gp)
        .values("period")
        .annotate(
            revenue_p=Coalesce(
                Sum(revenue_expr, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
            cogs_p=Coalesce(
                Sum(F("quantity") * _unit_purchase, output_field=MONEY_FIELD),
                ZERO_MONEY,
            ),
        )
        .order_by("period")
    )
    gross_profit_by_date = [
        {
            "date": row["period"],
            "revenue": _money_str(row["revenue_p"] or Decimal("0.00")),
            "cost_of_goods_sold": _money_str(row["cogs_p"] or Decimal("0.00")),
            "gross_profit": _money_str(
                (row["revenue_p"] or Decimal("0.00")) - (row["cogs_p"] or Decimal("0.00"))
            ),
        }
        for row in gross_profit_by_period_qs
    ]

    # ======================================================
    # Stock value (стоимость склада): sum(quantity * purchase_price)
    # ======================================================
    products_qs = Product.objects.filter(company=company)
    if branch is not None:
        # важно: как в ProductListView через CompanyBranchRestrictedMixin
        # при выбранном филиале берём ТОЛЬКО его записи (без branch=NULL)
        products_qs = products_qs.filter(branch=branch)
    else:
        products_qs = products_qs.filter(branch__isnull=True)
    stock_value_dec = (
        products_qs.aggregate(
            v=Coalesce(
                Sum(
                    Coalesce(F("quantity"), V(Decimal("0"), output_field=QTY_FIELD))
                    * F("purchase_price"),
                    output_field=MONEY_FIELD,
                ),
                ZERO_MONEY,
            )
        )["v"]
        or Decimal("0.00")
    )

    # Стоимость склада по розничной цене: sum(quantity * price)
    stock_retail_value_dec = (
        products_qs.aggregate(
            v=Coalesce(
                Sum(
                    Coalesce(F("quantity"), V(Decimal("0"), output_field=QTY_FIELD))
                    * F("price"),
                    output_field=MONEY_FIELD,
                ),
                ZERO_MONEY,
            )
        )["v"]
        or Decimal("0.00")
    )

    # Стоимость сырья: sum(quantity * price) по ItemMake
    item_make_qs = ItemMake.objects.filter(company=company)
    if branch is not None:
        # важно: как в ItemListCreateAPIView через CompanyBranchRestrictedMixin
        item_make_qs = item_make_qs.filter(branch=branch)
    else:
        item_make_qs = item_make_qs.filter(branch__isnull=True)

    raw_material_value_dec = (
        item_make_qs.aggregate(
            v=Coalesce(
                Sum(
                    Coalesce(F("quantity"), V(Decimal("0"), output_field=QTY_FIELD))
                    * F("price"),
                    output_field=MONEY_FIELD,
                ),
                ZERO_MONEY,
            )
        )["v"]
        or Decimal("0.00")
    )

    # ======================================================
    # Total debt (общий долг по клиентам):
    #   sum((deal.amount - deal.prepayment) - paid_per_deal_installment)
    # ======================================================
    deals_qs = ClientDeal.objects.filter(company=company, kind=ClientDeal.Kind.DEBT)
    if branch is not None:
        # В других метриках (например, stock_value) включают и филиальные, и глобальные записи.
        deals_qs = deals_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
    else:
        deals_qs = deals_qs.filter(branch__isnull=True)

    paid_subq = (
        DealInstallment.objects.filter(deal_id=OuterRef("pk"))
        .values("deal_id")
        .annotate(s=Sum("paid_amount"))
        .values("s")[:1]
    )

    client_deals_receivable_dec = (
        deals_qs.annotate(paid=Coalesce(Subquery(paid_subq), V(Decimal("0.00"), output_field=MONEY_FIELD)))
        .annotate(remaining=(F("amount") - F("prepayment")) - F("paid"))
        .aggregate(t=Sum("remaining"))["t"]
        or Decimal("0.00")
    )

    # Продажи POS/маркет «в долг» (клиент должен компании)
    sales_debt_qs = Sale.objects.filter(company=company, status=Sale.Status.DEBT)
    if branch is not None:
        sales_debt_qs = sales_debt_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
    else:
        sales_debt_qs = sales_debt_qs.filter(branch__isnull=True)
    pos_sales_receivable_dec = sales_debt_qs.aggregate(s=Coalesce(Sum("total"), ZERO_MONEY))["s"] or Decimal(
        "0.00"
    )

    accounts_receivable_dec = client_deals_receivable_dec + pos_sales_receivable_dec

    # Кредиторская задолженность:
    # - поставщики (склад): считаем сальдо по контрагентам как в сверке склада
    #   company_owes_counterparty = max( -( (doc_debit + money_expense) - (doc_credit + money_receipt) ), 0 )
    # - долги поставщикам из строительного реестра (building debt ledger)
    accounts_payable_dec = Decimal("0.00")
    if WarehouseStockDocument is not None and WarehouseMoneyDocument is not None and WarehouseCounterparty is not None:
        # контрагенты-поставщики компании
        cp_qs = WarehouseCounterparty.objects.filter(company=company).filter(
            Q(type=WarehouseCounterparty.Type.SUPPLIER) | Q(type=WarehouseCounterparty.Type.BOTH)
        )
        if branch is not None:
            cp_qs = cp_qs.filter(branch=branch)
        else:
            cp_qs = cp_qs.filter(branch__isnull=True)

        # товарные документы (проведённые) с контрагентом
        trade_doc_types = (
            WarehouseStockDocument.DocType.SALE,
            WarehouseStockDocument.DocType.PURCHASE,
            WarehouseStockDocument.DocType.SALE_RETURN,
            WarehouseStockDocument.DocType.PURCHASE_RETURN,
        )
        doc_debit_types = (WarehouseStockDocument.DocType.SALE, WarehouseStockDocument.DocType.PURCHASE_RETURN)
        doc_credit_types = (WarehouseStockDocument.DocType.PURCHASE, WarehouseStockDocument.DocType.SALE_RETURN)

        docs_qs = WarehouseStockDocument.objects.filter(
            status=WarehouseStockDocument.Status.POSTED,
            doc_type__in=trade_doc_types,
            counterparty__in=cp_qs,
            warehouse_from__company=company,
        )
        if branch is not None:
            docs_qs = docs_qs.filter(warehouse_from__branch=branch)
        else:
            docs_qs = docs_qs.filter(warehouse_from__branch__isnull=True)

        docs_agg = docs_qs.aggregate(
            doc_debit=Coalesce(Sum("total", filter=Q(doc_type__in=doc_debit_types)), ZERO_MONEY),
            doc_credit=Coalesce(Sum("total", filter=Q(doc_type__in=doc_credit_types)), ZERO_MONEY),
        )
        doc_debit = docs_agg["doc_debit"] or Decimal("0.00")
        doc_credit = docs_agg["doc_credit"] or Decimal("0.00")

        money_qs = WarehouseMoneyDocument.objects.filter(
            company=company,
            status=WarehouseMoneyDocument.Status.POSTED,
            counterparty__in=cp_qs,
            doc_type__in=(
                WarehouseMoneyDocument.DocType.MONEY_RECEIPT,
                WarehouseMoneyDocument.DocType.MONEY_EXPENSE,
            ),
        )
        if branch is not None:
            money_qs = money_qs.filter(branch=branch)
        else:
            money_qs = money_qs.filter(branch__isnull=True)

        money_agg = money_qs.aggregate(
            m_rec=Coalesce(Sum("amount", filter=Q(doc_type=WarehouseMoneyDocument.DocType.MONEY_RECEIPT)), ZERO_MONEY),
            m_paid=Coalesce(Sum("amount", filter=Q(doc_type=WarehouseMoneyDocument.DocType.MONEY_EXPENSE)), ZERO_MONEY),
        )
        m_rec = money_agg["m_rec"] or Decimal("0.00")
        m_paid = money_agg["m_paid"] or Decimal("0.00")

        # balance > 0: контрагент должен компании; balance < 0: компания должна контрагенту
        balance = (doc_debit + m_paid) - (doc_credit + m_rec)
        accounts_payable_dec = (-balance) if balance < 0 else Decimal("0.00")
        if accounts_payable_dec < 0:
            accounts_payable_dec = Decimal("0.00")

    if BuildingDebtLedgerEntry is not None:
        building_agg = BuildingDebtLedgerEntry.objects.filter(
            company=company,
            status=BuildingDebtLedgerEntry.Status.APPROVED,
            direction=BuildingDebtLedgerEntry.Direction.PAYABLE,
            counterparty_type=BuildingDebtLedgerEntry.CounterpartyType.SUPPLIER,
        ).aggregate(
            charges=Coalesce(
                Sum("amount", filter=Q(entry_type=BuildingDebtLedgerEntry.EntryType.CHARGE)),
                ZERO_MONEY,
            ),
            payments=Coalesce(
                Sum("amount", filter=Q(entry_type=BuildingDebtLedgerEntry.EntryType.PAYMENT)),
                ZERO_MONEY,
            ),
            barter=Coalesce(
                Sum("amount", filter=Q(entry_type=BuildingDebtLedgerEntry.EntryType.BARTER)),
                ZERO_MONEY,
            ),
            writeoff=Coalesce(
                Sum("amount", filter=Q(entry_type=BuildingDebtLedgerEntry.EntryType.WRITEOFF)),
                ZERO_MONEY,
            ),
            adjustments=Coalesce(
                Sum("amount", filter=Q(entry_type=BuildingDebtLedgerEntry.EntryType.ADJUSTMENT)),
                ZERO_MONEY,
            ),
        )
        building_accounts_payable_dec = (
            (building_agg["charges"] or Decimal("0.00"))
            - (building_agg["payments"] or Decimal("0.00"))
            - (building_agg["barter"] or Decimal("0.00"))
            - (building_agg["writeoff"] or Decimal("0.00"))
            + (building_agg["adjustments"] or Decimal("0.00"))
        )
        if building_accounts_payable_dec > 0:
            accounts_payable_dec += building_accounts_payable_dec

    # ======================================================
    # Expense breakdown (статья расходов): CashFlow by name
    # ======================================================
    cfqs = CashFlow.objects.filter(
        company=company,
        type=CashFlow.Type.EXPENSE,
        status=CashFlow.Status.APPROVED,
        created_at__gte=dt_from,
        created_at__lt=dt_to_excl,
    )
    if branch is not None:
        cfqs = cfqs.filter(Q(branch=branch) | Q(branch__isnull=True))
    else:
        cfqs = cfqs.filter(branch__isnull=True)
    expense_breakdown_qs = (
        cfqs.values("name")
        .annotate(
            total=Coalesce(Sum("amount"), ZERO_MONEY),
            count=Count("id"),
        )
        .order_by("-total")
    )
    expense_breakdown = [
        {
            "name": r["name"] or "Без названия",
            "total": _money_str(r["total"]),
            "count": r["count"],
        }
        for r in expense_breakdown_qs
    ]

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
            # Валовая прибыль (оплаченные продажи за период): выручка − себестоимость
            "revenue": _money_str(revenue_dec),
            "cost_of_goods_sold": _money_str(cogs_dec),
            "gross_profit": _money_str(gross_profit_dec),
            "gross_margin_percent": _money_str(gross_margin_pct),
            # склад
            "stock_value": _money_str(stock_value_dec),  # закупочная стоимость остатков
            "stock_purchase_value": _money_str(stock_value_dec),  # alias для новой карточки
            "stock_retail_value": _money_str(stock_retail_value_dec),
            # сырьё
            "raw_material_value": _money_str(raw_material_value_dec),
            # Дебиторская: долги клиентов (CRM-сделки + продажи «в долг»)
            "accounts_receivable": _money_str(accounts_receivable_dec),
            "accounts_receivable_client_deals": _money_str(client_deals_receivable_dec),
            "accounts_receivable_pos_sales": _money_str(pos_sales_receivable_dec),
            # Кредиторская: долг перед поставщиками по закупкам в кредит (склад)
            "accounts_payable": _money_str(accounts_payable_dec),
            # Совместимость: раньше только остаток по рассрочке ClientDeal
            "total_debt": _money_str(client_deals_receivable_dec),
        },
        "charts": {
            "sales_by_date": sales_by_date,
            "gross_profit_by_date": gross_profit_by_date,
            "transfers_by_date": transfers_by_date,
            "top_products_by_sales": top_products_by_sales,
            "top_users_by_sales": top_users_by_sales,
            "top_users_by_transfers": top_users_by_transfers,
            "sales_distribution_by_product": sales_distribution_by_product,
            "expense_breakdown": expense_breakdown,
        },
    }
