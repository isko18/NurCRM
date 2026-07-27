"""
Сборка снапшота сводки продаж (Сводка).

Снапшот фиксируется при создании сводки и пересобирается через regenerate.
Источник данных — накладные продаж (Document, doc_type=SALE) за дату сводки по складу,
опционально ограниченные выбранными агентами (type=by_agents).
"""

import re
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from . import models
from .services import effective_document_line_discount_percent

# Какие накладные НЕ считаем «продажами за день»: отклонённые, заявки на продажу и черновики.
EXCLUDED_SALE_STATUSES = (
    models.Document.Status.REJECTED,
    models.Document.Status.SALE_REQUEST,
    models.Document.Status.DRAFT,
)

TWOPLACES = Decimal("0.01")
THREEPLACES = Decimal("0.001")


def _q3(value) -> Decimal:
    return Decimal(value or 0).quantize(THREEPLACES)


def _q2(value) -> Decimal:
    return Decimal(value or 0).quantize(TWOPLACES)


def _full_name(user) -> str:
    if not user:
        return ""
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = f"{first} {last}".strip()
    return full or getattr(user, "email", "") or str(getattr(user, "id", ""))


def next_summary_number(company) -> str:
    """Следующий человекочитаемый номер сводки (СВ-000123) в рамках компании."""
    max_n = 0
    numbers = (
        models.WarehouseSalesSummary.objects
        .filter(company=company, number__startswith="СВ-")
        .values_list("number", flat=True)
    )
    for num in numbers:
        match = re.match(r"^СВ-(\d+)$", num or "")
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"СВ-{max_n + 1:06d}"


def _documents_queryset(summary):
    """Накладные продаж, попадающие в снапшот сводки."""
    warehouse_ids = summary.warehouse_ids()
    qs = (
        models.Document.objects
        .filter(
            doc_type=models.Document.DocType.SALE,
            warehouse_from_id__in=warehouse_ids,
            date__date=summary.date,
        )
        .exclude(status__in=EXCLUDED_SALE_STATUSES)
        .select_related("counterparty", "agent")
    )
    if not warehouse_ids:
        return qs.none()
    if summary.type == models.WarehouseSalesSummary.Type.BY_AGENTS:
        agent_ids = list(summary.agents.values_list("id", flat=True))
        qs = qs.filter(agent_id__in=agent_ids) if agent_ids else qs.none()
    return qs


def _product_weight_per_unit(product) -> Decimal:
    """Вес единицы товара: фактический вес из характеристик, иначе 1 для весовых, иначе 0."""
    if product is None:
        return Decimal("0")
    characteristics = getattr(product, "characteristics", None)
    if characteristics and characteristics.factual_weight_kg:
        return Decimal(characteristics.factual_weight_kg)
    if getattr(product, "is_weight", False):
        return Decimal("1")
    return Decimal("0")


def _per_package(product) -> Decimal:
    """Кол-во базовых единиц в одной упаковке (первая упаковка товара), иначе 1."""
    if product is None:
        return Decimal("1")
    package = next(iter(getattr(product, "packages").all()), None) if hasattr(product, "packages") else None
    if package and package.quantity_in_package and package.quantity_in_package > 0:
        return Decimal(package.quantity_in_package)
    return Decimal("1")


