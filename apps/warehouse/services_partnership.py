"""
Партнёрство компаний (склад и касса): перемещение и инкассация с согласием партнёра,
разрыв и настройки сторон, журнал. См. stock-partnership.md (§6–§7).
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.warehouse import models, services, services_money

PS = models.CompanyStockPartnership
OP = models.PartnerOperationRequest
EV = models.CompanyStockPartnershipEvent


class PartnershipError(ValueError):
    """Бизнес-ошибка партнёрства → 400 {"detail": …}."""


# ---------------------------------------------------------------------------
# Остатки (та же логика, что в post_document для TRANSFER)
# ---------------------------------------------------------------------------


def available_qty(warehouse, product) -> Decimal:
    from . import stock as stock_service

    return Decimal(stock_service.get_on_hand(warehouse=warehouse, product=product))


def check_stock(warehouse_from, items):
    for it in items:
        product, qty = it["product"], Decimal(it["qty"])
        if product.warehouse_id != warehouse_from.id:
            raise PartnershipError(f"Товар «{product.name}» не со склада «{warehouse_from.name}».")
        cur = available_qty(warehouse_from, product)
        if cur < qty:
            label = product.article or product.name or f"ID {product.id}"
            raise PartnershipError(
                f"Недостаточно товара '{label}' на складе '{warehouse_from.name}'. Доступно: {cur}, требуется: {qty}"
            )


# ---------------------------------------------------------------------------
# Проведение
# ---------------------------------------------------------------------------


def execute_transfer(*, warehouse_from, warehouse_to, items, comment, created_by, initiator_company):
    """
    Межкомпанейский TRANSFER одной транзакцией (П5): документ, строки, проведение.
    Цена строки — закупочная цена товара-источника (D5), значение клиента не используется.
    Ошибка → откат, черновика не остаётся.
    """
    with transaction.atomic():
        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=warehouse_from,
            warehouse_to=warehouse_to,
            comment=comment or "",
            created_by=created_by,
            initiator_company=initiator_company,
        )
        for it in items:
            product = it["product"]
            item = models.DocumentItem(
                document=doc,
                product=product,
                qty=Decimal(it["qty"]),
                price=Decimal(product.purchase_price or 0).quantize(Decimal("0.01")),
                discount_percent=Decimal("0.00"),
                discount_amount=Decimal("0.00"),
            )
            try:
                item.clean()
            except Exception as e:  # django ValidationError
                raise PartnershipError(_err_text(e))
            item.save()
        try:
            services.post_document(doc)
        except Exception as e:
            raise PartnershipError(_err_text(e))
    return doc


def execute_incassation(*, cash_register_from, cash_register_to, amount, comment, created_by, operation=None):
    try:
        inc = services_money.post_partner_cash_incassation(
            cash_register_from=cash_register_from,
            cash_register_to=cash_register_to,
            amount=amount,
            comment=comment or "",
            created_by=created_by,
        )
    except Exception as e:
        raise PartnershipError(_err_text(e))
    if operation is not None:
        models.CompanyCashIncassation.objects.filter(pk=inc.pk).update(partner_operation=operation)
        inc.partner_operation = operation
    return inc


def _err_text(e) -> str:
    md = getattr(e, "message_dict", None)
    if md:
        return "; ".join(f"{', '.join(map(str, v))}" for v in md.values())
    msgs = getattr(e, "messages", None)
    if msgs:
        return "; ".join(map(str, msgs))
    return str(e)


# ---------------------------------------------------------------------------
# Запросы «забрать у партнёра»
# ---------------------------------------------------------------------------


def create_transfer_request(*, partnership, initiator_company, warehouse_from, warehouse_to, items, comment, user):
    check_stock(warehouse_from, items)  # заведомо невыполнимый запрос не создаём
    return OP.objects.create(
        partnership=partnership,
        kind=OP.Kind.TRANSFER,
        initiator_company=initiator_company,
        source_company_id=warehouse_from.company_id,
        warehouse_from=warehouse_from,
        warehouse_to=warehouse_to,
        items=[{"product": str(it["product"].id), "qty": str(Decimal(it["qty"]).quantize(Decimal("0.001")))} for it in items],
        comment=(comment or "")[:512],
        created_by=user,
    )


def create_incassation_request(*, partnership, initiator_company, cash_register_from, cash_register_to, amount,
                               comment, user):
    return OP.objects.create(
        partnership=partnership,
        kind=OP.Kind.INCASSATION,
        initiator_company=initiator_company,
        source_company_id=cash_register_from.company_id,
        cash_register_from=cash_register_from,
        cash_register_to=cash_register_to,
        amount=Decimal(amount).quantize(Decimal("0.01")),
        comment=(comment or "")[:512],
        created_by=user,
    )


def _operation_items(op):
    ids = [it["product"] for it in op.items or []]
    products = {str(p.id): p for p in models.WarehouseProduct.objects.filter(id__in=ids)}
    out = []
    for it in op.items or []:
        p = products.get(str(it["product"]))
        if p is None:
            raise PartnershipError("Товар из запроса больше не существует.")
        out.append({"product": p, "qty": Decimal(it["qty"])})
    return out


def approve_operation(op_id, *, user):
    """Проводит операцию. Бизнес-ошибка → PartnershipError, операция остаётся PENDING."""
    with transaction.atomic():
        op = OP.objects.select_for_update().select_related("partnership").get(pk=op_id)
        if op.status != OP.Status.PENDING:
            raise PartnershipError("Операция уже обработана.")
        if op.partnership.status != PS.Status.ACTIVE:
            raise PartnershipError("Партнёрство разорвано.")
        try:
            with transaction.atomic():  # ошибка проведения откатывает только проведение
                if op.kind == OP.Kind.TRANSFER:
                    doc = execute_transfer(
                        warehouse_from=op.warehouse_from,
                        warehouse_to=op.warehouse_to,
                        items=_operation_items(op),
                        comment=op.comment,
                        created_by=op.created_by,
                        initiator_company=op.initiator_company,
                    )
                    op.document = doc
                else:
                    inc = execute_incassation(
                        cash_register_from=op.cash_register_from,
                        cash_register_to=op.cash_register_to,
                        amount=op.amount,
                        comment=op.comment,
                        created_by=op.created_by,
                        operation=op,
                    )
                    op.incassation = inc
        except PartnershipError:
            raise
        op.status = OP.Status.APPROVED
        op.decided_by = user
        op.decided_at = timezone.now()
        op.error = ""
        op.save(update_fields=["status", "decided_by", "decided_at", "error", "document", "incassation", "updated_at"])
    return op


def decide_operation(op_id, *, user, status, reason=""):
    with transaction.atomic():
        op = OP.objects.select_for_update().get(pk=op_id)
        if op.status != OP.Status.PENDING:
            raise PartnershipError("Операция уже обработана.")
        op.status = status
        op.decided_by = user
        op.decided_at = timezone.now()
        op.reject_reason = (reason or "")[:512]
        op.save(update_fields=["status", "decided_by", "decided_at", "reject_reason", "updated_at"])
    return op


# ---------------------------------------------------------------------------
# Партнёрство: активация, разрыв, настройки
# ---------------------------------------------------------------------------


def activate_from_request(req, *, user):
    """Принятие заявки: новая пара или реактивация разорванной (флаги к значениям по умолчанию)."""
    id_lo, id_hi = models.canonical_company_pair_ids(req.from_company_id, req.to_company_id)
    p, created = PS.objects.select_for_update().get_or_create(
        company_a_id=id_lo, company_b_id=id_hi,
        defaults={"activated_at": timezone.now(), "created_from_request": req},
    )
    if not created:
        if p.status == PS.Status.ACTIVE:
            return p
        p.status = PS.Status.ACTIVE
        p.a_allows_direct_pull = p.b_allows_direct_pull = False
        p.a_shares_sales_history = p.b_shares_sales_history = True
        p.activated_at = timezone.now()
        p.created_from_request = req
        p.terminated_at = None
        p.terminated_by = None
        p.terminated_by_company = None
        p.save()
    EV.objects.create(partnership=p, kind=EV.Kind.ACTIVATED, company_id=req.to_company_id, user=user,
                      payload={"request": str(req.id)})
    return p


def terminate(partnership, *, company, user):
    with transaction.atomic():
        p = PS.objects.select_for_update().get(pk=partnership.pk)
        if p.status != PS.Status.ACTIVE:
            raise PartnershipError("Партнёрство уже разорвано.")
        p.status = PS.Status.TERMINATED
        p.terminated_at = timezone.now()
        p.terminated_by = user
        p.terminated_by_company = company
        p.save(update_fields=["status", "terminated_at", "terminated_by", "terminated_by_company"])
        cancelled = OP.objects.filter(partnership=p, status=OP.Status.PENDING).update(
            status=OP.Status.CANCELLED, reject_reason="Партнёрство разорвано", decided_by=user,
            decided_at=timezone.now(), updated_at=timezone.now(),
        )
        EV.objects.create(partnership=p, kind=EV.Kind.TERMINATED, company=company, user=user,
                          payload={"cancelled_operations": cancelled})
    return p, cancelled


SETTINGS_FIELDS = ("allow_direct_pull", "share_sales_history")
_FLAG = {"allow_direct_pull": "allows_direct_pull", "share_sales_history": "shares_sales_history"}


def change_settings(partnership, *, company, user, changes: dict):
    with transaction.atomic():
        p = PS.objects.select_for_update().get(pk=partnership.pk)
        changed = {}
        for name, value in changes.items():
            attr = _FLAG[name]
            current = getattr(p, f"{p._side(company.id)}_{attr}")
            if current != bool(value):
                p.set_side_flag(company.id, attr, value)
                changed[name] = bool(value)
        if changed:
            p.save()
            EV.objects.create(partnership=p, kind=EV.Kind.SETTINGS_CHANGED, company=company, user=user, payload=changed)
    return p


def partner_row(p, company):
    """Элемент списка активных партнёров (§7.2) для компании company."""
    partner = p.company_b if str(p.company_a_id) == str(company.id) else p.company_a
    return {
        "id": str(partner.id),
        "name": partner.name,
        "partnership_id": str(p.id),
        "since": (p.activated_at or p.created_at).isoformat(),
        "allow_direct_pull": p.allows_direct_pull_from(company.id),
        "partner_allows_direct_pull": p.allows_direct_pull_from(partner.id),
        "share_sales_history": p.shares_sales_history_of(company.id),
        "partner_shares_sales_history": p.shares_sales_history_of(partner.id),
        "pending_operations_in": OP.objects.filter(
            partnership=p, source_company=company, status=OP.Status.PENDING
        ).count(),
    }
