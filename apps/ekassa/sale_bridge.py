"""
Отправка оплаченной продажи (main.Sale) в eKassa после commit транзакции.

Вызывается из Sale.mark_paid() через transaction.on_commit — после checkout_cart и mark_paid.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from apps.ekassa.client import EkassaHttpClient
from apps.ekassa.exceptions import EkassaAPIError
from apps.ekassa.services import get_integration

logger = logging.getLogger(__name__)


def _som_to_tyiyun_int(d: Decimal) -> int:
    return int((d * Decimal("100")).quantize(Decimal("1")))


def _merge_ekassa_meta(sale_id, patch: dict) -> None:
    from apps.main.models import Sale

    s = Sale.objects.filter(pk=sale_id).only("ekassa_fiscal").first()
    if not s:
        return
    m = dict(s.ekassa_fiscal or {})
    m.update(patch)
    Sale.objects.filter(pk=sale_id).update(ekassa_fiscal=m)


def _sale_item_to_good(item) -> dict:
    from apps.main.models import Product

    prod = item.product
    unit = "шт."
    calc = 0
    code = "0"
    if prod:
        unit = (prod.unit or "шт.")[:32]
        calc = 1 if prod.kind == Product.Kind.SERVICE else 0
        code = (prod.barcode or prod.code or str(prod.id).replace("-", ""))[:64] or "0"
    qty = Decimal(str(item.quantity or 0))
    line_total = item.line_total
    if qty and qty != 0:
        eff = (line_total / qty).quantize(Decimal("0.01"))
    else:
        eff = Decimal("0")
    price_ty = _som_to_tyiyun_int(eff)
    return {
        "calcItemAttributeCode": calc,
        "name": (item.name_snapshot or "Позиция")[:255],
        "sgtin": str(code),
        "price": price_ty,
        "quantity": float(qty),
        "unit": unit or "шт.",
        "st": 0,
        "vat": 0,
    }


def try_fiscalize_pos_sale(sale_id) -> None:
    from apps.main.models import Sale

    sale = (
        Sale.objects.filter(pk=sale_id)
        .select_related("company", "client")
        .prefetch_related("items__product")
        .first()
    )
    if not sale:
        return

    if sale.status != Sale.Status.PAID:
        return

    meta = sale.ekassa_fiscal or {}
    if meta.get("fd_number") is not None or meta.get("status") == "ok":
        return

    cfg = get_integration(sale.company)
    if cfg is None or not cfg.is_ready():
        return

    items = list(sale.items.all().order_by("id"))
    if not items:
        return

    goods = [_sale_item_to_good(it) for it in items]

    newid = meta.get("newid") or str(uuid.uuid4())
    _merge_ekassa_meta(sale_id, {"status": "pending", "newid": newid})

    body = {
        "fiscal_number": cfg.fiscal_number.strip(),
        "newid": newid,
        "operation": "INCOME",
        "cash": sale.payment_method == Sale.PaymentMethod.CASH,
        "goods": goods,
    }

    disc_ty = _som_to_tyiyun_int(Decimal(str(sale.discount_total or 0)))
    if disc_ty > 0:
        body["discount"] = str(disc_ty)

    if sale.payment_method == Sale.PaymentMethod.CASH and sale.cash_received:
        body["received"] = str(_som_to_tyiyun_int(Decimal(str(sale.cash_received))))

    client = sale.client
    if client is not None:
        email = (getattr(client, "email", None) or "").strip()
        if email:
            body["customerContact"] = email

    try:
        cli = EkassaHttpClient(cfg)
        resp = cli.request_json("POST", "/api/v2/receipt", json_body=body)
    except EkassaAPIError as e:
        logger.warning("eKassa receipt failed sale_id=%s: %s", sale_id, e, exc_info=False)
        err_payload = e.payload
        if isinstance(err_payload, dict) and len(str(err_payload)) > 8000:
            err_payload = {"detail": str(err_payload.get("message", ""))[:2000]}
        _merge_ekassa_meta(
            sale_id,
            {
                "status": "error",
                "newid": newid,
                "message": str(e),
                "ekassa_payload": err_payload,
            },
        )
        return
    except Exception as e:
        logger.exception("eKassa receipt unexpected error sale_id=%s", sale_id)
        _merge_ekassa_meta(
            sale_id,
            {"status": "error", "newid": newid, "message": str(e)},
        )
        return

    data = resp.get("data") or {}
    fields = data.get("fields") or {}
    fd = fields.get("1040")
    fd_int = None
    if fd is not None:
        try:
            fd_int = int(fd)
        except (TypeError, ValueError):
            fd_int = None

    _merge_ekassa_meta(
        sale_id,
        {
            "status": "ok",
            "newid": newid,
            "fd_number": fd_int,
            "ekassa_receipt_id": data.get("id"),
            "message": resp.get("message"),
        },
    )