@transaction.atomic
def build_summary_snapshot(summary):
    """
    Пересобирает снапшот сводки: documents, products, totals.
    Возвращает обновлённый объект summary.
    """
    summary.documents.all().delete()
    summary.products.all().delete()

    docs = list(_documents_queryset(summary))
    doc_by_id = {doc.id: doc for doc in docs}

    # --- Накладные (снапшот построчно) ---
    item_totals = (
        models.DocumentItem.objects
        .filter(document__in=docs)
        .values("document_id")
        .annotate(qty=Sum("qty"))
    )
    qty_by_doc = {row["document_id"]: row["qty"] or Decimal("0") for row in item_totals}

    # Вес по накладной считаем из строк с учётом веса единицы товара.
    weight_by_doc = {}
    items = (
        models.DocumentItem.objects
        .filter(document__in=docs)
        .select_related("product", "product__characteristics")
        .prefetch_related("product__packages")
    )
    # Группировка товаров: (product_id, unit, price) -> агрегаты
    product_groups = {}
    # Позиции по накладным (детализация для PDF): document_id -> [snapshot dict]
    items_by_doc = {}
    for item in items:
        product = item.product
        qty = Decimal(item.qty or 0)
        amount = Decimal(item.line_total or 0)
        weight_per_unit = _product_weight_per_unit(product)
        weight = qty * weight_per_unit

        weight_by_doc[item.document_id] = weight_by_doc.get(item.document_id, Decimal("0")) + weight

        unit = (getattr(product, "unit", "") or "").strip()
        price = Decimal(item.price or 0)

        # Эффективная скидка строки = % на товар, иначе общая скидка документа
        # (совпадает с тем, как считается line_total в DocumentItem.save).
        doc = doc_by_id.get(item.document_id)
        doc_dp = Decimal(getattr(doc, "discount_percent", None) or 0)
        eff_pct = effective_document_line_discount_percent(item.discount_percent, doc_dp)
        items_by_doc.setdefault(item.document_id, []).append({
            "name": getattr(product, "name", "") or "",
            "unit": unit,
            "quantity": _q3(qty),
            "price": _q2(price),
            "discount_percent": Decimal(eff_pct).quantize(TWOPLACES),
            "discount_amount": _q2(item.discount_amount),
            "amount": _q2(amount),
            "weight": _q3(weight),
        })

        unit_lower = unit.lower()
        name_clean = (getattr(product, "name", "") or "").strip()
        key = (name_clean, unit_lower, price)
        group = product_groups.get(key)
        if group is None:
            group = {
                "product": product,
                "name": name_clean,
                "unit": unit,
                "price": price,
                "quantity": Decimal("0"),
                "amount": Decimal("0"),
                "weight": Decimal("0"),
            }
            product_groups[key] = group
        group["quantity"] += qty
        group["amount"] += amount
        group["weight"] += weight

    summary_doc_rows = []
    for doc in docs:
        counterparty = doc.counterparty
        summary_doc_rows.append(models.WarehouseSalesSummaryDocument(
            summary=summary,
            document=doc,
            number=doc.number or "",
            date=doc.date.date() if doc.date else summary.date,
            agent=_full_name(doc.agent),
            client=getattr(counterparty, "name", "") or "",
            address=getattr(counterparty, "address", "") or "",
            quantity=_q3(qty_by_doc.get(doc.id, 0)),
            weight=_q3(weight_by_doc.get(doc.id, 0)),
            amount=_q2(doc.total or 0),
        ))
    models.WarehouseSalesSummaryDocument.objects.bulk_create(summary_doc_rows)

    # --- Позиции накладных (детализация для PDF) ---
    # summary_doc_rows получили id (UUID) ещё до bulk_create, поэтому связь по document_id надёжна.
    doc_row_by_docid = {row.document_id: row for row in summary_doc_rows}
    summary_item_rows = []
    for doc_id, item_list in items_by_doc.items():
        summary_doc = doc_row_by_docid.get(doc_id)
        if summary_doc is None:
            continue
        for it in item_list:
            summary_item_rows.append(models.WarehouseSalesSummaryDocumentItem(
                summary_document=summary_doc,
                name=it["name"],
                unit=it["unit"],
                quantity=it["quantity"],
                price=it["price"],
                discount_percent=it["discount_percent"],
                discount_amount=it["discount_amount"],
                amount=it["amount"],
                weight=it["weight"],
            ))
    if summary_item_rows:
        models.WarehouseSalesSummaryDocumentItem.objects.bulk_create(summary_item_rows)

    # --- Товары (агрегированная таблица) ---
    summary_product_rows = []
    for group in product_groups.values():
        quantity = group["quantity"]
        per_package = _per_package(group["product"])
        packages = (quantity / per_package) if per_package else quantity
        summary_product_rows.append(models.WarehouseSalesSummaryProduct(
            summary=summary,
            product=group["product"],
            name=group["name"],
            unit=group["unit"],
            packages=_q3(packages),
            per_package=_q3(per_package),
            quantity=_q3(quantity),
            price=_q2(group["price"]),
            amount=_q2(group["amount"]),
            weight=_q3(group["weight"]),
        ))
    models.WarehouseSalesSummaryProduct.objects.bulk_create(summary_product_rows)

    # --- Итоги ---
    summary.documents_count = len(summary_doc_rows)
    summary.products_count = len(summary_product_rows)
    summary.total_quantity = _q3(sum((row.quantity for row in summary_product_rows), Decimal("0")))
    summary.total_weight = _q3(sum((row.weight for row in summary_product_rows), Decimal("0")))
    summary.total_amount = _q2(sum((row.amount for row in summary_product_rows), Decimal("0")))
    summary.save(update_fields=[
        "documents_count", "products_count",
        "total_quantity", "total_weight", "total_amount", "updated_at",
    ])
    return summary
