"""
Единый сервис остатков склада.

Принцип (см. stock-single-source-of-truth.md):

1. ``StockBalance`` (склад + товар) — единственный источник правды по остатку.
2. ``WarehouseProduct.quantity`` — денормализованная копия регистра для «своего»
   склада товара. Её пишет только этот модуль (``apply_stock_delta``).
3. Любое изменение регистра — это ``StockMove`` (с документом или с указанием
   источника). Инвариант: ``StockBalance.qty == Σ StockMove.qty_delta``.
4. Никаких ``max(карточка, регистр)``: проверка и списание идут по одному числу.

Переходный период: у части товаров остаток есть только в карточке (записи
``StockBalance`` нет). Для таких товаров при первом обращении с блокировкой
регистр разово инициализируется значением карточки, и создаётся движение
``source_kind="opening"`` (ленивый вариант шага 2 разовой сверки). Если запись
``StockBalance`` существует, карточка не читается никогда.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, F, Max, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from . import models
from .models import q_qty


ZERO = Decimal("0.000")

SourceKind = models.StockMove.SourceKind
MoveKind = models.StockMove.MoveKind


class InsufficientStock(ValueError):
    """
    Недостаточно товара на складе.

    Наследуется от ValueError: views складских документов уже превращают ValueError
    в ответ 400 {"detail": "Недостаточно товара ..."}.
    """

    def __init__(self, *, product, warehouse, available, required, message=None):
        self.product = product
        self.warehouse = warehouse
        self.available = q_qty(Decimal(available or 0))
        self.required = q_qty(Decimal(required or 0))
        if message is None:
            message = (
                f"Недостаточно товара '{product_display(product)}' на складе "
                f"'{getattr(warehouse, 'name', None) or 'не указан'}'. "
                f"Доступно: {self.available}, требуется: {self.required}"
            )
        super().__init__(message)


def product_display(product) -> str:
    if product is None:
        return "—"
    return (
        getattr(product, "article", None)
        or getattr(product, "name", None)
        or f"ID {getattr(product, 'pk', '')}"
    )


def _is_own_warehouse(product, warehouse) -> bool:
    return (
        product is not None
        and warehouse is not None
        and getattr(product, "warehouse_id", None) == getattr(warehouse, "pk", None)
    )


def _balance_qs(warehouse, product):
    return models.StockBalance.objects.filter(warehouse_id=warehouse.pk, product_id=product.pk)


def _card_qty_from_db(product, *, lock: bool) -> Decimal:
    qs = models.WarehouseProduct.objects.filter(pk=product.pk)
    if lock:
        qs = qs.select_for_update(of=("self",))
    row = qs.values_list("quantity", flat=True).first()
    return q_qty(Decimal(row or 0))


def _ensure_balance(warehouse, product) -> models.StockBalance:
    """
    Запись регистра под блокировкой (SELECT ... FOR UPDATE). Если её нет —
    создаётся: для «своего» склада товара значением карточки с движением
    opening (чтобы инвариант регистр == Σ движений сохранялся), иначе нулём.
    Должна вызываться внутри транзакции.
    """
    bal = _balance_qs(warehouse, product).select_for_update().first()
    if bal is not None:
        return bal

    opening = ZERO
    if _is_own_warehouse(product, warehouse):
        # Блокируем строку товара: параллельные инициализации одного товара
        # выстраиваются в очередь, вторая увидит уже созданный регистр.
        opening = _card_qty_from_db(product, lock=True)
        bal = _balance_qs(warehouse, product).select_for_update().first()
        if bal is not None:
            return bal

    # Если движения по паре уже есть (старые данные без регистра), открываем
    # регистр так, чтобы он совпал с карточкой, а Σ движений — с регистром.
    moves_sum = q_qty(
        models.StockMove.objects.filter(warehouse_id=warehouse.pk, product_id=product.pk)
        .aggregate(s=Sum("qty_delta"))["s"] or ZERO
    )
    target = opening if _is_own_warehouse(product, warehouse) else moves_sum
    bal = models.StockBalance.objects.create(warehouse=warehouse, product=product, qty=target)
    delta = q_qty(target - moves_sum)
    if delta != 0:
        models.StockMove.objects.create(
            document=None,
            warehouse=warehouse,
            product=product,
            qty_delta=delta,
            move_kind=MoveKind.RECEIPT if delta > 0 else MoveKind.EXPENSE,
            source_kind=SourceKind.OPENING,
            source_id=None,
        )
    return bal


def get_on_hand(*, warehouse, product, lock: bool = False) -> Decimal:
    """
    Остаток товара на складе по регистру.

    lock=True — строка регистра блокируется до конца транзакции (для проверки
    перед списанием); при отсутствии регистра он разово инициализируется
    (см. модульный docstring). Требует открытой транзакции.

    lock=False — только чтение, ничего не пишет. Если регистра нет, для своего
    склада возвращается значение карточки — ровно то, чем регистр будет
    инициализирован при первой операции.
    """
    if warehouse is None or product is None:
        return ZERO
    if lock:
        with transaction.atomic():
            return q_qty(Decimal(_ensure_balance(warehouse, product).qty or 0))
    bal = _balance_qs(warehouse, product).values_list("qty", flat=True).first()
    if bal is not None:
        return q_qty(Decimal(bal))
    if _is_own_warehouse(product, warehouse):
        return _card_qty_from_db(product, lock=False)
    return ZERO


def apply_stock_delta(
    *,
    warehouse,
    product,
    delta,
    move_kind=None,
    document=None,
    source_kind: str = SourceKind.DOCUMENT,
    source_id=None,
    allow_negative: bool = False,
) -> models.StockBalance:
    """
    Единственная точка изменения складского остатка.

    Блокирует регистр, проверяет отрицательный остаток (если не allow_negative),
    создаёт StockMove, сохраняет регистр и обновляет карточку товара для его
    собственного склада. Нулевая delta ничего не создаёт.
    """
    delta = q_qty(Decimal(delta or 0))
    with transaction.atomic():
        bal = _ensure_balance(warehouse, product)
        cur = q_qty(Decimal(bal.qty or 0))
        if delta == 0:
            bal.last_move = None
            return bal
        new_qty = q_qty(cur + delta)
        if new_qty < 0 and not allow_negative and delta < 0:
            raise InsufficientStock(product=product, warehouse=warehouse, available=cur, required=-delta)
        if move_kind is None:
            move_kind = MoveKind.RECEIPT if delta > 0 else MoveKind.EXPENSE
        move = models.StockMove.objects.create(
            document=document,
            warehouse=warehouse,
            product=product,
            qty_delta=delta,
            move_kind=move_kind,
            source_kind=source_kind,
            source_id=source_id,
        )
        bal.qty = new_qty
        bal.save(update_fields=["qty"])
        if _is_own_warehouse(product, warehouse):
            models.WarehouseProduct.objects.filter(pk=product.pk).update(quantity=new_qty)
            # экземпляр в памяти тоже актуализируем, чтобы случайный save() не вернул старое число
            product.quantity = new_qty
        bal.last_move = move
        return bal


# ---------------------------------------------------------------------------
# Сверка (reconcile_warehouse_stock, check_stock_consistency)
# ---------------------------------------------------------------------------

_DEC = DecimalField(max_digits=18, decimal_places=3)


def stock_rows(*, company_id=None, warehouse_id=None):
    """
    Строки сверки по товарам, привязанным к складу: карточка, регистр своего
    склада, Σ движений по своему складу, остаток у агентов, даты.
    Возвращает итератор dict.
    """
    bal_sq = models.StockBalance.objects.filter(
        product_id=OuterRef("pk"), warehouse_id=OuterRef("warehouse_id")
    ).values("qty")[:1]
    moves_sq = (
        models.StockMove.objects.filter(product_id=OuterRef("pk"), warehouse_id=OuterRef("warehouse_id"))
        .order_by()
        .values("product_id")
        .annotate(s=Sum("qty_delta"))
        .values("s")[:1]
    )
    last_move_sq = (
        models.StockMove.objects.filter(product_id=OuterRef("pk"), warehouse_id=OuterRef("warehouse_id"))
        .order_by()
        .values("product_id")
        .annotate(m=Max("created_at"))
        .values("m")[:1]
    )
    agent_sq = (
        models.AgentStockBalance.objects.filter(product_id=OuterRef("pk"))
        .order_by()
        .values("product_id")
        .annotate(s=Sum("qty"))
        .values("s")[:1]
    )
    qs = models.WarehouseProduct.objects.filter(warehouse__isnull=False)
    if company_id:
        qs = qs.filter(company_id=company_id)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    qs = (
        qs.annotate(
            balance_qty=Subquery(bal_sq, output_field=_DEC),
            moves_sum=Coalesce(Subquery(moves_sq, output_field=_DEC), Value(ZERO), output_field=_DEC),
            last_move_at=Subquery(last_move_sq),
            agent_qty=Coalesce(Subquery(agent_sq, output_field=_DEC), Value(ZERO), output_field=_DEC),
        )
        .order_by("company_id", "warehouse_id", "name", "id")
        .values(
            "id", "name", "company_id", "company__name", "warehouse_id", "warehouse__name",
            "quantity", "updated_date", "balance_qty", "moves_sum", "agent_qty", "last_move_at",
        )
    )
    for r in qs.iterator(chunk_size=2000):
        card = q_qty(Decimal(r["quantity"] or 0))
        bal = r["balance_qty"]
        bal = None if bal is None else q_qty(Decimal(bal))
        moves = q_qty(Decimal(r["moves_sum"] or 0))
        issues = []
        if bal is None:
            if card != 0 or moves != 0:
                issues.append("no_balance")
        else:
            if card != bal:
                issues.append("card_ne_balance")
            if bal != moves:
                issues.append("balance_ne_moves")
            if bal < 0:
                issues.append("negative_balance")
        yield {
            "company_id": r["company_id"],
            "company": r["company__name"],
            "warehouse_id": r["warehouse_id"],
            "warehouse": r["warehouse__name"],
            "product_id": r["id"],
            "product": r["name"],
            "card_qty": card,
            "balance_qty": bal,
            "moves_sum": moves,
            "agent_qty": q_qty(Decimal(r["agent_qty"] or 0)),
            "card_updated_at": r["updated_date"],
            "last_move_at": r["last_move_at"],
            "issues": issues,
        }


def foreign_balance_rows(*, company_id=None):
    """
    Записи регистра по «чужому» складу (product.warehouse != balance.warehouse),
    у которых регистр ≠ Σ движений. В норме таких нет; проверка для полноты.
    """
    moves_sq = (
        models.StockMove.objects.filter(product_id=OuterRef("product_id"), warehouse_id=OuterRef("warehouse_id"))
        .order_by()
        .values("product_id")
        .annotate(s=Sum("qty_delta"))
        .values("s")[:1]
    )
    qs = models.StockBalance.objects.exclude(product__warehouse_id=F("warehouse_id"))
    if company_id:
        qs = qs.filter(warehouse__company_id=company_id)
    qs = qs.annotate(
        moves_sum=Coalesce(Subquery(moves_sq, output_field=_DEC), Value(ZERO), output_field=_DEC)
    ).values("id", "warehouse_id", "product_id", "qty", "moves_sum")
    for r in qs.iterator(chunk_size=2000):
        if q_qty(Decimal(r["qty"] or 0)) != q_qty(Decimal(r["moves_sum"] or 0)):
            yield r


def init_opening_for_pair(*, warehouse, product) -> dict:
    """
    Шаги 2–3 сверки для одной пары (склад товара + товар), в своей транзакции:
    - регистра нет → создать StockBalance = карточка;
    - движение opening на разницу (регистр − Σ движений), чтобы инвариант выполнялся.
    Карточку не меняет. Идемпотентно. Возвращает {"created_balance", "opening_delta"}.
    """
    with transaction.atomic():
        existed = _balance_qs(warehouse, product).exists()
        bal = _ensure_balance(warehouse, product)
        moves_sum = q_qty(
            models.StockMove.objects.filter(warehouse_id=warehouse.pk, product_id=product.pk)
            .aggregate(s=Sum("qty_delta"))["s"] or ZERO
        )
        delta = q_qty(Decimal(bal.qty or 0) - moves_sum)
        if delta != 0:
            models.StockMove.objects.create(
                document=None,
                warehouse=warehouse,
                product=product,
                qty_delta=delta,
                move_kind=MoveKind.RECEIPT if delta > 0 else MoveKind.EXPENSE,
                source_kind=SourceKind.OPENING,
                source_id=None,
            )
        return {"created_balance": not existed, "opening_delta": delta}


# ---------------------------------------------------------------------------
# Корректировка остатка (stock-adjustment, начальный остаток при создании товара)
# ---------------------------------------------------------------------------

INITIAL_STOCK_COMMENT = "Начальный остаток при создании товара"


def post_inventory_adjustment(*, product, fact_qty, comment: str = "", user=None) -> dict:
    """
    Устанавливает фактический остаток товара на его складе: создаёт и проводит
    документ INVENTORY с одной строкой (qty = факт). Без денежного эффекта:
    для INVENTORY не создаются MoneyDocument / кассовый запрос / cashflow, цена строки 0.

    Возвращает {"document", "qty_before", "qty_after", "delta"}.
    ValueError — если документ не удалось провести (сообщение для 400).
    """
    from . import services

    warehouse = product.warehouse
    if warehouse is None:
        raise ValueError("Товар не привязан к складу.")
    fact = q_qty(Decimal(fact_qty))
    if fact < 0:
        raise ValueError("Фактический остаток не может быть отрицательным.")

    with transaction.atomic():
        qty_before = get_on_hand(warehouse=warehouse, product=product, lock=True)
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.INVENTORY,
            warehouse_from=warehouse,
            comment=(comment or "").strip() or "Корректировка остатка",
        )
        models.DocumentItem.objects.create(
            document=doc,
            product=product,
            qty=fact,
            price=Decimal("0.00"),
        )
        # allow_duplicate: повтор той же корректировки безвреден (delta станет 0).
        services.post_document(doc, allow_negative=False, user=user, allow_duplicate=True)
        doc.refresh_from_db()
        qty_after = get_on_hand(warehouse=warehouse, product=product, lock=True)
    return {
        "document": doc,
        "qty_before": qty_before,
        "qty_after": qty_after,
        "delta": q_qty(qty_after - qty_before),
    }
