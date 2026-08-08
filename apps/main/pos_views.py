from rest_framework import generics, status, permissions, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime, parse_date
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.pagination import _positive_int

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from django.http import FileResponse
from django.http import Http404
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import timedelta, datetime, date, time as dtime
import io, os, uuid, logging

from django.db.models import Q, F, Value as V, Sum, Prefetch, Count
from django.db.models.functions import Coalesce
from django.utils.timezone import is_aware, make_aware, get_current_timezone

from typing import List, Optional, Dict
from dataclasses import dataclass

from django.core.cache import cache
from django.conf import settings
import requests
import qrcode

from apps.users.models import (
    Roles,
    User,
    Company,
    SCALE_BARCODE_MODE_AUTO,
    SCALE_BARCODE_MODE_WEIGHT,
    SCALE_BARCODE_MODE_AMOUNT,
    SCALE_BARCODE_LAYOUT_PLU,
    SCALE_BARCODE_LAYOUT_CODE,
    SCALE_BARCODE_AMOUNT_UNIT_TIYIN,
    SCALE_BARCODE_AMOUNT_UNIT_SOM,
)
from apps.main.models import (
    Cart,
    CartItem,
    CartItemDeletionLog,
    Sale,
    SaleItem,
    SalePayment,
    Product,
    ProductPackage,
    ProductPromotionTier,
    ProductAlternateBarcode,
    MobileScannerToken,
    Client,
    ProductImage,
)
from apps.main.models import ManufactureSubreal, AgentSaleAllocation, ReturnFromAgent
from apps.main.cache_utils import invalidate_cache_pattern
from apps.main.services import checkout_cart, NotEnoughStock
from apps.main.cart_service import abandon_cart
from apps.ekassa.runtime import schedule_after_commit
from apps.ekassa.shift_bridge import sync_ekassa_after_local_shift_open_by_id


def _ekassa_checkout_hint(company):
    """Если у компании включена eKassa — клиенту можно показать, что чек уходит в ОФД."""
    from apps.ekassa.services import get_integration

    cfg = get_integration(company)
    if cfg and cfg.is_ready():
        return {"queued": True}
    return None
from apps.main.services_agent_pos import checkout_agent_cart, AgentNotEnoughStock
from apps.main.utils_numbers import ensure_sale_doc_number
from apps.main.views import CompanyBranchRestrictedMixin, SupplierReceiptLimitPagination
from apps.construction.models import Cashbox, CashShift
from .pos_utils import (
    money,
    qty3,
    q2,
    fmt_money,
    fmt,
    to_decimal,
    as_decimal,
    _q2,
    Q2,
    Q3,
    line_qty_consume_units,
    default_unit_price_for_package,
    total_cart_consume_packs_for_product,
    log_cart_item_deletion,
)

from .pos_serializers import (
    SaleCartSerializer,
    SaleItemSerializer,
    ScanRequestSerializer,
    AddItemSerializer,
    CartItemPatchSerializer,
    CheckoutSerializer,
    PayDebtSerializer,
    MobileScannerTokenSerializer,
    SaleListSerializer,
    SaleDetailSerializer,
    StartCartOptionsSerializer,
    CustomCartItemCreateSerializer,
    SaleStatusUpdateSerializer,
    ReceiptSerializer,
    AgentCheckoutSerializer,
    _is_owner_like,
    CartItemDeletionLogSerializer,
)

try:
    from apps.main.models import ClientDeal, DealInstallment
except Exception:
    ClientDeal = None
    DealInstallment = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

try:
    pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(FONTS_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")))
except Exception:
    # если шрифтов нет в окружении — PDF всё равно сгенерится на Helvetica
    pass


def register_fonts_if_needed():
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))
    except Exception:
        pass


register_fonts_if_needed()

class MarketCashierOnlyMixin:
    """
    Ограничение POS/кассирского функционала:
    - проверка доступа выполняется на фронте
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        return


def _set_font(p, name: str, size: int, fallback: str = "Helvetica"):
    """
    Безопасно ставит шрифт. Если кастомного нет — падаем на fallback,
    но PDF продолжаем генерировать.
    """
    try:
        p.setFont(name, size)
    except Exception:
        p.setFont(fallback, size)


# Алиасы для обратной совместимости (используются в коде)
_to_decimal = to_decimal
_as_decimal = as_decimal


def _is_market_company(company: Company) -> bool:
    try:
        return bool(company and getattr(company, "is_market", None) and company.is_market())
    except Exception:
        return False


def _build_physical_receipt_text(sale, *, payment_method=None, cash_received=None, change=None, include_shift=True) -> str:
    from apps.main.receipt_header import receipt_vendor_header

    ensure_sale_doc_number(sale)

    created_at = timezone.localtime(sale.created_at) if getattr(sale, "created_at", None) else timezone.localtime()
    vh = receipt_vendor_header(sale)
    company_name = vh.get("brand") or "Компания"
    cashier_name = ""
    if getattr(sale, "user", None):
        cashier_name = (
            getattr(sale.user, "get_full_name", lambda: "")()
            or getattr(sale.user, "full_name", None)
            or getattr(sale.user, "username", None)
            or ""
        )

    payment_method_value = payment_method or getattr(sale, "payment_method", None)
    payment_label = None
    try:
        payment_label = sale.get_payment_method_display()
    except Exception:
        payment_label = payment_method_value or ""

    cash_received_value = getattr(sale, "cash_received", None)
    if cash_received_value in (None, ""):
        cash_received_value = cash_received if cash_received is not None else Decimal("0.00")

    change_value = getattr(sale, "change", None)
    if change_value in (None, ""):
        change_value = change if change is not None else Decimal("0.00")

    lines = [company_name]
    if vh.get("inn"):
        lines.append(f"ИНН {vh['inn']}")
    if vh.get("address"):
        lines.append(vh["address"])
    lines.extend(
        [
            f"Чек № {getattr(sale, 'doc_no', None) or sale.id}",
            created_at.strftime("%d.%m.%Y %H:%M"),
        ]
    )
    if cashier_name:
        lines.append(f"Кассир: {cashier_name}")
    if include_shift and getattr(sale, "shift_id", None):
        lines.append(f"Смена: {sale.shift_id}")
    if getattr(sale, "cashbox_id", None):
        lines.append(f"Касса: {sale.cashbox_id}")
    if getattr(sale, "client", None):
        client_name = (
            getattr(sale.client, "full_name", None)
            or getattr(sale.client, "llc", None)
            or getattr(sale.client, "enterprise", None)
            or ""
        )
        if client_name:
            lines.append(f"Клиент: {client_name}")

    lines.extend([
        "-" * 32,
        "Товары",
    ])

    for idx, it in enumerate(sale.items.all(), start=1):
        item_name = (getattr(it, "name_snapshot", None) or getattr(it, "custom_name", None) or "Товар").strip()
        qty = fmt(getattr(it, "quantity", 0))
        unit_price = fmt_money(getattr(it, "unit_price", 0))
        line_base = (getattr(it, "unit_price", 0) or 0) * (getattr(it, "quantity", 0) or 0)
        line_disc = getattr(it, "line_discount", None) or 0
        line_total = fmt_money(line_base - line_disc)
        lines.append(f"{idx}. {item_name}")
        if Decimal(str(line_disc or 0)) > 0:
            lines.append(f"   Скидка: {fmt_money(line_disc)}")
        lines.append(f"   {qty} x {unit_price} = {line_total}")

    lines.extend([
        "-" * 32,
        f"Сумма: {fmt_money(sale.subtotal)}",
    ])
    if sale.discount_total and sale.discount_total > 0:
        lines.append(f"Скидка: {fmt_money(sale.discount_total)}")
    if sale.tax_total and sale.tax_total > 0:
        lines.append(f"Налог: {fmt_money(sale.tax_total)}")
    lines.append(f"Итого: {fmt_money(sale.total)}")

    payment_lines = sale.payment_lines() if hasattr(sale, "payment_lines") else []
    if payment_lines:
        if len(payment_lines) > 1:
            lines.append("Оплата:")
            for line in payment_lines:
                try:
                    label = line.get_method_display()
                except Exception:
                    label = line.method
                lines.append(f"  {label}: {fmt_money(line.amount)}")
        else:
            line = payment_lines[0]
            try:
                payment_label = line.get_method_display()
            except Exception:
                payment_label = line.method
            if line.method == Sale.PaymentMethod.CASH:
                lines.append("Оплата: Наличные")
            elif line.method == Sale.PaymentMethod.DEBT:
                lines.append("Оплата: В долг")
            else:
                lines.append(f"Оплата: {payment_label}")

        cash_portion = sale.cash_payment_amount() if hasattr(sale, "cash_payment_amount") else Decimal("0.00")
        if cash_portion > 0:
            cash_received_value = getattr(sale, "cash_received", None)
            if cash_received_value in (None, ""):
                cash_received_value = cash_received if cash_received is not None else cash_portion
            change_value = getattr(sale, "change", None)
            if change_value in (None, ""):
                change_value = change if change is not None else Decimal("0.00")
            lines.append(f"Получено наличными: {fmt_money(cash_received_value)}")
            if Decimal(str(change_value or 0)) > 0:
                lines.append(f"Сдача: {fmt_money(change_value)}")
    elif payment_method_value == Sale.PaymentMethod.CASH:
        lines.append("Оплата: Наличные")
        lines.append(f"Получено: {fmt_money(cash_received_value)}")
        lines.append(f"Сдача: {fmt_money(change_value)}")
    elif payment_method_value == Sale.PaymentMethod.DEBT:
        lines.append("Оплата: В долг")
    elif payment_label:
        lines.append(f"Оплата: {payment_label}")

    lines.append("-" * 32)
    lines.append("Спасибо за покупку")

    # eKassa (если фискализация уже выполнена и данные сохранены в Sale.ekassa_fiscal)
    try:
        meta = getattr(sale, "ekassa_fiscal", None) or {}
    except Exception:
        meta = {}
    if isinstance(meta, dict) and meta:
        lines.append("-" * 32)
        lines.append("eKassa")
        status = meta.get("status")
        if status is not None:
            lines.append(f"status: {status}")
        # Явные реквизиты (как в «Дубликат»)
        kkm_reg = meta.get("kkm_reg_number")
        fm_number = meta.get("fm_number")
        fd_number = meta.get("fd_number") if meta.get("fd_number") is not None else meta.get("fields", {}).get("1040")
        fpd = meta.get("fpd")
        if kkm_reg:
            lines.append(f"РН ККМ\t{kkm_reg}")
        if fm_number:
            lines.append(f"ФМ\t{fm_number}")
        if fd_number is not None:
            lines.append(f"ФД\t{fd_number}")
        if fpd:
            lines.append(f"ФПД\t{fpd}")
        if meta.get("ekassa_receipt_id") is not None:
            lines.append(f"receipt_id: {meta.get('ekassa_receipt_id')}")
        # Данные для QR (ссылка из ответа eKassa)
        link = meta.get("link")
        if link:
            lines.append(f"QR\t{link}")
        fields = meta.get("fields")
        if isinstance(fields, dict) and fields:
            lines.append("fields:")
            for k in sorted(fields.keys(), key=lambda x: str(x)):
                v = fields.get(k)
                if v is None or v == "":
                    continue
                lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _truthy_query_param(val) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _cart_queryset_for_response():
    image_qs = ProductImage.objects.only("id", "product_id", "image", "alt", "is_primary").order_by("id")
    promo_tier_qs = ProductPromotionTier.objects.only(
        "id", "product_id", "position", "min_amount", "discount_percent", "promo_quantity"
    ).order_by("position", "id")
    item_qs = (
        CartItem.objects.select_related("product")
        .only(
            "id",
            "cart_id",
            "product_id",
            "custom_name",
            "quantity",
            "unit_price",
            "line_discount",
            "sale_package_id",
            "product__id",
            "product__name",
            "product__barcode",
            "product__stock",
            "product__is_weight",
        )
        .prefetch_related(
            Prefetch("product__images", queryset=image_qs),
            Prefetch("product__promotion_tiers", queryset=promo_tier_qs),
        )
        .order_by("id")
    )
    return Cart.objects.select_related("shift").only(
        "id",
        "company_id",
        "status",
        "is_wholesale",
        "is_default",
        "shift_id",
        "subtotal",
        "discount_total",
        "order_discount_total",
        "order_discount_percent",
        "tax_total",
        "total",
    ).prefetch_related(
        Prefetch("items", queryset=item_qs)
    )


MAX_OPEN_CARTS_PER_SHIFT = 10


def _normalize_pos_request_data(data):
    """Фронт может прислать camelCase (isNew, saleId)."""
    if not hasattr(data, "get"):
        return data
    out = data.copy() if hasattr(data, "copy") else dict(data)
    if "isNew" in out and "is_new" not in out:
        out["is_new"] = out["isNew"]
    if "saleId" in out and "sale_id" not in out:
        out["sale_id"] = out["saleId"]
    if "isWholesale" in out and "is_wholesale" not in out:
        out["is_wholesale"] = out["isWholesale"]
    if "orderDiscountTotal" in out and "order_discount_total" not in out:
        out["order_discount_total"] = out["orderDiscountTotal"]
    if "orderDiscountPercent" in out and "order_discount_percent" not in out:
        out["order_discount_percent"] = out["orderDiscountPercent"]
    return out


def _pos_body_has_explicit_field(request, *field_names):
    """Поле реально передано в теле запроса (не дефолт сериализатора)."""
    raw = _normalize_pos_request_data(getattr(request, "data", {}) or {})
    if not hasattr(raw, "get"):
        return False
    for name in field_names:
        if name in raw and raw.get(name) not in (None, "", "null"):
            return True
    return False


def _shift_carts_base_qs(company, user, shift):
    qs = Cart.objects.filter(
        company=company,
        shift=shift,
        status=Cart.Status.ACTIVE,
    )
    if user:
        qs = qs.filter(Q(user=user) | Q(shift__cashier=user))
    return qs


def _shift_active_carts_qs(company, user, shift):
    """Список вкладок корзин (с items_count). Без select_for_update — несовместимо с GROUP BY в PostgreSQL."""
    return (
        _shift_carts_base_qs(company, user, shift)
        .annotate(items_count=Count("items"))
        .order_by("created_at")
    )



def _pick_shift_cart_id(*, company, user, shift, sale_id=None):
    base = _shift_carts_base_qs(company, user, shift)
    if sale_id:
        return sale_id if base.filter(id=sale_id).exists() else None
    cart_id = (
        base.filter(is_default=True)
        .order_by("-updated_at")
        .values_list("id", flat=True)
        .first()
    )
    if cart_id:
        return cart_id
    return base.order_by("-updated_at").values_list("id", flat=True).first()


def _lock_single_shift_cart(*, company, user, shift, cart_id):
    if not cart_id:
        return None
    return (
        _shift_carts_base_qs(company, user, shift)
        .select_for_update(of=("self",))
        .filter(id=cart_id)
        .first()
    )


def _find_locked_shift_cart(*, company, user, shift, sale_id=None):
    """FOR UPDATE только одной корзины — не блокируем всю смену."""
    cart_id = _pick_shift_cart_id(
        company=company,
        user=user,
        shift=shift,
        sale_id=sale_id,
    )
    return _lock_single_shift_cart(
        company=company,
        user=user,
        shift=shift,
        cart_id=cart_id,
    )


def _get_pos_open_cart_for_cashier(*, company, user, cart_id):
    """Open-корзина кассира по id (GET вкладки / переключение)."""
    return (
        Cart.objects.filter(
            id=cart_id,
            company=company,
            status=Cart.Status.ACTIVE,
            shift__status=CashShift.Status.OPEN,
        )
        .filter(Q(user=user) | Q(shift__cashier=user))
        .select_related("shift")
        .first()
    )


def _abandon_pos_open_cart(*, company, user, cart):
    """Закрыть open-корзину (вкладку кассы) — status=abandoned, не DELETE Sale."""
    shift = cart.shift
    with transaction.atomic():
        cart = (
            Cart.objects.select_for_update(of=("self",))
            .select_related("shift")
            .get(id=cart.id, company=company, status=Cart.Status.ACTIVE)
        )
        was_default = bool(cart.is_default)
        abandon_cart(cart=cart)
        if was_default:
            new_default_id = (
                _shift_carts_base_qs(company, user, shift)
                .order_by("created_at")
                .values_list("id", flat=True)
                .first()
            )
            if new_default_id:
                Cart.objects.filter(id=new_default_id).update(
                    is_default=True,
                    updated_at=timezone.now(),
                )
    return shift


def _pos_cart_tab_label(cart, ordered_carts):
    if getattr(cart, "is_default", False):
        return "Основная"
    for idx, c in enumerate(ordered_carts):
        if c.id == cart.id:
            return f"Корзина {idx + 1}"
    return "Корзина"


def _serialize_pos_cart_tab(cart, ordered_carts):
    return {
        "id": str(cart.id),
        "is_default": bool(getattr(cart, "is_default", False)),
        "label": _pos_cart_tab_label(cart, ordered_carts),
        "items_count": int(getattr(cart, "items_count", 0) or 0),
        "total": fmt_money(cart.total),
        "status": "open",
    }


def _serialize_pos_sale(request, cart):
    data = SaleCartSerializer(cart, context={"request": request}).data
    if data.get("id") is not None:
        data["id"] = str(data["id"])
    for field in (
        "subtotal",
        "discount_total",
        "order_discount_total",
        "tax_total",
        "total",
    ):
        if field in data and data[field] is not None:
            data[field] = fmt_money(data[field])
    if data.get("order_discount_percent") is not None:
        data["order_discount_percent"] = f"{money(data['order_discount_percent']):.2f}"
    if data.get("shift"):
        data["shift"] = str(data["shift"])
    return data


def _pos_multi_cart_response(request, active_cart, *, status_code=status.HTTP_200_OK):
    shift = active_cart.shift
    user = active_cart.user or request.user
    company = active_cart.company
    carts_qs = _shift_active_carts_qs(company, user, shift)
    ordered = list(carts_qs)
    active_id = str(active_cart.id)
    return Response(
        {
            "sale": _serialize_pos_sale(request, active_cart),
            "active_sale_id": active_id,
            # Старый фронт читал id корзины из корня ответа start/scan/GET.
            "id": active_id,
            "carts": [_serialize_pos_cart_tab(c, ordered) for c in ordered],
        },
        status=status_code,
    )


def _resolve_pos_target_cart(*, company, user, shift, sale_id=None, for_update=False):
    if for_update:
        return _lock_pos_target_cart(company=company, user=user, shift=shift, sale_id=sale_id)

    qs = _shift_active_carts_qs(company, user, shift)

    if sale_id:
        cart = qs.filter(id=sale_id).first()
        if not cart:
            raise ValidationError({"sale_id": "Открытая корзина не найдена в этой смене."})
        return cart

    target = qs.filter(is_default=True).first() or qs.first()
    if not target:
        raise ValidationError({"detail": "Нет открытых корзин в смене."})
    return target


def _cart_response(request, cart_id, *, status_code=status.HTTP_200_OK, multi_cart=False):
    cart = get_object_or_404(
        _cart_queryset_for_response(),
        id=cart_id,
        company=request.user.company,
    )
    if multi_cart and cart.shift_id:
        return _pos_multi_cart_response(request, cart, status_code=status_code)
    return Response(
        SaleCartSerializer(cart, context={"request": request}).data,
        status=status_code,
    )


def _upsert_scanned_cart_item(cart, product, quantity):
    scanned_qty = qty3(quantity)
    item = (
        CartItem.objects.select_for_update()
        .filter(cart=cart, product=product, sale_package__isnull=True)
        .first()
    )
    if item:
        item.quantity = qty3(item.quantity + scanned_qty)
        item.save(update_fields=["quantity"], skip_full_clean=True)
        return item

    item = CartItem(
        cart=cart,
        company=cart.company,
        branch=getattr(cart, "branch", None),
        product=product,
        quantity=scanned_qty,
        unit_price=(
            (product.wholesale_price or product.price)
            if getattr(cart, "is_wholesale", False)
            else product.price
        ),
    )
    item.save(skip_full_clean=True)
    return item


def _reprice_cart_items_for_mode(cart: Cart) -> None:
    items = list(cart.items.select_related("product", "sale_package"))
    if not items:
        return
    changed = []
    for it in items:
        if not it.product_id or not it.product:
            continue
        p = it.product
        if getattr(cart, "is_wholesale", False):
            raw_wholesale = getattr(p, "wholesale_price", None)
            raw_retail = getattr(p, "price", None)
            pack_price = Decimal(str(raw_wholesale)) if raw_wholesale not in (None, 0, "0") else Decimal(str(raw_retail or 0))
            if it.sale_package_id:
                ipp = Decimal(str(it.sale_package.quantity_in_package or 0))
                it.unit_price = _q2(pack_price / ipp) if ipp > 0 else _q2(pack_price)
            else:
                it.unit_price = _q2(pack_price)
        else:
            if it.sale_package_id:
                it.unit_price = _q2(default_unit_price_for_package(p, it.sale_package))
            else:
                it.unit_price = _q2(Decimal(str(getattr(p, "price", None) or 0)))
        changed.append(it)
    if changed:
        CartItem.objects.bulk_update(changed, ["unit_price"])
        cart.recalc()


def _aware(dt_or_date, end=False):
    tz = get_current_timezone()
    if isinstance(dt_or_date, datetime):
        return dt_or_date if is_aware(dt_or_date) else make_aware(dt_or_date, tz)
    if isinstance(dt_or_date, date):
        t = dtime(23, 59, 59) if end else dtime(0, 0, 0)
        return make_aware(datetime.combine(dt_or_date, t), tz)
    return None


def _parse_range_dt(v: str, *, end: bool) -> Optional[datetime]:
    """
    start/end из query_params.
    Поддерживает YYYY-MM-DD и ISO datetime.
    Всегда возвращает aware datetime или None.
    """
    if not v:
        return None
    dt = parse_datetime(v)
    if dt:
        return _aware(dt, end=end)
    d = parse_date(v)
    if d:
        return _aware(d, end=end)
    return None


def _safe(v) -> str:
    return v if (v is not None and str(v).strip()) else "—"


# qty3 импортирован из pos_utils


@dataclass
class Entry:
    date: datetime
    desc: str
    debit: Decimal
    credit: Decimal


def _party_lines(
    title,
    name,
    inn=None,
    okpo=None,
    score=None,
    bik=None,
    addr=None,
    phone=None,
    email=None,
):
    return [
        title,
        name,
        f"ИНН: {_safe(inn)}   ОКПО: {_safe(okpo)}",
        f"Р/с: {_safe(score)}   БИК: {_safe(bik)}",
        f"Адрес: {_safe(addr)}",
        f"Тел.: {_safe(phone)}",
    ]


# Префиксы EAN-13 весов ШТРИХ-ПРИНТ (см. «Префикс штрих-кода» в драйвере весов):
#   весовой товар  -> в поле EEEEE зашит ВЕС (граммы): 00214 = 0.214 кг
#   итоговый       -> в поле EEEEE зашита СТОИМОСТЬ в сомах: 00044 = 44 сом
# По умолчанию весовой = 20, итоговый = 25 (заводская настройка ШТРИХ).
# Если на весах префиксы перенастроены — поправьте эти множества.
SCALE_WEIGHT_PREFIXES = {"20"}
SCALE_AMOUNT_PREFIXES = {"25"}


def _company_scale_barcode_settings(company_id):
    """(mode, layout, amount_unit) чтения штрихкода весов для компании."""
    row = (
        Company.objects.filter(id=company_id)
        .values("scale_barcode_mode", "scale_barcode_layout", "scale_barcode_amount_unit")
        .first()
    ) or {}
    mode = row.get("scale_barcode_mode") or SCALE_BARCODE_MODE_AUTO
    layout = row.get("scale_barcode_layout") or SCALE_BARCODE_LAYOUT_PLU
    amount_unit = row.get("scale_barcode_amount_unit") or SCALE_BARCODE_AMOUNT_UNIT_TIYIN
    return mode, layout, amount_unit


def _company_scale_barcode_mode(company_id) -> str:
    """Режим чтения штрихкода весов (обратная совместимость)."""
    return _company_scale_barcode_settings(company_id)[0]


def _parse_scale_barcode(barcode: str, mode: str = SCALE_BARCODE_MODE_AUTO,
                         layout: str = SCALE_BARCODE_LAYOUT_PLU,
                         amount_unit: str = SCALE_BARCODE_AMOUNT_UNIT_TIYIN):
    """
    EAN-13 штрихкод весов. Раскладка полей задаётся `layout`:

    - "plu"  (по умолчанию): FF PPPPP EEEEE C
        FF (20–29) префикс, PPPPP PLU (5 цифр), EEEEE вес/сумма (5 цифр), C — чек.
        Товар ищется по Product.plu.
    - "code" : FF PPPPPP EEEE C
        FF префикс, PPPPPP PLU (6 цифр — в экспорте «Код»=PLU), EEEE вес/сумма
        (4 цифры), C — чек. Товар ищется по Product.plu.

    Трактовка поля значения задаётся `mode` (Company.scale_barcode_mode):
    weight | amount | auto (по префиксу). Единица суммы — `amount_unit`
    (Company.scale_barcode_amount_unit): tiyin (÷100) | som (целые сомы).
    """
    if not barcode or len(barcode) != 13 or not barcode.isdigit():
        return None

    prefix = barcode[0:2]
    try:
        prefix_num = int(prefix)
    except ValueError:
        return None
    if not (20 <= prefix_num <= 29):
        return None

    check_digit = barcode[12]

    # Границы полей: «по коду» — PLU(6)+значение(4), «по PLU» — PLU(5)+значение(5).
    if layout == SCALE_BARCODE_LAYOUT_CODE:
        raw_code, value_raw = barcode[2:8], barcode[8:12]
    else:
        raw_code, value_raw = barcode[2:7], barcode[7:12]

    try:
        plu = int(raw_code)
    except ValueError:
        return None

    if mode == SCALE_BARCODE_MODE_WEIGHT:
        as_weight = True
    elif mode == SCALE_BARCODE_MODE_AMOUNT:
        as_weight = False
    else:  # auto — по префиксу
        as_weight = prefix in SCALE_WEIGHT_PREFIXES

    # Весовой штрихкод: в поле зашит ВЕС (граммы), а не сумма.
    if as_weight:
        try:
            weight_raw = int(value_raw)
        except ValueError:
            return None
        return {
            "prefix": prefix,
            "plu": plu,
            # Раскладка «по коду» исторически отдавала код без ведущих нулей.
            "raw_code": str(plu) if layout == SCALE_BARCODE_LAYOUT_CODE else raw_code,
            "weight_raw": weight_raw,
            "weight_kg": Decimal(weight_raw) / Decimal(1000),
            "check_digit": check_digit,
            "mode": "weight",
        }

    # Итоговый/прочие префиксы: в поле зашита СТОИМОСТЬ. Единица зависит от весов:
    #   tiyin — сумма в тыйынах: на этикетке 38.00 сом → поле «03800» (делим на 100);
    #   som   — сумма целыми сомами: на этикетке 36 сом → поле «00036» (как есть).
    divisor = Decimal(1) if amount_unit == SCALE_BARCODE_AMOUNT_UNIT_SOM else Decimal(100)
    amount = (Decimal(value_raw) / divisor).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "prefix": prefix,
        "plu": plu,
        "raw_code": str(plu) if layout == SCALE_BARCODE_LAYOUT_CODE else raw_code,
        "amount_raw": value_raw,
        "amount": amount,
        "amount_unit": amount_unit,
        "check_digit": check_digit,
        "mode": "amount_plain",
    }


def _scale_barcode_variants(barcode: str, mode: str, layout: str,
                            amount_unit: str = SCALE_BARCODE_AMOUNT_UNIT_TIYIN) -> list[dict]:
    """
    Разборы весового штрихкода: сначала раскладка из настроек компании, затем запасная.

    Раскладки дают РАЗНЫЙ PLU для одного и того же кода:
        2 00453 00054 9  →  layout=plu → PLU 453 (вес 54 г)
                            layout=code → PLU 4530 (вес 54 г)
    Если в `Company.scale_barcode_layout` указана не та раскладка, которую реально
    печатают весы, товар не находится («Товар с PLU 4530 … не найден»). Поэтому после
    промаха по основной раскладке пробуем альтернативную.

    Обе раскладки трактуют поле значения одинаково (вес или сумма — по `mode`),
    поэтому запасной разбор безопасен и для суммового штрихкода.
    """
    primary = _parse_scale_barcode(barcode, mode, layout, amount_unit)
    if not primary:
        return []

    variants = [primary]
    alt_layout = (
        SCALE_BARCODE_LAYOUT_PLU
        if layout == SCALE_BARCODE_LAYOUT_CODE
        else SCALE_BARCODE_LAYOUT_CODE
    )

    alt = _parse_scale_barcode(barcode, mode, alt_layout, amount_unit)
    if alt and alt.get("plu") != primary.get("plu"):
        variants.append(alt)
    return variants


def _finalize_scale_data_for_product(product, scale_data: dict) -> Optional[str]:
    """
    Для mode=amount_plain дополняет scale_data полем quantity_kg.
    Возвращает текст ошибки или None.
    """
    if not scale_data or scale_data.get("mode") != "amount_plain":
        return None

    price_raw = getattr(product, "price", None)
    if price_raw is None or price_raw == "":
        return "Невозможно рассчитать вес: у товара не указана цена"

    price = Decimal(str(price_raw))
    if price <= 0:
        return "Невозможно рассчитать вес: у товара не указана цена"

    amount = scale_data.get("amount")
    if amount is None:
        return "Невозможно рассчитать вес: в штрихкоде не указана сумма"

    quantity_kg = (Decimal(str(amount)) / price).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    scale_data["quantity_kg"] = quantity_kg
    scale_data["plu"] = scale_data.get("plu")
    scale_data["amount"] = Decimal(str(amount))
    scale_data["mode"] = "amount_plain"
    return None


def _effective_qty_from_scale_data(scale_data, qty) -> Decimal:
    if scale_data:
        if "quantity_kg" in scale_data:
            return Decimal(str(scale_data["quantity_kg"]))
        if "weight_kg" in scale_data:
            return Decimal(str(scale_data["weight_kg"]))
    return Decimal(str(qty))


def _parse_scale_barcode_loose(barcode: str):
    """
    Как на складе (warehouse): любой 13-значный EAN → PLU + вес.
    Используется только если прямой поиск по штрихкоду не дал результат.
    """
    if not barcode or len(barcode) != 13 or not barcode.isdigit():
        return None

    raw_code = barcode[2:7]
    weight_digits = barcode[7:12]

    try:
        plu = int(raw_code)
        weight_raw = int(weight_digits)
    except ValueError:
        return None

    return {
        "prefix": barcode[0:2],
        "plu": plu,
        "raw_code": raw_code,
        "weight_raw": weight_raw,
        "weight_kg": weight_raw / 1000.0,
    }


def _pos_barcode_lookup_candidates(barcode: str):
    """Варианты штрихкода для поиска (ведущие нули, EAN-13 padding)."""
    raw = (barcode or "").strip()
    if not raw:
        return []

    candidates = []

    def _add(value):
        value = (value or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    _add(raw)
    if raw.isdigit():
        _add(raw.lstrip("0") or "0")
        if len(raw) < 13:
            _add(raw.zfill(13))
        if len(raw) == 13 and raw.startswith("0"):
            _add(raw[1:])
        if len(raw) == 14 and raw.startswith("0"):
            _add(raw[1:])
    return candidates


POS_SCAN_PRODUCT_FIELDS = (
    "id",
    "company_id",
    "price",
    "barcode",
    "plu",
    "code",
    "is_weight",
)


def _product_pk_from_cache(value):
    """Поддержка legacy-кэша: раньше хранили ORM-объект, теперь — UUID строкой."""
    if value is None:
        return None
    if isinstance(value, Product):
        return value.pk
    return value


pos_scan_logger = logging.getLogger("crm.pos.scan")


class AmbiguousBarcode(Exception):
    """Штрихкод (с учётом нормализации/альт-кодов) найден у нескольких разных товаров.

    Раньше в таких случаях брался произвольный `.first()` — в чек мог уйти не тот
    товар. Теперь бэкенд не угадывает: касса получает 409 и просит выбрать вручную.
    """

    def __init__(self, barcode: str, matches):
        self.barcode = barcode
        self.matches = matches  # [(id_str, name), ...]
        super().__init__(f"Штрихкод {barcode} найден у нескольких товаров.")


def _ambiguous_barcode_response(exc: "AmbiguousBarcode") -> Response:
    return Response(
        {
            "ambiguous": True,
            "message": (
                f"Штрихкод {exc.barcode} найден у нескольких товаров — "
                f"выберите нужный вручную."
            ),
            "matches": [{"id": mid, "name": name} for mid, name in exc.matches],
        },
        status=status.HTTP_409_CONFLICT,
    )


def _product_matches_candidates(product, candidates, company_id) -> bool:
    """Проверка, что найденный (в т.ч. из кэша) товар действительно содержит один
    из кандидатов-штрихкодов — защита от протухшего/чужого кэша."""
    if (getattr(product, "barcode", None) or "") in candidates:
        return True
    return ProductAlternateBarcode.objects.filter(
        product_id=product.pk, company_id=company_id, barcode__in=candidates
    ).exists()


def _resolve_product_by_barcode_for_pos(company_id, barcode: str, *, only_fields):
    """Товар по основному или дополнительному штрихкоду. В кэше хранится только UUID.

    Приоритет — точное совпадение основного `barcode` (это и есть напечатанный код),
    затем кэш (с проверкой), затем нормализованные кандидаты и альт-коды. Если после
    нормализации/альтов под скан подходят РАЗНЫЕ товары — поднимаем AmbiguousBarcode,
    а не берём произвольный.
    """
    candidates = _pos_barcode_lookup_candidates(barcode)
    if not candidates:
        return None
    raw = (barcode or "").strip()

    # 1) Точное совпадение основного штрихкода — вне конкуренции.
    exact = (
        Product.objects.only(*only_fields)
        .filter(company_id=company_id, barcode=raw)
        .first()
    )
    if exact:
        pos_scan_logger.info("scan barcode=%s company=%s -> product=%s (exact)", raw, company_id, exact.pk)
        return exact

    # 2) Кэш — но проверяем, что товар всё ещё несёт этот штрихкод.
    for candidate in candidates:
        cache_key = f"product_barcode:{company_id}:{candidate}"
        cached_id = _product_pk_from_cache(cache.get(cache_key))
        if cached_id:
            p = (
                Product.objects.only(*only_fields)
                .filter(pk=cached_id, company_id=company_id)
                .first()
            )
            if p and _product_matches_candidates(p, candidates, company_id):
                pos_scan_logger.info("scan barcode=%s company=%s -> product=%s (cache)", raw, company_id, p.pk)
                return p
            cache.delete(cache_key)

    # 3) Полный поиск по кандидатам (основной + альтернативные), детерминированно.
    matches = list(
        Product.objects.only(*only_fields)
        .filter(company_id=company_id)
        .filter(Q(barcode__in=candidates) | Q(alternate_barcodes__barcode__in=candidates))
        .distinct()
        .order_by("created_at", "id")
    )
    if not matches:
        return None
    if len(matches) > 1:
        pairs = [(str(m.pk), getattr(m, "name", "")) for m in matches]
        pos_scan_logger.warning("scan barcode=%s company=%s AMBIGUOUS -> %s", raw, company_id, pairs)
        raise AmbiguousBarcode(raw, pairs)

    product = matches[0]
    for candidate in candidates:
        cache.set(f"product_barcode:{company_id}:{candidate}", str(product.pk), 300)
    pos_scan_logger.info("scan barcode=%s company=%s -> product=%s", raw, company_id, product.pk)
    return product


def _resolve_product_by_plu_or_code_for_pos(company_id, scale_data: dict, *, only_fields):
    """Весовой штрих: сначала PLU (как на складе), затем legacy-поиск по code."""
    plu = scale_data.get("plu")
    if plu is not None:
        product = Product.objects.only(*only_fields).filter(company_id=company_id, plu=plu).first()
        if product:
            return product

    raw_code = scale_data.get("raw_code") or ""
    try:
        normalized_code = str(int(raw_code))
    except Exception:
        normalized_code = raw_code
    padded_code = normalized_code.zfill(4) if normalized_code.isdigit() else normalized_code

    for code_value in (normalized_code, raw_code, padded_code):
        if not code_value:
            continue
        cache_key = f"product_code:{company_id}:{code_value}"
        cached_id = _product_pk_from_cache(cache.get(cache_key))
        if cached_id:
            try:
                return Product.objects.only(*only_fields).get(pk=cached_id, company_id=company_id)
            except (Product.DoesNotExist, TypeError, ValueError):
                cache.delete(cache_key)
        try:
            product = Product.objects.only(*only_fields).get(
                company_id=company_id,
                code=code_value,
            )
            cache.set(cache_key, str(product.pk), 300)
            return product
        except Product.DoesNotExist:
            continue
    return None


def _lookup_product_for_pos_scan(company_id, barcode: str, *, only_fields=POS_SCAN_PRODUCT_FIELDS):
    """
    Единый поиск товара для POS-скана.
    Возвращает (product, scale_data|None, error_message|None).
    """
    barcode = (barcode or "").strip()
    if not barcode:
        return None, None, "Пустой штрихкод"

    product = _resolve_product_by_barcode_for_pos(company_id, barcode, only_fields=only_fields)
    if product:
        return product, None, None

    # Прямой PLU (короткий числовой код с этикетки/весов)
    if barcode.isdigit() and len(barcode) <= 7:
        try:
            plu_value = int(barcode)
            product = Product.objects.only(*only_fields).filter(company_id=company_id, plu=plu_value).first()
            if product:
                return product, None, None
        except ValueError:
            pass

    scale_mode, scale_layout, scale_amount_unit = _company_scale_barcode_settings(company_id)
    scale_variants = _scale_barcode_variants(barcode, scale_mode, scale_layout, scale_amount_unit)
    if scale_variants:
        for idx, scale_data in enumerate(scale_variants):
            product = _resolve_product_by_plu_or_code_for_pos(
                company_id, scale_data, only_fields=only_fields
            )
            if not product:
                continue
            if idx > 0:
                pos_scan_logger.warning(
                    "scan barcode=%s company=%s: товар найден по ЗАПАСНОЙ раскладке весов "
                    "(plu=%s вместо %s) — проверьте Company.scale_barcode_layout",
                    barcode, company_id, scale_data.get("plu"), scale_variants[0].get("plu"),
                )
            finalize_error = _finalize_scale_data_for_product(product, scale_data)
            if finalize_error:
                return None, scale_data, finalize_error
            return product, scale_data, None

        primary = scale_variants[0]
        plu = primary.get("plu")
        raw_code = primary.get("raw_code")
        message = f"Товар с PLU {plu} / кодом {raw_code} не найден"
        if len(scale_variants) > 1:
            tried = " / ".join(str(v.get("plu")) for v in scale_variants)
            message = f"Товар не найден: проверены PLU {tried} (штрихкод {barcode})"
        return None, primary, message

    # Fallback как на складе: 13 цифр → PLU из середины штрихкода
    scale_loose = _parse_scale_barcode_loose(barcode)
    if scale_loose:
        product = Product.objects.only(*only_fields).filter(
            company_id=company_id,
            plu=scale_loose["plu"],
        ).first()
        if product:
            return product, scale_loose, None
        plu = scale_loose.get("plu")
        raw_code = scale_loose.get("raw_code")
        return None, scale_loose, f"Товар с PLU {plu} / кодом {raw_code} не найден"

    # Внутренний код товара (code), если штрихкод числовой
    if barcode.isdigit():
        code_candidates = [barcode, str(int(barcode)), barcode.zfill(4)]
        for code_value in code_candidates:
            product = Product.objects.only(*only_fields).filter(
                company_id=company_id,
                code=code_value,
            ).first()
            if product:
                return product, None, None

    return None, None, "Товар не найден"


def _lock_pos_target_cart(*, company, user, shift, sale_id=None):
    """Блокировка одной open-корзины без annotate (PostgreSQL: FOR UPDATE + GROUP BY запрещён)."""
    cart = _find_locked_shift_cart(company=company, user=user, shift=shift, sale_id=sale_id)
    if not cart:
        if sale_id:
            raise ValidationError({"sale_id": "Открытая корзина не найдена в этой смене."})
        raise ValidationError({"detail": "Нет открытых корзин в смене."})
    return cart


def _resolve_pos_cashbox(company, branch, cashbox_id=None):
    """
    Правило:
    - если cashbox_id передали -> берём её (только этой компании/филиала)
    - иначе -> берём последнюю созданную кассу этого филиала
    - если в филиале нет кассы -> пробуем глобальную (branch NULL)
    """
    qs = Cashbox.objects.filter(company=company)

    if branch is None:
        qs_branch = qs.filter(branch__isnull=True)
    else:
        qs_branch = qs.filter(branch=branch)

    if cashbox_id:
        cb = qs_branch.filter(id=cashbox_id).first()
        if not cb:
            raise ValidationError({"cashbox_id": "Касса не найдена или не принадлежит этому филиалу."})
        return cb

    cb = qs_branch.order_by("-created_at").first()
    if cb:
        return cb

    cb = qs.filter(branch__isnull=True).order_by("-created_at").first()
    return cb


def _find_open_shift_for_cashier(*, company, cashier, cashbox=None, branch=None, for_update=False):
    qs = CashShift.objects.filter(
        company=company,
        cashier=cashier,
        status=CashShift.Status.OPEN,
    )
    if for_update:
        qs = qs.select_for_update()
    if cashbox is not None:
        qs = qs.filter(cashbox=cashbox)
    elif branch is not None:
        qs = qs.filter(branch=branch)
    return qs.order_by("-opened_at").first()


def _resolve_requested_open_shift(*, company, cashier, shift_id, cashbox_id=None):
    try:
        parsed_shift_id = uuid.UUID(str(shift_id))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError({"shift": "Некорректный ID смены."})

    shift = (
        CashShift.objects.select_related("cashbox", "cashier")
        .filter(id=parsed_shift_id, company=company)
        .first()
    )
    if not shift or shift.status != CashShift.Status.OPEN:
        raise ValidationError(
            {"detail": "Смена не открыта. Сначала откройте смену на кассе, затем начните продажу."}
        )
    if cashbox_id and str(shift.cashbox_id) != str(cashbox_id):
        raise ValidationError({"cashbox_id": "Переданная смена относится к другой кассе."})
    return shift


def _ensure_open_shift(*, company, branch, cashier, cashbox, opening_cash=None):
    """
    Возвращает открытую смену ТОЛЬКО этого cashier в этой cashbox.
    Если нет — открывает новую.
    Чужую смену НЕ возвращает никогда.
    """
    if not cashier or not getattr(cashier, "is_authenticated", False):
        raise ValidationError({"cashier": "Нужен кассир."})
    if not cashbox:
        raise ValidationError({"cashbox": "Нужна касса."})

    if opening_cash in (None, "", "null", "None"):
        opening_cash = None
    if opening_cash is not None:
        try:
            opening_cash = Decimal(str(opening_cash))
        except Exception:
            opening_cash = Decimal("0.00")
    else:
        opening_cash = Decimal("0.00")

    qs = (
        CashShift.objects.select_for_update()
        .filter(
            company=company,
            cashbox=cashbox,
            status=CashShift.Status.OPEN,
            cashier=cashier,
        )
        .order_by("-opened_at")
    )
    shift = qs.first()
    if shift:
        return shift

    shift = CashShift.objects.create(
        company=company,
        branch=branch,
        cashbox=cashbox,
        cashier=cashier,
        status=CashShift.Status.OPEN,
        opened_at=timezone.now(),
        opening_cash=opening_cash,
    )
    schedule_after_commit(sync_ekassa_after_local_shift_open_by_id, shift.id)
    return shift


class ClientReconciliationJSONAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, client_id, *args, **kwargs):
        company = request.user.company
        client = get_object_or_404(Client, id=client_id, company=company)

        source = (request.query_params.get("source") or "both").lower()
        currency = request.query_params.get("currency") or "KGS"

        s = request.query_params.get("start")
        e = request.query_params.get("end")
        if not s or not e:
            return Response(
                {"detail": "Укажите параметры start и end (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_raw = parse_datetime(s) or parse_date(s)
        end_raw = parse_datetime(e) or parse_date(e)
        if not start_raw or not end_raw:
            return Response(
                {"detail": "Неверный формат дат. Используйте YYYY-MM-DD или ISO datetime."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_dt = _aware(start_raw, end=False)
        end_dt = _aware(end_raw, end=True)

        if start_dt > end_dt:
            return Response(
                {"detail": "start не может быть больше end."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------- opening ----------
        debit_before = Decimal("0.00")
        credit_before = Decimal("0.00")

        if source in ("both", "sales"):
            # Отменённые продажи (полный возврат) не создают задолженности —
            # исключаем их, чтобы возврат корректно уменьшал долг в акте сверки.
            sales_before = (
                Sale.objects.filter(company=company, client=client, created_at__lt=start_dt)
                .exclude(status=Sale.Status.CANCELED)
                .aggregate(s=Sum("total"))
                .get("s")
                or Decimal("0")
            )
            debit_before += sales_before

        if ClientDeal and source in ("both", "deals"):
            deals_before = (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    created_at__lt=start_dt,
                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT],
                )
                .aggregate(s=Sum("amount"))
                .get("s")
                or Decimal("0")
            )
            debit_before += deals_before

            pre_before = (
                ClientDeal.objects.filter(company=company, client=client, created_at__lt=start_dt)
                .aggregate(s=Sum("prepayment"))
                .get("s")
                or Decimal("0")
            )
            credit_before += pre_before

        if DealInstallment and source in ("both", "deals"):
            inst_before = (
                DealInstallment.objects.filter(
                    deal__company=company,
                    deal__client=client,
                    paid_on__isnull=False,
                    paid_on__lt=start_dt.date(),
                )
                .aggregate(s=Sum("amount"))
                .get("s")
                or Decimal("0")
            )
            credit_before += inst_before

        opening = q2(debit_before - credit_before)

        # ---------- entries ----------
        entries = []

        def _push(dt, title, a_debit, a_credit, b_debit, b_credit, ref_type=None, ref_id=None):
            # отдаём ISO в локальном времени, чтобы фронту было проще
            dt_local = timezone.localtime(dt) if hasattr(timezone, "localtime") else dt
            entries.append(
                {
                    "date": dt_local.isoformat(),
                    "title": title,
                    "a_debit": fmt(a_debit),
                    "a_credit": fmt(a_credit),
                    "b_debit": fmt(b_debit),
                    "b_credit": fmt(b_credit),
                    "ref_type": ref_type,
                    "ref_id": str(ref_id) if ref_id else None,
                }
            )

        if source in ("both", "sales"):
            qs = (
                Sale.objects.filter(
                    company=company,
                    client=client,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                )
                .exclude(status=Sale.Status.CANCELED)
                .order_by("created_at")
            )
            for srow in qs:
                amt = q2(srow.total)
                if amt > 0:
                    _push(
                        srow.created_at,
                        f"Продажа {srow.id}",
                        a_debit=amt,
                        a_credit=Decimal("0.00"),
                        b_debit=Decimal("0.00"),
                        b_credit=amt,
                        ref_type="sale",
                        ref_id=srow.id,
                    )

        if ClientDeal and source in ("both", "deals"):
            deals_qs = (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT],
                )
                .order_by("created_at")
            )
            for d in deals_qs:
                amt = q2(d.amount)
                if amt > 0:
                    _push(
                        d.created_at,
                        f"Сделка: {d.title} ({d.get_kind_display()})",
                        a_debit=amt,
                        a_credit=Decimal("0.00"),
                        b_debit=Decimal("0.00"),
                        b_credit=amt,
                        ref_type="deal",
                        ref_id=d.id,
                    )

            pre_qs = (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    prepayment__gt=0,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                )
                .order_by("created_at")
            )
            for d in pre_qs:
                pp = q2(d.prepayment)
                _push(
                    d.created_at,
                    f"Предоплата (сделка: {d.title})",
                    a_debit=Decimal("0.00"),
                    a_credit=pp,
                    b_debit=pp,
                    b_credit=Decimal("0.00"),
                    ref_type="deal_prepayment",
                    ref_id=d.id,
                )

        if DealInstallment and source in ("both", "deals"):
            inst_qs = (
                DealInstallment.objects.filter(
                    deal__company=company,
                    deal__client=client,
                    paid_on__isnull=False,
                    paid_on__gte=start_dt.date(),
                    paid_on__lte=end_dt.date(),
                )
                .select_related("deal")
                .order_by("paid_on", "number")
            )
            for inst in inst_qs:
                amt = q2(inst.amount)
                dt = _aware(inst.paid_on, end=False)
                _push(
                    dt,
                    f"Оплата по рассрочке №{inst.number} (сделка: {inst.deal.title})",
                    a_debit=Decimal("0.00"),
                    a_credit=amt,
                    b_debit=amt,
                    b_credit=Decimal("0.00"),
                    ref_type="installment_payment",
                    ref_id=inst.id,
                )

        # сортировка по дате (ISO строка уже есть, но сортируем по реальному dt)
        # поэтому собираем ещё раз через парсинг ISO не надо — проще сортировать до преобразования
        # тут уже entries готовые => сортируем по date-строке, она ISO и корректно сортируется
        entries.sort(key=lambda x: x["date"])

        # ---------- totals & closing ----------
        def _sum_field(field: str) -> Decimal:
            s = Decimal("0")
            for r in entries:
                try:
                    s += Decimal(str(r[field]).replace(",", "."))
                except Exception:
                    pass
            return q2(s)

        totals = {
            "a_debit": fmt(_sum_field("a_debit")),
            "a_credit": fmt(_sum_field("a_credit")),
            "b_debit": fmt(_sum_field("b_debit")),
            "b_credit": fmt(_sum_field("b_credit")),
        }

        # closing = opening + обороты (как в PDF)
        closing = q2(opening + _sum_field("a_debit") - _sum_field("a_credit"))
        as_of_date = (end_dt + timedelta(days=1)).date()

        company_name = getattr(company, "llc", None) or getattr(company, "name", str(company))
        client_name = client.llc or client.enterprise or client.full_name

        debtor = None
        creditor = None
        amount = abs(closing)

        if closing > 0:
            debtor = client_name
            creditor = company_name
        elif closing < 0:
            debtor = company_name
            creditor = client_name

        # ---------- running balance after each entry (для фронта) ----------
        # running = opening; for each row: running += a_debit - a_credit
        running = opening
        for r in entries:
            a_deb = _as_decimal(r["a_debit"], default=Decimal("0"))
            a_cre = _as_decimal(r["a_credit"], default=Decimal("0"))
            running = q2(running + a_deb - a_cre)
            r["running_balance_after"] = fmt(running)

        payload = {
            "company": {
                "id": str(company.id),
                "name": company_name,
                "inn": _safe(getattr(company, "inn", None)),
                "okpo": _safe(getattr(company, "okpo", None)),
                "score": _safe(getattr(company, "score", None)),
                "bik": _safe(getattr(company, "bik", None)),
                "address": _safe(getattr(company, "address", None)),
                "phone": _safe(getattr(company, "phone", None)),
                "email": _safe(getattr(company, "email", None)),
            },
            "client": {
                "id": str(client.id),
                "name": client_name,
                "inn": _safe(getattr(client, "inn", None)),
                "okpo": _safe(getattr(client, "okpo", None)),
                "score": _safe(getattr(client, "score", None)),
                "bik": _safe(getattr(client, "bik", None)),
                "address": _safe(getattr(client, "address", None)),
                "phone": _safe(getattr(client, "phone", None)),
                "email": _safe(getattr(client, "email", None)),
            },
            "period": {
                "start": start_dt.date().isoformat(),
                "end": end_dt.date().isoformat(),
                "source": source,
                "currency": currency,
            },
            "opening_balance": fmt(opening),
            "entries": entries,
            "totals": totals,
            "closing_balance": fmt(closing),
            "as_of_date": as_of_date.isoformat(),
            "debt": {
                "debtor": debtor,
                "creditor": creditor,
                "amount": fmt(amount),
                "currency": currency,
            },
        }
        return Response(payload, status=200)

class ClientReconciliationClassicAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, client_id, *args, **kwargs):
        company = request.user.company
        client = get_object_or_404(Client, id=client_id, company=company)

        source = (request.query_params.get("source") or "both").lower()
        currency = request.query_params.get("currency") or "KGS"

        s = request.query_params.get("start")
        e = request.query_params.get("end")
        if not s or not e:
            return self._error_pdf("Укажите параметры start и end (YYYY-MM-DD).")

        start_dt = parse_datetime(s) or parse_date(s)
        end_dt = parse_datetime(e) or parse_date(e)
        if not start_dt or not end_dt:
            return self._error_pdf("Неверный формат дат. Используйте YYYY-MM-DD или ISO datetime.")

        start_dt = _aware(start_dt, end=False)
        end_dt = _aware(end_dt, end=True)

        debit_before = Decimal("0.00")
        credit_before = Decimal("0.00")

        if source in ("both", "sales"):
            # Отменённые продажи (полный возврат) не создают задолженности —
            # исключаем их, чтобы возврат корректно уменьшал долг в акте сверки.
            sales_before = (
                Sale.objects.filter(company=company, client=client, created_at__lt=start_dt)
                .exclude(status=Sale.Status.CANCELED)
                .aggregate(s=Sum("total"))["s"]
                or Decimal("0")
            )
            debit_before += sales_before

        if ClientDeal and source in ("both", "deals"):
            deals_before = (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    created_at__lt=start_dt,
                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT],
                ).aggregate(s=Sum("amount"))["s"]
                or Decimal("0")
            )
            debit_before += deals_before

            pre_before = (
                ClientDeal.objects.filter(company=company, client=client, created_at__lt=start_dt).aggregate(
                    s=Sum("prepayment")
                )["s"]
                or Decimal("0")
            )
            credit_before += pre_before

        if DealInstallment and source in ("both", "deals"):
            inst_before = (
                DealInstallment.objects.filter(
                    deal__company=company,
                    deal__client=client,
                    paid_on__isnull=False,
                    paid_on__lt=start_dt.date(),
                ).aggregate(s=Sum("amount"))["s"]
                or Decimal("0")
            )
            credit_before += inst_before

        opening = q2(debit_before - credit_before)

        entries: List[Dict] = []

        if source in ("both", "sales"):
            for s in (
                Sale.objects.filter(
                    company=company,
                    client=client,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                )
                .exclude(status=Sale.Status.CANCELED)
                .order_by("created_at")
            ):
                if q2(s.total) > 0:
                    entries.append(
                        dict(
                            date=s.created_at,
                            title=f"Продажа {s.id}",
                            a_debit=q2(s.total),
                            a_credit=Decimal("0.00"),
                            b_debit=Decimal("0.00"),
                            b_credit=q2(s.total),
                        )
                    )

        if ClientDeal and source in ("both", "deals"):
            for d in (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT],
                ).order_by("created_at")
            ):
                amt = q2(d.amount)
                if amt > 0:
                    entries.append(
                        dict(
                            date=d.created_at,
                            title=f"Сделка: {d.title} ({d.get_kind_display()})",
                            a_debit=amt,
                            a_credit=Decimal("0.00"),
                            b_debit=Decimal("0.00"),
                            b_credit=amt,
                        )
                    )

            for d in (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    prepayment__gt=0,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                ).order_by("created_at")
            ):
                pp = q2(d.prepayment)
                entries.append(
                    dict(
                        date=d.created_at,
                        title=f"Предоплата (сделка: {d.title})",
                        a_debit=Decimal("0.00"),
                        a_credit=pp,
                        b_debit=pp,
                        b_credit=Decimal("0.00"),
                    )
                )

        if DealInstallment and source in ("both", "deals"):
            for inst in (
                DealInstallment.objects.filter(
                    deal__company=company,
                    deal__client=client,
                    paid_on__isnull=False,
                    paid_on__gte=start_dt.date(),
                    paid_on__lte=end_dt.date(),
                )
                .select_related("deal")
                .order_by("paid_on", "number")
            ):
                amt = q2(inst.amount)
                dt = _aware(inst.paid_on, end=False)
                entries.append(
                    dict(
                        date=dt,
                        title=f"Оплата по рассрочке №{inst.number} (сделка: {inst.deal.title})",
                        a_debit=Decimal("0.00"),
                        a_credit=amt,
                        b_debit=amt,
                        b_credit=Decimal("0.00"),
                    )
                )

        entries.sort(key=lambda x: x["date"])

        totals = dict(
            a_debit=q2(sum(x["a_debit"] for x in entries) if entries else 0),
            a_credit=q2(sum(x["a_credit"] for x in entries) if entries else 0),
            b_debit=q2(sum(x["b_debit"] for x in entries) if entries else 0),
            b_credit=q2(sum(x["b_credit"] for x in entries) if entries else 0),
        )

        closing = q2(opening + totals["a_debit"] - totals["a_credit"])
        on_date = (end_dt + timedelta(days=1)).date()

        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        W, H = A4

        try:
            FONT, BFONT = "DejaVu", "DejaVu-Bold"
            p.setFont(BFONT, 14)
        except Exception:
            FONT, BFONT = "Helvetica", "Helvetica-Bold"
            p.setFont(BFONT, 14)

        p.drawCentredString(W / 2, H - 20 * mm, "АКТ СВЕРКИ ВЗАИМНЫХ РАСЧЁТОВ")
        p.setFont(FONT, 11)
        p.drawCentredString(
            W / 2,
            H - 27 * mm,
            f"Период: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}   валюта сверки {currency}",
        )

        company_name = getattr(company, "llc", None) or getattr(company, "name", str(company))
        client_name = client.llc or client.enterprise or client.full_name

        p.setFont(BFONT, 10)
        p.drawString(20 * mm, H - 38 * mm, "КОМПАНИЯ")
        p.drawString(110 * mm, H - 38 * mm, "КЛИЕНТ")
        p.setFont(FONT, 11)
        p.drawString(20 * mm, H - 44 * mm, _safe(company_name))
        p.drawString(110 * mm, H - 44 * mm, _safe(client_name))
        p.setFont(FONT, 9)
        p.drawString(
            20 * mm,
            H - 50 * mm,
            f"ИНН: {_safe(getattr(company,'inn',None))}    ОКПО: {_safe(getattr(company,'okpo',None))}",
        )
        p.drawString(
            110 * mm,
            H - 50 * mm,
            f"ИНН: {_safe(client.inn)}    ОКПО: {_safe(client.okpo)}",
        )
        p.drawString(
            20 * mm,
            H - 56 * mm,
            f"Р/с: {_safe(getattr(company,'score',None))}    БИК: {_safe(getattr(company,'bik',None))}",
        )
        p.drawString(
            110 * mm,
            H - 56 * mm,
            f"Р/с: {_safe(client.score)}    БИК: {_safe(client.bik)}",
        )
        p.drawString(20 * mm, H - 62 * mm, f"Адрес: {_safe(getattr(company,'address',None))}")
        p.drawString(110 * mm, H - 62 * mm, f"Адрес: {_safe(client.address)}")
        p.drawString(
            20 * mm,
            H - 68 * mm,
            f"Тел.: {_safe(getattr(company,'phone',None))}    E-mail: {_safe(getattr(company,'email',None))}",
        )
        p.drawString(
            110 * mm,
            H - 68 * mm,
            f"Тел.: {_safe(client.phone)}    E-mail: {_safe(client.email)}",
        )

        y = H - 78 * mm
        p.setFont(BFONT, 9)
        p.drawString(20 * mm, y, "№")
        p.drawString(28 * mm, y, "Содержание записи")
        p.drawString(100 * mm, y, _safe(company_name))
        p.drawString(148 * mm, y, _safe(client_name))

        y -= 5 * mm
        p.setFont(BFONT, 9)
        p.drawString(100 * mm, y, "Дт")
        p.drawString(118 * mm, y, "Кт")
        p.drawString(148 * mm, y, "Дт")
        p.drawString(166 * mm, y, "Кт")
        p.line(20 * mm, y - 1 * mm, 190 * mm, y - 1 * mm)
        y -= 6 * mm

        p.setFont(FONT, 9)
        a_dt = opening if opening > 0 else Decimal("0.00")
        a_kt = -opening if opening < 0 else Decimal("0.00")
        b_dt = a_kt
        b_kt = a_dt

        p.drawString(28 * mm, y, "Сальдо начальное")
        p.drawRightString(115 * mm, y, fmt(a_dt))
        p.drawRightString(133 * mm, y, fmt(a_kt))
        p.drawRightString(163 * mm, y, fmt(b_dt))
        p.drawRightString(181 * mm, y, fmt(b_kt))
        y -= 7 * mm

        num = 0
        p.setFont(FONT, 9)

        def ensure_page_space(current_y: float) -> float:
            if current_y < 40 * mm:
                p.showPage()
                try:
                    p.setFont(BFONT, 10)
                except Exception:
                    p.setFont("Helvetica-Bold", 10)
                p.drawString(20 * mm, H - 20 * mm, "Продолжение акта сверки")
                yy = H - 30 * mm
                p.setFont(BFONT, 9)
                p.drawString(20 * mm, yy, "№")
                p.drawString(28 * mm, yy, "Содержание записи")
                p.drawString(100 * mm, yy, _safe(company_name))
                p.drawString(148 * mm, yy, _safe(client_name))
                yy -= 5 * mm
                p.drawString(100 * mm, yy, "Дт")
                p.drawString(118 * mm, yy, "Кт")
                p.drawString(148 * mm, yy, "Дт")
                p.drawString(166 * mm, yy, "Кт")
                p.line(20 * mm, yy - 1 * mm, 190 * mm, yy - 1 * mm)
                return yy - 6 * mm
            return current_y

        for row in entries:
            y = ensure_page_space(y)
            num += 1
            p.drawString(20 * mm, y, str(num))
            desc = row["title"]
            line1 = desc[:52]
            line2 = desc[52:104] if len(desc) > 52 else ""
            p.drawString(28 * mm, y, line1)

            p.drawRightString(115 * mm, y, fmt(row["a_debit"]))
            p.drawRightString(133 * mm, y, fmt(row["a_credit"]))
            p.drawRightString(163 * mm, y, fmt(row["b_debit"]))
            p.drawRightString(181 * mm, y, fmt(row["b_credit"]))
            y -= 6 * mm
            if line2:
                y = ensure_page_space(y)
                p.drawString(28 * mm, y, line2)
                y -= 6 * mm

        y -= 4 * mm
        p.line(20 * mm, y, 190 * mm, y)
        y -= 7 * mm
        p.setFont(BFONT, 10)
        p.drawString(28 * mm, y, "Итого обороты:")
        p.drawRightString(115 * mm, y, fmt(totals["a_debit"]))
        p.drawRightString(133 * mm, y, fmt(totals["a_credit"]))
        p.drawRightString(163 * mm, y, fmt(totals["b_debit"]))
        p.drawRightString(181 * mm, y, fmt(totals["b_credit"]))
        y -= 10 * mm

        debtor, creditor, amount = None, None, abs(closing)
        if closing > 0:
            debtor = client_name
            creditor = company_name
        elif closing < 0:
            debtor = company_name
            creditor = client_name

        p.setFont(FONT, 10)
        if amount == 0:
            phrase = f"Задолженность отсутствует на {on_date.strftime('%d.%m.%Y')}."
        else:
            phrase = (
                f"Задолженность {debtor} перед {creditor} на {on_date.strftime('%d.%m.%Y')} "
                f"составляет {fmt(amount)} {currency}"
            )
        p.drawString(20 * mm, y, phrase)
        y -= 8 * mm
        if amount == 0:
            p.drawString(20 * mm, y, "(Ноль сом 00 тыйын)")
        y -= 16 * mm

        p.setFont(BFONT, 10)
        p.drawString(20 * mm, y, _safe(company_name))
        p.drawString(110 * mm, y, _safe(client_name))
        y -= 8 * mm
        p.setFont(FONT, 10)
        p.drawString(20 * mm, y, "Главный бухгалтер: __________________")
        p.drawString(110 * mm, y, "Главный бухгалтер: __________________")

        p.showPage()
        p.save()
        buf.seek(0)
        filename = f"reconciliation_classic_{client.id}_{start_dt.date()}_{end_dt.date()}.pdf"
        return FileResponse(buf, as_attachment=True, filename=filename)

    def _error_pdf(self, message: str):
        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        p.setFont("Helvetica-Bold", 14)
        p.drawString(30 * mm, 260 * mm, "Невозможно сформировать акт сверки")
        p.setFont("Helvetica", 11)
        p.drawString(30 * mm, 248 * mm, message)
        p.showPage()
        p.save()
        buf.seek(0)
        return FileResponse(buf, as_attachment=False, filename="reconciliation_error.pdf", status=400)


class SaleInvoiceDownloadAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company", "user", "client").prefetch_related("items__product"),
            id=pk,
            company=request.user.company,
        )

        doc_no = ensure_sale_doc_number(sale)

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=(210 * mm, 297 * mm))

        # ✅ безопасно: если DejaVu не зарегистрирован — упадём на Helvetica
        _set_font(p, "DejaVu-Bold", 14, fallback="Helvetica-Bold")
        p.drawCentredString(105 * mm, 280 * mm, f"НАКЛАДНАЯ № {doc_no}")
        _set_font(p, "DejaVu", 10, fallback="Helvetica")
        p.drawCentredString(105 * mm, 273 * mm, f"от {sale.created_at.strftime('%d.%m.%Y %H:%M')}")

        company = sale.company
        client = sale.client

        left = _party_lines(
            "КОМПАНИЯ",
            getattr(company, "llc", None) or getattr(company, "name", "—"),
            inn=getattr(company, "inn", None),
            okpo=getattr(company, "okpo", None),
            score=getattr(company, "score", None),
            bik=getattr(company, "bik", None),
            addr=getattr(company, "address", None),
            phone=getattr(company, "phone", None),
        )
        if client:
            right = _party_lines(
                "ПОКУПАТЕЛЬ",
                client.llc or client.enterprise or client.full_name,
                inn=client.inn,
                okpo=client.okpo,
                score=client.score,
                bik=client.bik,
                addr=client.address,
                phone=client.phone,
            )
        else:
            right = _party_lines("ПОКУПАТЕЛЬ", "—")

        y = 260 * mm
        x_left, x_right = 20 * mm, 110 * mm
        _set_font(p, "DejaVu-Bold", 10, fallback="Helvetica-Bold")
        p.drawString(x_left, y, left[0])
        p.drawString(x_right, y, right[0])
        y -= 6 * mm
        _set_font(p, "DejaVu", 10, fallback="Helvetica")
        for i in range(1, len(left)):
            p.drawString(x_left, y, left[i])
            p.drawString(x_right, y, right[i])
            y -= 6 * mm

        y -= 6 * mm
        _set_font(p, "DejaVu-Bold", 10, fallback="Helvetica-Bold")
        p.drawString(20 * mm, y, "Товар")
        p.drawRightString(140 * mm, y, "Кол-во")
        p.drawRightString(160 * mm, y, "Цена")
        p.drawRightString(190 * mm, y, "Сумма")

        y -= 5
        p.line(20 * mm, y, 190 * mm, y)
        y -= 10

        _set_font(p, "DejaVu", 10, fallback="Helvetica")
        for it in sale.items.all():
            p.drawString(20 * mm, y, (it.name_snapshot or "")[:60])
            p.drawRightString(140 * mm, y, str(it.quantity))
            p.drawRightString(160 * mm, y, fmt_money(it.unit_price))
            p.drawRightString(
                190 * mm,
                y,
                fmt_money((it.unit_price * it.quantity) - (getattr(it, "line_discount", None) or 0)),
            )
            y -= 7 * mm
            if y < 60 * mm:
                p.showPage()
                y = 270 * mm
                _set_font(p, "DejaVu", 10, fallback="Helvetica")

        y -= 10
        _set_font(p, "DejaVu-Bold", 11, fallback="Helvetica-Bold")
        p.drawRightString(190 * mm, y, f"СУММА (без скидок): {fmt_money(sale.subtotal)}")
        y -= 6 * mm
        if sale.discount_total and sale.discount_total > 0:
            p.drawRightString(190 * mm, y, f"СКИДКА: {fmt_money(sale.discount_total)}")
            y -= 6 * mm
        if sale.tax_total and sale.tax_total > 0:
            p.drawRightString(190 * mm, y, f"НАЛОГ: {fmt_money(sale.tax_total)}")
            y -= 6 * mm
        p.drawRightString(190 * mm, y, f"ИТОГО К ОПЛАТЕ: {fmt_money(sale.total)}")

        y -= 20
        _set_font(p, "DejaVu", 10, fallback="Helvetica")
        p.drawString(20 * mm, y, "Продавец: _____________")
        p.drawString(120 * mm, y, "Покупатель: _____________")

        p.showPage()
        p.save()

        buffer.seek(0)
        return FileResponse(buffer, as_attachment=True, filename=f"invoice_{doc_no}.pdf")


def _serialize_sale_payments(sale):
    lines = sale.payment_lines()
    out = []
    for line in lines:
        try:
            method_display = line.get_method_display()
        except Exception:
            method_display = line.method
        out.append(
            {
                "method": line.method,
                "method_display": method_display,
                "amount": fmt_money(line.amount),
            }
        )
    return out


class SaleReceiptDataAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company", "user").prefetch_related("items", "payments"),
            id=pk,
            company=request.user.company,
        )
        cashier_override = (request.query_params.get("cashier_name") or "").strip()
        cashier_name = cashier_override if cashier_override else None
        from apps.main.printers import build_receipt_payload
        from apps.ekassa.sale_bridge import wait_for_pos_sale_ekassa

        if _truthy_query_param(request.query_params.get("wait_ekassa")):
            wait_for_pos_sale_ekassa(sale.pk)
            sale.refresh_from_db()

        payload = build_receipt_payload(sale, cashier_name=cashier_name, ensure_number=True)

        if _truthy_query_param(request.query_params.get("receipt_text")):
            payload["receipt_text"] = _build_physical_receipt_text(
                sale,
                payment_method=sale.payment_method,
                cash_received=sale.cash_received,
                change=sale.change,
                include_shift=bool(getattr(sale, "shift_id", None)),
            )

        return Response(payload, status=200)


class SaleStartAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user
        company = self._company() or user.company
        branch = self._auto_branch()

        cashbox_id = request.data.get("cashbox_id")
        requested_shift_id = request.data.get("shift_id") or request.data.get("shift")
        # opening_cash НЕ используется тут намеренно:
        # смена должна быть открыта отдельным действием, а start не открывает её автоматически.

        if requested_shift_id:
            shift = _resolve_requested_open_shift(
                company=company,
                cashier=user,
                shift_id=requested_shift_id,
                cashbox_id=cashbox_id,
            )
            cashbox = shift.cashbox
            branch = shift.branch
        else:
            cashbox = _resolve_pos_cashbox(company, branch, cashbox_id=cashbox_id)
            if not cashbox and not cashbox_id:
                shift = _find_open_shift_for_cashier(company=company, cashier=user, branch=branch)
                if shift:
                    cashbox = shift.cashbox
                    branch = shift.branch
                else:
                    raise ValidationError({"detail": "Нет кассы для этого филиала. Создай Cashbox."})
            elif not cashbox:
                raise ValidationError({"detail": "Нет кассы для этого филиала. Создай Cashbox."})

            shift = _find_open_shift_for_cashier(company=company, cashier=user, cashbox=cashbox)
            if not shift and not cashbox_id:
                shift = _find_open_shift_for_cashier(company=company, cashier=user, branch=branch)
                if shift:
                    cashbox = shift.cashbox
                    branch = shift.branch

        if not shift:
            raise ValidationError(
                {"detail": "Смена не открыта. Сначала откройте смену на кассе, затем начните продажу."}
            )

        if not cashbox:
            cashbox = shift.cashbox
            if shift:
                branch = shift.branch

        opts = StartCartOptionsSerializer(data=_normalize_pos_request_data(request.data))
        opts.is_valid(raise_exception=True)
        is_wholesale_req = (
            bool(opts.validated_data.get("is_wholesale"))
            if "is_wholesale" in opts.validated_data
            else None
        )
        is_new = bool(opts.validated_data.get("is_new"))
        requested_sale_id = opts.validated_data.get("sale_id")

        with transaction.atomic():
            cart = None
            created = False

            if requested_sale_id:
                cart = _find_locked_shift_cart(
                    company=company,
                    user=user,
                    shift=shift,
                    sale_id=requested_sale_id,
                )
                if not cart:
                    raise ValidationError({"sale_id": "Открытая корзина не найдена в этой смене."})
            elif is_new:
                open_count = _shift_carts_base_qs(company, user, shift).count()
                if open_count >= MAX_OPEN_CARTS_PER_SHIFT:
                    raise ValidationError(
                        {"detail": f"Достигнут лимит открытых корзин ({MAX_OPEN_CARTS_PER_SHIFT})."}
                    )
                cart = Cart.objects.create(
                    company=company,
                    user=user,
                    status=Cart.Status.ACTIVE,
                    branch=branch or shift.branch,
                    shift=shift,
                    is_default=False,
                    is_wholesale=bool(is_wholesale_req) if is_wholesale_req is not None else False,
                )
                created = True
            else:
                cart = _find_locked_shift_cart(company=company, user=user, shift=shift)
                if cart is None:
                    cart = Cart.objects.create(
                        company=company,
                        user=user,
                        status=Cart.Status.ACTIVE,
                        branch=branch or shift.branch,
                        shift=shift,
                        is_default=True,
                        is_wholesale=bool(is_wholesale_req) if is_wholesale_req is not None else False,
                    )
                    created = True
                elif (branch or shift.branch) and cart.branch_id != getattr(shift.branch, "id", None):
                    cart.branch = branch or shift.branch
                    cart.save(update_fields=["branch"])
                elif not _shift_carts_base_qs(company, user, shift).filter(is_default=True).exists():
                    cart.is_default = True
                    cart.save(update_fields=["is_default", "updated_at"])

            # recalc() делаем один раз — в конце (после применения скидок/опта).
            # Раньше здесь был лишний recalc() на каждый start.
            update_f = []
            wholesale_changed = False

            if _pos_body_has_explicit_field(
                request,
                "order_discount_total",
                "orderDiscountTotal",
                "order_discount_percent",
                "orderDiscountPercent",
            ):
                order_disc_total = opts.validated_data.get("order_discount_total")
                order_disc_percent = opts.validated_data.get("order_discount_percent")
                
                is_admin = getattr(request.user, "role", None) in ["owner", "admin"]
                max_dp = request.user.company.max_discount_percent
                if max_dp is not None and not is_admin:
                    if order_disc_percent is not None and Decimal(str(order_disc_percent)) > max_dp:
                        return Response({"detail": f"Максимальная скидка — {max_dp}%", "max_discount_percent": str(max_dp)}, status=400)
                    if order_disc_total is not None and getattr(cart, "total_price", 0) > 0: # Approximation before recalc or skip
                        pass # Validated on recalc or here if subtotal available, but CartStart creates cart, maybe no subtotal yet.
                        # Actually CartStart sets discount on empty cart, so total is 0. Skip total check here.
                
                if order_disc_percent is not None:
                    cart.order_discount_percent = _q2(Decimal(str(order_disc_percent)))
                    cart.order_discount_total = Decimal("0.00")
                else:
                    cart.order_discount_percent = None
                    cart.order_discount_total = _q2(order_disc_total or Decimal("0.00"))
                update_f.extend(["order_discount_total", "order_discount_percent"])

            if _pos_body_has_explicit_field(request, "is_wholesale", "isWholesale"):
                is_wholesale = bool(opts.validated_data.get("is_wholesale"))
                if getattr(cart, "is_wholesale", False) != is_wholesale:
                    cart.is_wholesale = is_wholesale
                    update_f.append("is_wholesale")
                    wholesale_changed = True

            if update_f:
                update_f.append("updated_at")
                cart.save(update_fields=update_f)
            if created or wholesale_changed:
                _reprice_cart_items_for_mode(cart)

            cart.recalc()
            cart_id = cart.id

        cart = get_object_or_404(_cart_queryset_for_response(), id=cart_id, company=company)
        return _pos_multi_cart_response(request, cart, status_code=status.HTTP_201_CREATED)


class CartDetailAPIView(MarketCashierOnlyMixin, generics.RetrieveAPIView):
    serializer_class = SaleCartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return _cart_queryset_for_response().filter(company=self.request.user.company)

    def patch(self, request, *args, **kwargs):
        """Обновить скидку на чек: order_discount_total или order_discount_percent."""
        cart = self.get_object()
        if cart.status != Cart.Status.ACTIVE:
            return Response({"detail": "Корзина не активна."}, status=400)
        opts = StartCartOptionsSerializer(data=request.data, partial=True)
        opts.is_valid(raise_exception=True)
        order_disc_total = opts.validated_data.get("order_discount_total")
        order_disc_percent = opts.validated_data.get("order_discount_percent")
        
        is_admin = getattr(request.user, "role", None) in ["owner", "admin"]
        max_dp = request.user.company.max_discount_percent
        if max_dp is not None and not is_admin:
            if order_disc_percent is not None and Decimal(str(order_disc_percent)) > max_dp:
                return Response({"detail": f"Максимальная скидка — {max_dp}%", "max_discount_percent": str(max_dp)}, status=400)
            if order_disc_total is not None:
                # We need subtotal to check limit
                subtotal = cart.total_price + (cart.order_discount_total or 0)
                limit = subtotal * (max_dp / Decimal("100.0"))
                if Decimal(str(order_disc_total)) > limit:
                    return Response({"detail": f"Максимальная скидка — {max_dp}%", "max_discount_percent": str(max_dp)}, status=400)
                    
        if order_disc_percent is not None:
            cart.order_discount_percent = _q2(Decimal(str(order_disc_percent)))
            cart.order_discount_total = Decimal("0.00")
        elif order_disc_total is not None:
            cart.order_discount_percent = None
            cart.order_discount_total = _q2(order_disc_total)
        if order_disc_total is not None or order_disc_percent is not None:
            cart.save(update_fields=["order_discount_total", "order_discount_percent", "updated_at"])
        cart.recalc()
        return _cart_response(request, cart.id, status_code=200)


class SaleScanAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        url_cart = get_object_or_404(
            Cart.objects.select_related("shift", "user"),
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        if not url_cart.shift_id:
            raise ValidationError({"detail": "Корзина не привязана к смене."})

        ser = ScanRequestSerializer(data=_normalize_pos_request_data(request.data))
        ser.is_valid(raise_exception=True)

        sale_id = ser.validated_data.get("sale_id")
        barcode = ser.validated_data["barcode"].strip()
        qty = ser.validated_data["quantity"]

        try:
            product, scale_data, lookup_error = _lookup_product_for_pos_scan(url_cart.company_id, barcode)
        except AmbiguousBarcode as exc:
            return _ambiguous_barcode_response(exc)
        if not product:
            return Response({"not_found": True, "message": lookup_error or "Товар не найден"}, status=404)

        effective_qty = _effective_qty_from_scale_data(scale_data, qty)

        with transaction.atomic():
            cart = _lock_pos_target_cart(
                company=request.user.company,
                user=request.user,
                shift=url_cart.shift,
                sale_id=sale_id,
            )
            _upsert_scanned_cart_item(cart, product, effective_qty)
            cart.recalc()
            cart_id = cart.id

        cart = get_object_or_404(_cart_queryset_for_response(), id=cart_id, company=request.user.company)
        return _pos_multi_cart_response(request, cart, status_code=status.HTTP_201_CREATED)


class SaleAddItemAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart.objects.select_related("company", "branch", "user", "shift"),
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        product = get_object_or_404(
            Product,
            id=ser.validated_data["product_id"],
            company_id=cart.company_id,
        )
        qty = ser.validated_data["quantity"]
        allow_minus = bool(ser.validated_data.get("allow_minus"))
        can_minus = allow_minus and _is_owner_like(request.user)

        unit_price = ser.validated_data.get("unit_price")
        line_discount = ser.validated_data.get("discount_total")
        sale_package_id = ser.validated_data.get("sale_package_id")
        pkg = None
        if sale_package_id:
            pkg = get_object_or_404(
                ProductPackage.objects.filter(
                    id=sale_package_id,
                    product_id=product.id,
                    company_id=cart.company_id,
                )
            )

        # unit_price — база, line_discount — скидка на строку (хранятся отдельно)
        if unit_price is not None:
            base_price = _q2(unit_price)
        else:
            if getattr(cart, "is_wholesale", False):
                raw_wholesale = getattr(product, "wholesale_price", None)
                raw_retail = getattr(product, "price", None)
                pack_price = Decimal(str(raw_wholesale)) if raw_wholesale not in (None, 0, "0") else Decimal(str(raw_retail or 0))
                if pkg:
                    ipp = Decimal(str(pkg.quantity_in_package or 0))
                    base_price = _q2(pack_price / ipp) if ipp > 0 else _q2(pack_price)
                else:
                    base_price = _q2(pack_price)
            else:
                base_price = _q2(default_unit_price_for_package(product, pkg))
        disc_total = _q2(Decimal(str(line_discount))) if line_discount is not None else Decimal("0.00")

        # Цена продажи не ниже закупочной, кроме случая со скидкой (со скидкой можно ниже)
        if disc_total <= 0:
            min_price = _q2(Decimal(str(getattr(product, "purchase_price", None) or 0)))
            if pkg:
                ipp = Decimal(str(pkg.quantity_in_package or 0))
                min_price = _q2(min_price / ipp) if ipp > 0 else min_price
            qty_dec = Decimal(str(qty))
            effective_unit = base_price - (disc_total / qty_dec) if qty_dec else base_price
            if effective_unit < min_price:
                return Response(
                    {"unit_price": f"Цена продажи не может быть ниже закупочной ({min_price})."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Блокируем корзину для предотвращения race conditions
        cart = Cart.objects.select_for_update().get(id=cart.id)

        other = total_cart_consume_packs_for_product(cart.id, product.id)
        target = (
            CartItem.objects.select_for_update()
            .filter(cart=cart, product=product, sale_package=pkg)
            .first()
        )
        if target:
            other = qty3(other - line_qty_consume_units(target.quantity, pkg))
            combined_consume = line_qty_consume_units(target.quantity + qty, pkg)
        else:
            combined_consume = line_qty_consume_units(qty, pkg)
        have = Decimal(str(product.quantity or 0))
        if (not can_minus) and qty3(other + combined_consume) > have:
            return Response(
                {
                    "detail": (
                        "Недостаточно остатка (учёт в пачках). "
                        f"Доступно не более {qty3(max(Decimal('0'), have - other))} условных пачек с учётом корзины."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if target:
            item = target
            item.quantity = qty3(item.quantity + qty)
            if unit_price is not None:
                item.unit_price = base_price
            if line_discount is not None:
                item.line_discount = (Decimal(str(item.line_discount or 0)) + disc_total)
            update_f = ["quantity"]
            if unit_price is not None:
                update_f.append("unit_price")
            if line_discount is not None:
                update_f.append("line_discount")
            item.save(update_fields=update_f, skip_full_clean=True)
        else:
            item = CartItem(
                cart=cart,
                company=cart.company,
                branch=getattr(cart, "branch", None),
                product=product,
                sale_package=pkg,
                quantity=qty3(qty),
                unit_price=base_price,
                line_discount=disc_total,
            )
            item.save(skip_full_clean=True)

        cart.recalc()
        resp = _cart_response(request, cart.id, status_code=status.HTTP_201_CREATED)
        # Возвращаем UUID конкретной добавленной/обновлённой строки, чтобы фронт
        # PATCH-ил именно её (штучную/упаковочную), а не падал в поиск по product_id.
        if isinstance(resp.data, dict):
            resp.data["added_item_id"] = str(item.id)
            resp.data["added_item"] = {
                "id": str(item.id),
                "product": str(product.id),
                "sale_package": str(pkg.id) if pkg else None,
            }
        return resp


class SaleCheckoutAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        with transaction.atomic():
            cart = get_object_or_404(
                Cart.objects.select_related("company", "branch", "user", "shift"),
                id=pk,
                company=request.user.company,
                status=Cart.Status.ACTIVE,
            )

            ser = CheckoutSerializer(data=request.data, context={"request": request, "cart": cart})
            ser.is_valid(raise_exception=True)

            print_receipt = ser.validated_data["print_receipt"]
            allow_minus = bool(ser.validated_data.get("allow_minus"))
            can_minus = allow_minus and _is_owner_like(request.user)
            client_id = ser.validated_data.get("client_id")
            payment_method = ser.validated_data.get("payment_method") or Sale.PaymentMethod.CASH
            cash_received = ser.validated_data.get("cash_received") or Decimal("0.00")
            payments = ser.validated_data.get("payments") or None
            cashbox_id = ser.validated_data.get("cashbox_id")

            if not cart.shift_id:
                company = cart.company
                branch = getattr(cart, "branch", None)
                cashbox = _resolve_pos_cashbox(company, branch, cashbox_id=cashbox_id)
                if not cashbox and not cashbox_id:
                    shift = _find_open_shift_for_cashier(company=company, cashier=request.user, branch=branch)
                    if shift:
                        cashbox = shift.cashbox
                        branch = shift.branch
                    else:
                        raise ValidationError({"detail": "Нет кассы для этого филиала. Создай Cashbox."})
                elif not cashbox:
                    raise ValidationError({"detail": "Нет кассы для этого филиала. Создай Cashbox."})

                shift = _find_open_shift_for_cashier(company=company, cashier=request.user, cashbox=cashbox)
                if not shift and not cashbox_id:
                    shift = _find_open_shift_for_cashier(company=company, cashier=request.user, branch=branch)
                    if shift:
                        cashbox = shift.cashbox
                        branch = shift.branch

                if not shift:
                    raise ValidationError(
                        {"detail": "Смена не открыта. Сначала откройте смену на кассе, затем завершите продажу."}
                    )
                cart.shift = shift
                if (branch or shift.branch) and cart.branch_id != getattr(shift.branch, "id", None):
                    cart.branch = branch or shift.branch
                    cart.save(update_fields=["shift", "branch"])
                else:
                    cart.save(update_fields=["shift"])

            cart.recalc()
            if not payments and payment_method == Sale.PaymentMethod.CASH and cash_received < cart.total:
                return Response(
                    {"detail": "Сумма, полученная наличными, меньше суммы продажи."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            client_obj = None
            if client_id:
                client_obj = get_object_or_404(Client, id=client_id, company=request.user.company)

            try:
                sale = checkout_cart(
                    cart,
                    allow_negative_stock=can_minus,
                    payments=payments,
                    payment_method=None if payments else payment_method,
                    cash_received=cash_received,
                    client=client_obj,
                )
            except NotEnoughStock as e:
                return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            except ValueError as e:
                return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

            payload = {
                "sale_id": str(sale.id),
                "status": sale.status,
                "subtotal": fmt_money(sale.subtotal),
                "discount_total": fmt_money(sale.discount_total),
                "tax_total": fmt_money(sale.tax_total),
                "total": fmt_money(sale.total),
                "client": str(sale.client_id) if sale.client_id else None,
                "client_name": getattr(sale.client, "full_name", None) if sale.client else None,
                "payment_method": sale.payment_method,
                "cash_received": fmt_money(sale.cash_received),
                "change": fmt_money(sale.change),
                "payments": _serialize_sale_payments(sale),
                "shift_id": str(sale.shift_id) if sale.shift_id else None,
                "cashbox_id": str(sale.cashbox_id) if sale.cashbox_id else None,
            }

            if print_receipt:
                payload["receipt_print_path"] = (
                    f"/api/main/pos/sales/{sale.id}/receipt/?wait_ekassa=1&receipt_text=1"
                )

            if cart.shift_id:
                remaining_qs = _shift_active_carts_qs(cart.company, request.user, cart.shift)
                ordered = list(remaining_qs)
                if ordered:
                    active_open = next((c for c in ordered if c.is_default), ordered[0])
                    active_open = get_object_or_404(
                        _cart_queryset_for_response(),
                        id=active_open.id,
                        company=request.user.company,
                    )
                    payload["active_sale_id"] = str(active_open.id)
                    payload["sale"] = _serialize_pos_sale(request, active_open)
                    payload["carts"] = [_serialize_pos_cart_tab(c, ordered) for c in ordered]

        hint = _ekassa_checkout_hint(sale.company)
        if hint:
            payload["ekassa"] = hint

        return Response(payload, status=status.HTTP_201_CREATED)


class SalePayDebtAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    """
    Оплатить ранее оформленную продажу "в долг".
    POST /api/main/pos/sales/<sale_id>/pay-debt/
    Body: { "payment_method": "cash|transfer|mbank|...", "cash_received": "..."(только для cash) }
    """

    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        qs = (
            Sale.objects.select_for_update()
            .select_related("shift", "cashbox", "client", "user")
            .prefetch_related("items")
        )
        qs = self._filter_qs_company_branch(qs)
        sale = get_object_or_404(qs, id=pk)

        if sale.status != Sale.Status.DEBT:
            raise ValidationError({"detail": "Продажа не находится в статусе 'Долг'."})

        ser = PayDebtSerializer(data=request.data, context={"sale": sale})
        ser.is_valid(raise_exception=True)

        sale.mark_paid(
            payment_method=ser.validated_data["payment_method"],
            cash_received=ser.validated_data.get("cash_received"),
        )

        sale.refresh_from_db()
        return Response(SaleDetailSerializer(sale, context={"request": request}).data, status=status.HTTP_200_OK)


def _parse_partial_return_items(data) -> Optional[List[tuple]]:
    """
    None — полный возврат чека (как без тела запроса).
    Список — частичный возврат: пары (sale_item_id UUID, quantity).
    """
    if not isinstance(data, dict):
        return None
    raw = data.get("items", None)
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValidationError({"items": 'Ожидается список объектов с полями "sale_item_id" и "quantity".'})
    if len(raw) == 0:
        return None
    merged: Dict[uuid.UUID, Decimal] = {}
    for row in raw:
        if not isinstance(row, dict):
            raise ValidationError({"items": "Каждая позиция должна быть объектом."})
        sid = row.get("sale_item_id") or row.get("id")
        if sid in (None, ""):
            raise ValidationError({"items": "Укажите sale_item_id (или id) для каждой строки."})
        try:
            uid = uuid.UUID(str(sid))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"items": f"Некорректный sale_item_id: {sid!r}."})
        try:
            q = qty3(Decimal(str(row.get("quantity"))))
        except Exception:
            raise ValidationError({"items": "Некорректное quantity."})
        if q <= 0:
            raise ValidationError({"items": "quantity должно быть > 0."})
        merged[uid] = merged.get(uid, Decimal("0")) + q
    return [(k, merged[k]) for k in sorted(merged.keys())]


def _restock_product_for_sale_item_return(item: SaleItem, return_qty: Decimal) -> None:
    if not item.product_id:
        return
    stock_delta = line_qty_consume_units(return_qty, getattr(item, "sale_package", None))
    if stock_delta <= 0:
        return
    Product.objects.filter(pk=item.product_id).update(quantity=F("quantity") + stock_delta)


def _release_agent_allocations_for_qty(sale_item: SaleItem, return_qty_int: int) -> List[tuple]:
    """
    Снимает агентские привязки (AgentSaleAllocation) по строке чека на указанное
    количество. Возвращает разбивку [(subreal_id, agent_id, take), ...] — нужна,
    чтобы зафиксировать возврат/брак по каждой партии (subreal).
    """
    if return_qty_int <= 0:
        return []
    qs = (
        AgentSaleAllocation.objects.select_for_update()
        .filter(sale_item=sale_item)
        .order_by("-id")
    )
    if not qs.exists():
        return []
    remaining = return_qty_int
    breakdown: List[tuple] = []
    for alloc in qs:
        if remaining <= 0:
            break
        take = min(int(alloc.qty), remaining)
        if take <= 0:
            continue
        new_q = int(alloc.qty) - take
        if new_q <= 0:
            AgentSaleAllocation.objects.filter(pk=alloc.pk).delete()
        else:
            AgentSaleAllocation.objects.filter(pk=alloc.pk).update(qty=new_q)
        breakdown.append((alloc.subreal_id, alloc.agent_id, take))
        remaining -= take
    if remaining > 0:
        raise ValidationError(
            {"items": "Недостаточно привязок по строке чека для агентского возврата (рассинхронизация данных)."}
        )
    return breakdown


def _sale_item_unit_net(item: SaleItem) -> Decimal:
    """Чистая цена за единицу строки чека: (unit_price*qty - line_discount) / qty."""
    q = Decimal(str(item.quantity or 0))
    if q <= 0:
        return Decimal("0.00")
    net = Decimal(str(item.unit_price or 0)) * q - Decimal(str(item.line_discount or 0))
    return net / q


def _record_agent_sale_return(*, sale: Sale, breakdown: List[tuple], unit_net: Decimal,
                              is_defect: bool, user) -> None:
    """
    Фиксирует возврат/брак агентской продажи записями ReturnFromAgent (status=accepted)
    по разбивке снятых привязок.

    - is_defect=False (обычный возврат): товар уже вернулся агенту (привязка снята,
      «на руках» вырос). Только создаём запись для аналитики.
    - is_defect=True (брак): товар списывается — дополнительно увеличиваем
      subreal.qty_returned (уходит с рук агента) и на склад НЕ возвращаем.
    """
    now = timezone.now()
    for subreal_id, agent_id, take in breakdown:
        if take <= 0:
            continue
        amount = (unit_net * Decimal(take)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if is_defect:
            ManufactureSubreal.objects.filter(pk=subreal_id).update(
                qty_returned=F("qty_returned") + take
            )
        ReturnFromAgent.objects.create(
            subreal_id=subreal_id,
            returned_by_id=agent_id or getattr(user, "id", None),
            qty=int(take),
            is_defect=is_defect,
            amount=amount,
            client_id=sale.client_id,
            status=ReturnFromAgent.Status.ACCEPTED,
            accepted_by=user,
            accepted_at=now,
        )


def _rescale_sale_payments_after_partial_return(sale: Sale, old_total: Decimal, new_total: Decimal) -> None:
    """
    После частичного возврата приводим строки оплаты (SalePayment) в соответствие
    с новой суммой чека.

    Зачем: «живой» expected_cash смены считается по сумме SalePayment.amount
    оплаченных продаж (см. CashShift.calc_live_totals). Если строки оплаты не
    уменьшать, наличная касса смены после возврата остаётся завышенной.

    Поведение:
    - наличная строка уменьшается пропорционально → expected_cash смены падает;
    - безналичная строка тоже уменьшается, но на наличную кассу не влияет;
    - инвариант: sum(SalePayment.amount) == sale.total.
    Долговые чеки строк оплаты не имеют — тогда функция ничего не делает.
    """
    if not old_total or old_total <= 0 or new_total < 0:
        return

    payments = list(sale.payments.order_by("created_at", "id"))
    if not payments:
        # legacy-чек без строк оплаты: наличные считаются по sale.total, он уже пересчитан.
        return

    scale = Decimal(new_total) / Decimal(old_total)
    residual = money(new_total)
    survivors: List[SalePayment] = []
    for p in payments:
        scaled = money(Decimal(str(p.amount or 0)) * scale)
        if scaled <= 0:
            p.delete()
            continue
        p.amount = scaled
        survivors.append(p)
        residual = money(residual - scaled)

    # Копеечную погрешность округления вешаем на самую крупную строку,
    # чтобы сумма оплат точно совпала с новой суммой чека.
    if survivors and residual != 0:
        biggest = max(survivors, key=lambda x: x.amount)
        adjusted = money(biggest.amount + residual)
        if adjusted > 0:
            biggest.amount = adjusted

    for p in survivors:
        p.save(update_fields=["amount"])


def _recalc_sale_headers_from_items(sale: Sale) -> None:
    """Пересчитать суммы шапки чека по оставшимся строкам."""
    rows = list(SaleItem.objects.filter(sale=sale).order_by("id"))
    if not rows:
        return
    d0 = Decimal("0")
    new_subtotal = money(sum((it.unit_price or d0) * Decimal(str(it.quantity or 0)) for it in rows))
    new_line_disc = money(sum(Decimal(str(it.line_discount or 0)) for it in rows))
    old_sub = sale.subtotal or d0
    old_disc = sale.discount_total or d0
    old_tax = sale.tax_total or d0
    old_total = sale.total or d0
    old_taxable = old_sub - old_disc
    new_taxable = new_subtotal - new_line_disc
    if old_taxable > 0:
        new_tax = money(old_tax * (new_taxable / old_taxable))
    elif old_taxable == 0 and new_taxable == 0:
        new_tax = old_tax
    else:
        new_tax = d0
    new_total = money(new_taxable + new_tax)
    sale.subtotal = new_subtotal
    sale.discount_total = new_line_disc
    sale.tax_total = new_tax
    sale.total = new_total
    if old_total and old_total > 0:
        cr = sale.cash_received or d0
        sale.cash_received = money(cr * (new_total / old_total))
    sale.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "cash_received"])

    # Синхронизируем строки оплаты с новой суммой чека, чтобы «живой» расчёт смены
    # (expected_cash) уменьшался симметрично частичному возврату.
    _rescale_sale_payments_after_partial_return(sale, old_total, new_total)


def _parse_is_defect(data) -> bool:
    """Флаг брака на уровне всего запроса возврата."""
    if not isinstance(data, dict):
        return False
    val = data.get("is_defect", False)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "y", "on", "да")
    return False


def _execute_sale_return(
    sale: Sale,
    partial_items: Optional[List[tuple]],
    *,
    is_defect: bool = False,
    user=None,
) -> None:
    """
    partial_items=None — полный возврат (статус canceled, весь товар на склад / снятие аллокаций).
    Иначе — частичный возврат по строкам; чек остаётся paid/debt, пока есть строки.

    is_defect=True — брак: товар списывается, на склад/к агенту не возвращается,
    фиксируется как брак (для агентских продаж — записью ReturnFromAgent).
    """
    is_agent_sale = sale.agent_allocations.exists()

    if not partial_items:
        if is_agent_sale:
            # Полный возврат агентской продажи: по каждой строке снимаем привязки
            # и фиксируем возврат/брак (для аналитики и склада агента).
            for item in sale.items.select_related("product", "sale_package"):
                rq = qty3(Decimal(str(item.quantity or 0)))
                if rq <= 0:
                    continue
                breakdown = _release_agent_allocations_for_qty(item, int(rq))
                _record_agent_sale_return(
                    sale=sale, breakdown=breakdown,
                    unit_net=_sale_item_unit_net(item),
                    is_defect=is_defect, user=user,
                )
        else:
            # Обычная касса: при браке товар списываем (на склад не возвращаем).
            if not is_defect:
                for item in sale.items.filter(product_id__isnull=False).select_related("sale_package"):
                    rq = qty3(Decimal(str(item.quantity or 0)))
                    if rq <= 0:
                        continue
                    _restock_product_for_sale_item_return(item, rq)
        sale.status = Sale.Status.CANCELED
        sale.save(update_fields=["status"])
        return

    item_ids = [uid for uid, _ in partial_items]
    found = set(SaleItem.objects.filter(sale=sale, id__in=item_ids).values_list("id", flat=True))
    missing = set(item_ids) - found
    if missing:
        raise ValidationError({"items": "Есть позиции не из этого чека или несуществующие sale_item_id."})

    for sid, rq in partial_items:
        # FOR UPDATE только по sale_item: иначе PostgreSQL ругается на nullable side of outer join
        # при select_related(product, sale_package).
        item = (
            SaleItem.objects.select_for_update(of=("self",))
            .select_related("product", "sale_package")
            .get(pk=sid, sale=sale)
        )
        old_q = qty3(Decimal(str(item.quantity or 0)))
        rq = qty3(rq)
        if rq > old_q or rq <= 0:
            raise ValidationError({"items": f"Некорректное количество возврата для позиции {sid}."})

        if is_agent_sale:
            if rq != rq.to_integral_value():
                raise ValidationError({"items": "Для агентского чека количество возврата должно быть целым."})
            unit_net = _sale_item_unit_net(item)
            breakdown = _release_agent_allocations_for_qty(item, int(rq))
            _record_agent_sale_return(
                sale=sale, breakdown=breakdown, unit_net=unit_net,
                is_defect=is_defect, user=user,
            )
        else:
            # Обычная касса: при браке товар списываем (на склад не возвращаем).
            if not is_defect:
                _restock_product_for_sale_item_return(item, rq)

        new_q = qty3(old_q - rq)
        old_disc = Decimal(str(item.line_discount or 0))
        new_disc = money(old_disc * (new_q / old_q)) if old_q > 0 else old_disc

        if new_q <= 0:
            item.delete()
        else:
            item.quantity = new_q
            item.line_discount = new_disc
            item.save(update_fields=["quantity", "line_discount"])

    if not SaleItem.objects.filter(sale=sale).exists():
        sale.status = Sale.Status.CANCELED
        sale.save(update_fields=["status"])
    else:
        _recalc_sale_headers_from_items(sale)


class SaleReturnAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    """
    Возврат продажи (владелец и агент).
    POST /api/main/pos/sales/<pk>/return/

    Отменяет оплаченную или долговую продажу:
    - для обычных продаж: возвращает товар на склад (Product.quantity)
    - для агентских продаж: удаляет AgentSaleAllocation (товар снова у агента)
    """

    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        # Важно: `select_for_update()` нельзя применять к queryset'у с `select_related`/`prefetch_related`,
        # потому что Django может сгенерировать `LEFT OUTER JOIN` на nullable связях,
        # а PostgreSQL запрещает `FOR UPDATE` на nullable стороне такого join.
        #
        # Поэтому сначала блокируем только строку Sale без join'ов,
        # а связанные объекты подтягиваем уже обычными запросами.
        locked_qs = Sale.objects.select_for_update()
        locked_qs = self._filter_qs_company_branch(locked_qs)
        sale = get_object_or_404(locked_qs, id=pk)

        if sale.status == Sale.Status.CANCELED:
            return Response(
                {"detail": "Продажа уже отменена."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sale.status not in (Sale.Status.PAID, Sale.Status.DEBT):
            return Response(
                {"detail": "Возврат возможен только для оплаченных или долговых продаж."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            partial = _parse_partial_return_items(request.data)
            is_defect = _parse_is_defect(request.data)
            _execute_sale_return(sale, partial, is_defect=is_defect, user=request.user)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        # Инвалидируем кэши аналитики/списков, чтобы цифры обновлялись сразу после возврата.
        # (market analytics кэшируется по ключам nurcrm:analytics:market:... )
        invalidate_cache_pattern(f"analytics:market:{sale.company_id}:")
        # списки товаров/остатков тоже могут быть кэшированы
        invalidate_cache_pattern(f"products:list:{sale.company_id}:")

        sale.refresh_from_db()
        return Response(
            SaleDetailSerializer(sale, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class AgentSaleReturnAPIView(SaleReturnAPIView):
    """
    Возврат продажи агента — только свои.
    POST /api/main/agents/me/sales/<pk>/return/
    """

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        # Проверка доступа без select_for_update (DISTINCT + FOR UPDATE несовместимы в PostgreSQL)
        allowed_qs = (
            Sale.objects.filter(
                Q(agent_allocations__agent=request.user) | Q(user=request.user)
            )
            .filter(id=pk)
            .distinct()
        )
        allowed_qs = self._filter_qs_company_branch(allowed_qs)
        if not allowed_qs.exists():
            raise Http404("Продажа не найдена или не принадлежит агенту.")

        # Важно: блокируем только саму Sale без join'ов, чтобы избежать
        # ошибок БД вида "FOR UPDATE cannot be applied to the nullable side of an outer join".
        sale = Sale.objects.select_for_update().get(id=pk)

        if sale.status == Sale.Status.CANCELED:
            return Response(
                {"detail": "Продажа уже отменена."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sale.status not in (Sale.Status.PAID, Sale.Status.DEBT):
            return Response(
                {"detail": "Возврат возможен только для оплаченных или долговых продаж."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            partial = _parse_partial_return_items(request.data)
            is_defect = _parse_is_defect(request.data)
            _execute_sale_return(sale, partial, is_defect=is_defect, user=request.user)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        invalidate_cache_pattern(f"analytics:market:{sale.company_id}:")
        invalidate_cache_pattern(f"products:list:{sale.company_id}:")
        sale.refresh_from_db()
        return Response(
            SaleDetailSerializer(sale, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class SaleMobileScannerTokenAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart.objects.select_related("company", "branch", "user", "shift"),
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        token = MobileScannerToken.issue(cart, ttl_minutes=10)
        return Response(MobileScannerTokenSerializer(token).data, status=201)


class ProductFindByBarcodeAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        barcode = request.query_params.get("barcode", "").strip()
        if not barcode:
            return Response([], status=200)

        try:
            product = _resolve_product_by_barcode_for_pos(
                request.user.company_id,
                barcode,
                only_fields=("id", "name", "barcode", "price"),
            )
        except AmbiguousBarcode as exc:
            return _ambiguous_barcode_response(exc)

        if not product:
            return Response([], status=200)
        
        return Response(
            [{"id": str(product.id), "name": product.name, "barcode": product.barcode, "price": str(product.price)}],
            status=200,
        )


class MobileScannerIngestAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, token, *args, **kwargs):
        barcode = request.data.get("barcode", "").strip()
        raw_qty = request.data.get("quantity", "1.000")
        qty = _to_decimal(raw_qty, default=Decimal("1.000"))
        qty = qty3(qty)
        if qty <= 0:
            return Response({"detail": "quantity must be > 0"}, status=400)

        mt = MobileScannerToken.objects.select_related("cart", "cart__company").filter(token=token).first()
        if not mt:
            return Response({"detail": "invalid token"}, status=404)
        if not mt.is_valid():
            return Response({"detail": "token expired"}, status=410)

        cart = mt.cart
        # POS доступен только для сферы Маркет
        if not _is_market_company(getattr(cart, "company", None)):
            return Response({"detail": "invalid token"}, status=404)

        # ✅ защита: нельзя сканить в уже закрытую/неактивную корзину
        if cart.status != Cart.Status.ACTIVE:
            return Response({"detail": "cart is not active"}, status=409)

        try:
            product, scale_data, lookup_error = _lookup_product_for_pos_scan(
                cart.company_id,
                barcode,
                only_fields=("id", "company_id", "price", "barcode"),
            )
        except AmbiguousBarcode as exc:
            return _ambiguous_barcode_response(exc)
        if not product:
            return Response({"not_found": True, "message": lookup_error or "Товар не найден"}, status=404)

        effective_qty = _effective_qty_from_scale_data(scale_data, qty)

        with transaction.atomic():
            cart = Cart.objects.select_for_update().get(id=cart.id)
            _upsert_scanned_cart_item(cart, product, effective_qty)
        return Response({"ok": True}, status=201)


class PosSalesLimitPagination(SupplierReceiptLimitPagination):
    page_size = 100
    max_page_size = 500

    def get_page_size(self, request):
        # Размер страницы можно задать как `limit` (историческое имя),
        # так и `page_size` (используется фильтром аналитики).
        for param in (self.page_size_query_param, "page_size"):
            raw = request.query_params.get(param)
            if raw:
                try:
                    return _positive_int(raw, strict=True, cutoff=self.max_page_size)
                except (KeyError, ValueError):
                    pass
        return self.page_size


def _parse_decimal_param(raw):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def _apply_sale_date_filters(qs, request):
    start_raw = (
        (request.query_params.get("date_from") or "").strip()
        or (request.query_params.get("start") or "").strip()
    )
    end_raw = (
        (request.query_params.get("date_to") or "").strip()
        or (request.query_params.get("end") or "").strip()
    )
    start_dt = _parse_range_dt(start_raw, end=False) if start_raw else None
    end_dt = _parse_range_dt(end_raw, end=True) if end_raw else None
    if start_dt:
        qs = qs.filter(created_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(created_at__lte=end_dt)
    return qs


def _aggregate_pos_sales_total_amount(qs, *, status_filter: str):
    if status_filter != Sale.Status.CANCELED:
        qs = qs.exclude(status=Sale.Status.CANCELED)
    total = qs.aggregate(total=Coalesce(Sum("total"), Decimal("0.00")))["total"]
    if total is None:
        return Decimal("0.00")
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class SaleListAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, generics.ListAPIView):
    serializer_class = SaleListSerializer
    queryset = (
        Sale.objects.select_related("user")
        .prefetch_related("items__product")
        .all()
    )
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ("status", "user")
    search_fields = ("id",)
    ordering_fields = ("created_at", "total", "status")
    ordering = ("-created_at",)
    pagination_class = PosSalesLimitPagination

    def get_queryset(self):
        qs = super().get_queryset()
        qs = _apply_sale_date_filters(qs, self.request)

        paid_only = self.request.query_params.get("paid")
        if paid_only in ("1", "true", "True"):
            qs = qs.filter(status=Sale.Status.PAID)

        users_param = self.request.query_params.get("users")
        if users_param:
            user_ids = [x.strip() for x in users_param.split(",") if x.strip()]
            if user_ids:
                qs = qs.filter(user_id__in=user_ids)

        client_param = (self.request.query_params.get("client") or "").strip()
        if client_param:
            qs = qs.filter(client_id=client_param)

        # Аналитический фильтр (страница /crm/market/analytics → «Транзакции»)
        payment_method = (self.request.query_params.get("payment_method") or "").strip()
        if payment_method:
            qs = qs.filter(payment_method=payment_method)

        cashbox_param = (self.request.query_params.get("cashbox") or "").strip()
        if cashbox_param:
            qs = qs.filter(cashbox_id=cashbox_param)

        cashier_param = (self.request.query_params.get("cashier") or "").strip()
        if cashier_param:
            qs = qs.filter(user_id=cashier_param)

        min_total = _parse_decimal_param(self.request.query_params.get("min_total"))
        if min_total is not None:
            qs = qs.filter(total__gte=min_total)

        max_total = _parse_decimal_param(self.request.query_params.get("max_total"))
        if max_total is not None:
            qs = qs.filter(total__lte=max_total)

        return qs

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        status_param = (request.query_params.get("status") or "").strip()
        total_amount = _aggregate_pos_sales_total_amount(queryset, status_filter=status_param)

        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.paginator.get_paginated_response(serializer.data, total_amount=total_amount)


class SaleRetrieveAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "pk"

    queryset = (
        Sale.objects.select_related("user")
        .prefetch_related("items__product")
        .all()
    )

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return SaleStatusUpdateSerializer
        return SaleDetailSerializer

    def retrieve(self, request, *args, **kwargs):
        pk = kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        user = request.user
        company = self._company() or user.company

        cart = _get_pos_open_cart_for_cashier(company=company, user=user, cart_id=pk)
        if cart:
            cart = get_object_or_404(_cart_queryset_for_response(), id=cart.id, company=company)
            return _pos_multi_cart_response(request, cart)

        return super().retrieve(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        pk = kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        user = request.user
        company = self._company() or user.company

        cart = _get_pos_open_cart_for_cashier(company=company, user=user, cart_id=pk)
        if cart:
            is_admin = getattr(request.user, "role", None) in ["owner", "admin"]
            if not is_admin and getattr(company, "cashier_password", None):
                from django.core.cache import cache
                if not cache.get(f"delete_verified_{request.user.id}"):
                    return Response({"detail": "Требуется код удаления или время проверки истекло"}, status=status.HTTP_403_FORBIDDEN)
                    
            shift = _abandon_pos_open_cart(company=company, user=user, cart=cart)
            ordered = list(_shift_active_carts_qs(company, user, shift))
            if not ordered:
                return Response(
                    {
                        "sale": None,
                        "active_sale_id": None,
                        "id": None,
                        "carts": [],
                    },
                    status=status.HTTP_200_OK,
                )
            active = next((c for c in ordered if c.is_default), ordered[0])
            active = get_object_or_404(_cart_queryset_for_response(), id=active.id, company=company)
            return _pos_multi_cart_response(request, active)

        return super().destroy(request, *args, **kwargs)


class SaleBulkDeleteAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    """
    Массовое удаление продаж.
    Поддерживает DELETE и POST (многие клиенты/прокси не передают body с DELETE).
    Body: {"ids": ["uuid", ...], "allow_paid": false}
    """
    permission_classes = [permissions.IsAuthenticated]

    def _perform_bulk_delete(self, request):
        data = request.data or {}
        ids = data.get("ids")
        allow_paid = bool(data.get("allow_paid", False))
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "Укажите непустой список 'ids'."}, status=400)

        valid_ids, invalid_ids = [], []
        for x in ids:
            try:
                valid_ids.append(uuid.UUID(str(x)))
            except Exception:
                invalid_ids.append(str(x))

        if not valid_ids and invalid_ids:
            return Response({"detail": "Нет валидных UUID.", "invalid_ids": invalid_ids}, status=400)

        base_qs = Sale.objects.filter(id__in=valid_ids)
        base_qs = self._filter_qs_company_branch(base_qs)

        paid_ids = list(base_qs.filter(status=Sale.Status.PAID).values_list("id", flat=True))
        if paid_ids and not allow_paid:
            return Response(
                {
                    "detail": "Среди переданных продаж есть оплаченные. "
                              "Если хочешь удалить их тоже, передай allow_paid=true.",
                    "paid_ids": [str(x) for x in paid_ids],
                },
                status=400,
            )

        if allow_paid:
            deletable_qs = base_qs
            not_allowed_ids = []
        else:
            deletable_qs = base_qs.exclude(status=Sale.Status.PAID)
            not_allowed_ids = []

        found_ids = set(str(sid) for sid in base_qs.values_list("id", flat=True))
        not_found_ids = [str(x) for x in valid_ids if str(x) not in found_ids]

        deleted_count, _ = deletable_qs.delete()

        return Response(
            {
                "deleted": deleted_count,
                "not_found": not_found_ids + invalid_ids,
                "not_allowed": [str(x) for x in not_allowed_ids],
            },
            status=200,
        )

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        return self._perform_bulk_delete(request)

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        return self._perform_bulk_delete(request)


class AgentMySalesListAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    История продаж агента — только свои продажи.
    GET /api/main/agents/me/sales/

    Показывает продажи, где агент имеет AgentSaleAllocation или является кассиром (user).
    """
    serializer_class = SaleListSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ("status", "user")
    search_fields = ("id",)
    ordering_fields = ("created_at", "total", "status")
    ordering = ("-created_at",)

    def get_queryset(self):
        user = self.request.user
        qs = (
            Sale.objects.filter(
                Q(agent_allocations__agent=user) | Q(user=user)
            )
            .select_related("user")
            .prefetch_related("items__product")
            .distinct()
        )
        qs = self._filter_qs_company_branch(qs)

        start_dt = _parse_range_dt(self.request.query_params.get("start"), end=False)
        end_dt = _parse_range_dt(self.request.query_params.get("end"), end=True)
        if start_dt:
            qs = qs.filter(created_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(created_at__lte=end_dt)

        paid_only = self.request.query_params.get("paid")
        if paid_only in ("1", "true", "True"):
            qs = qs.filter(status=Sale.Status.PAID)

        return qs.order_by("-created_at")


class AgentMySaleRetrieveAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, generics.RetrieveAPIView):
    """
    Детали продажи агента — только свои.
    GET /api/main/agents/me/sales/<pk>/
    """
    serializer_class = SaleDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "pk"

    def get_queryset(self):
        user = self.request.user
        qs = (
            Sale.objects.filter(
                Q(agent_allocations__agent=user) | Q(user=user)
            )
            .select_related("user")
            .prefetch_related("items__product")
            .distinct()
        )
        return self._filter_qs_company_branch(qs)




# ========================
# Cashier Settings
# ========================
class MarketCashierSettingsAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        company = request.user.company
        is_admin = getattr(request.user, "role", None) in ["owner", "admin"]

        data = {
            "delete_item_code_required": bool(company.cashier_password),
            "max_discount_percent": str(company.max_discount_percent) if company.max_discount_percent is not None else None,
        }
        
        if is_admin:
            data["delete_item_code"] = company.cashier_password
            
        return Response(data, status=status.HTTP_200_OK)

    def patch(self, request, *args, **kwargs):
        company = request.user.company
        is_admin = getattr(request.user, "role", None) in ["owner", "admin"]
        if not is_admin:
            return Response({"detail": "Недостаточно прав"}, status=status.HTTP_403_FORBIDDEN)
            
        data = request.data
        if "delete_item_code" in data:
            code = data["delete_item_code"]
            if code in (None, ""):
                company.cashier_password = None
            else:
                code_str = str(code).strip()
                if not code_str.isdigit() or not (4 <= len(code_str) <= 8):
                    return Response({"delete_item_code": ["Код должен состоять из 4–8 цифр"]}, status=status.HTTP_400_BAD_REQUEST)
                company.cashier_password = code_str

        if "max_discount_percent" in data:
            mdp = data["max_discount_percent"]
            if mdp in (None, ""):
                company.max_discount_percent = None
            else:
                try:
                    from decimal import Decimal
                    val = Decimal(str(mdp))
                    if not (0 <= val <= 100):
                        raise ValueError
                    company.max_discount_percent = val
                except (ValueError, TypeError, ArithmeticError):
                    return Response({"max_discount_percent": ["Значение должно быть от 0 до 100"]}, status=status.HTTP_400_BAD_REQUEST)

        company.save(update_fields=["cashier_password", "max_discount_percent"])
        
        resp = {
            "delete_item_code_required": bool(company.cashier_password),
            "max_discount_percent": str(company.max_discount_percent) if company.max_discount_percent is not None else None,
            "delete_item_code": company.cashier_password
        }
        return Response(resp, status=status.HTTP_200_OK)


class VerifyDeleteCodeAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        company = request.user.company
        
        if not company.cashier_password:
            return Response({"valid": True}, status=status.HTTP_200_OK)
            
        code = request.data.get("code", "")
        
        from django.core.cache import cache
        throttle_key = f"verify_delete_throttle_{request.user.id}"
        attempts = cache.get(throttle_key, 0)
        if attempts >= 10:
            return Response({"detail": "Слишком много попыток. Попробуйте позже."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        cache.set(throttle_key, attempts + 1, timeout=60)
        
        import secrets
        if secrets.compare_digest(str(code).strip(), company.cashier_password):
            cache.set(f"delete_verified_{request.user.id}", True, timeout=120)
            return Response({"valid": True}, status=status.HTTP_200_OK)
            
        return Response({"valid": False}, status=status.HTTP_200_OK)

class CartItemUpdateDestroyAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_active_cart(self, request, cart_id):
        return get_object_or_404(
            Cart,
            id=cart_id,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )

    def _get_item_in_cart(self, cart, item_or_product_id):
        item = CartItem.objects.filter(cart=cart, id=item_or_product_id).select_related("product").first()
        if item:
            return item

        item = CartItem.objects.filter(cart=cart, product_id=item_or_product_id).select_related("product").first()
        if item:
            return item

        raise Http404("CartItem not found in this cart.")

    def _apply_min_price(self, item, unit_price):
        """Цена продажи не ниже закупочной. Со скидкой (line_discount > 0) можно ниже."""
        if not item.product_id:
            return unit_price
        if Decimal(str(getattr(item, "line_discount", None) or 0)) > 0:
            return unit_price
        min_price = _q2(Decimal(str(getattr(item.product, "purchase_price", None) or 0)))
        if unit_price < min_price:
            return min_price
        return unit_price

    @transaction.atomic
    def patch(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)

        ser = CartItemPatchSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        qty = data.get("quantity")
        
        is_admin = getattr(request.user, "role", None) in ["owner", "admin"]
        
        if qty is not None:
            qty = qty3(qty)
            if qty < 0:
                return Response({"quantity": "Количество не может быть отрицательным."}, status=400)
            if qty == 0:
                if not is_admin and getattr(request.user.company, "cashier_password", None):
                    from django.core.cache import cache
                    if not cache.get(f"delete_verified_{request.user.id}"):
                        return Response({"detail": "Требуется код удаления или время проверки истекло"}, status=status.HTTP_403_FORBIDDEN)
                
                log_cart_item_deletion(item=item, deleted_by=request.user)
                item.delete()
                cart.recalc()
                return Response(SaleCartSerializer(cart).data, status=200)
            item.quantity = qty

        unit_price = data.get("unit_price")
        line_discount = data.get("discount_total")

        # Цена и скидка меняются независимо. Со скидкой можно продавать ниже закупочной.
        if unit_price is not None:
            item.unit_price = self._apply_min_price(item, _q2(unit_price))
        if line_discount is not None:
            max_dp = request.user.company.max_discount_percent
            if max_dp is not None and not is_admin:
                current_qty = qty if qty is not None else item.quantity
                current_price = item.unit_price if unit_price is None else (unit_price if not hasattr(self, '_apply_min_price') else self._apply_min_price(item, _q2(unit_price)))
                limit = (current_price * current_qty) * (max_dp / Decimal("100.0"))
                if Decimal(str(line_discount)) > limit:
                    return Response({"detail": f"Максимальная скидка — {max_dp}%", "max_discount_percent": str(max_dp)}, status=status.HTTP_400_BAD_REQUEST)
            item.line_discount = _q2(Decimal(str(line_discount)))

        update_fields = []
        if qty is not None:
            update_fields.append("quantity")
        if unit_price is not None:
            update_fields.append("unit_price")
        if line_discount is not None:
            update_fields.append("line_discount")
        if update_fields:
            item.save(update_fields=update_fields)
        cart.recalc()
        if item.pk:
            item.refresh_from_db()
        return Response(SaleCartSerializer(cart).data, status=200)

    @transaction.atomic
    def delete(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)
        is_admin = getattr(request.user, "role", None) in ["owner", "admin"]
        if not is_admin and getattr(request.user.company, "cashier_password", None):
            from django.core.cache import cache
            if not cache.get(f"delete_verified_{request.user.id}"):
                return Response({"detail": "Требуется код удаления или время проверки истекло"}, status=status.HTTP_403_FORBIDDEN)

        log_cart_item_deletion(item=item, deleted_by=request.user)
        item.delete()
        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=200)


class CartItemDeletionLogListAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, generics.ListAPIView):
    """
    GET /api/main/pos/cart-item-deletions/
    Журнал удалений позиций из корзины (товар, количество, кто удалил, время).
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CartItemDeletionLogSerializer
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = CartItemDeletionLog.objects.select_related("deleted_by", "product", "cart").all()
        return self._filter_qs_company_branch(qs)


class SaleAddCustomItemAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart.objects.select_related("company", "branch", "user", "shift")
            .prefetch_related("items"),
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = CustomCartItemCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        name = ser.validated_data["name"].strip()
        if not name:
            return Response({"name": "Название не может быть пустым."}, status=400)

        price = _q2(ser.validated_data["price"])
        qty = _to_decimal(ser.validated_data.get("quantity", "1.000"), default=Decimal("1.000"))
        qty = qty3(qty)


        item = CartItem.objects.filter(
            cart=cart,
            product__isnull=True,
            custom_name=name,
            unit_price=price,
        ).first()

        if item:
            CartItem.objects.filter(pk=item.pk).update(quantity=F("quantity") + qty)
            item.refresh_from_db(fields=["quantity"])
        else:
            CartItem.objects.create(
                company=cart.company,
                branch=getattr(cart, "branch", None),
                cart=cart,
                product=None,
                custom_name=name,
                unit_price=price,
                quantity=qty,
            )

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


OWNER_ROLES = {Roles.OWNER}


def _is_owner(user) -> bool:
    if getattr(user, "role", None) in OWNER_ROLES:
        company = getattr(user, "company", None)
        if company and getattr(company, "owner_id", None):
            return company.owner_id == user.id
        return True
    return False


def _agent_available_qty(user, company, product_id) -> int:
    sub = ManufactureSubreal.objects.filter(company=company, agent_id=user.id, product_id=product_id)
    accepted = sub.aggregate(s=Sum("qty_accepted"))["s"] or 0
    returned = sub.aggregate(s=Sum("qty_returned"))["s"] or 0
    sold = (
        AgentSaleAllocation.objects.filter(
            company=company,
            agent=user,
            product_id=product_id,
        ).aggregate(s=Sum("qty"))["s"]
        or 0
    )
    return int(accepted) - int(returned) - int(sold)


def _resolve_acting_agent(request, cart, *, allow_owner_override=True):
    user = request.user
    company = user.company

    agent_id = request.data.get("agent") or request.query_params.get("agent")
    if allow_owner_override and agent_id:
        if not _is_owner(user):
            raise ValidationError({"agent": "Только владелец может продавать за агента."})
        agent = get_object_or_404(User, id=agent_id, company=company)
        cache.set(f"cart_agent:{cart.id}", str(agent.id), timeout=60 * 60)
        return agent

    cached_id = cache.get(f"cart_agent:{cart.id}")
    if cached_id:
        try:
            return User.objects.get(id=cached_id, company=company)
        except User.DoesNotExist:
            cache.delete(f"cart_agent:{cart.id}")

    return user


def _should_use_main_stock_in_agent_sale(*, user, acting_agent) -> bool:
    # If the operator is the owner, agent selection only affects attribution,
    # while product availability must still come from the main stock.
    return _is_owner(user)


@transaction.atomic
def _allocate_agent_sale(*, company, agent, sale: Sale):
    items = sale.items.select_related("product").all()

    for item in items:
        if not item.product_id:
            continue

        qty_to_allocate = int(item.quantity or 0)
        if qty_to_allocate <= 0:
            continue

        locked_subreals = list(
            ManufactureSubreal.objects.select_for_update()
            .filter(
                company=company,
                agent_id=agent.id,
                product_id=item.product_id,
            )
            .order_by("created_at", "id")
        )

        if not locked_subreals:
            raise ValidationError(
                {
                    "detail": f"У агента нет передач по товару "
                    f"{getattr(item.product, 'name', item.product_id)}."
                }
            )

        sub_ids = [s.id for s in locked_subreals]
        sold_map = {
            row["subreal_id"]: (row["s"] or 0)
            for row in AgentSaleAllocation.objects.filter(
                company=company,
                subreal_id__in=sub_ids,
            )
            .values("subreal_id")
            .annotate(s=Sum("qty"))
        }

        total_available = 0
        avail_rows = []
        for s in locked_subreals:
            sold = int(sold_map.get(s.id, 0))
            acc = int(s.qty_accepted or 0)
            ret = int(s.qty_returned or 0)
            avail = max(acc - ret - sold, 0)
            total_available += avail
            avail_rows.append((s, avail))

        if qty_to_allocate > total_available:
            name = getattr(item.product, "name", item.product_id)
            raise ValidationError(
                {
                    "detail": f"Недостаточно на руках у агента для товара {name}. "
                    f"Нужно {qty_to_allocate}, доступно {total_available}."
                }
            )

        for s, avail in avail_rows:
            if qty_to_allocate <= 0 or avail <= 0:
                continue
            take = min(avail, qty_to_allocate)

            AgentSaleAllocation.objects.create(
                company=company,
                agent=agent,
                subreal=s,
                sale=sale,
                sale_item=item,
                product=item.product,
                qty=take,
            )
            qty_to_allocate -= take

        if qty_to_allocate > 0:
            raise ValidationError({"detail": "Внутренняя ошибка распределения остатков по передачам."})


class AgentCartStartAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        user = request.user

        company = self._company() or user.company
        branch = self._auto_branch()

        qs = Cart.objects.filter(company=company, user=user, status=Cart.Status.ACTIVE)

        if hasattr(Cart, "branch"):
            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

        qs = qs.order_by("-created_at")
        cart = qs.first()
        created = False

        if cart is None:
            create_kwargs = dict(company=company, user=user, status=Cart.Status.ACTIVE)
            if hasattr(Cart, "branch"):
                create_kwargs["branch"] = branch
            if hasattr(Cart, "is_wholesale"):
                opts0 = StartCartOptionsSerializer(data=request.data)
                if opts0.is_valid():
                    is_wholesale0 = (
                        bool(opts0.validated_data.get("is_wholesale"))
                        if "is_wholesale" in opts0.validated_data
                        else False
                    )
                    create_kwargs["is_wholesale"] = is_wholesale0
            cart = Cart.objects.create(**create_kwargs)
            created = True
        else:
            extra_ids = list(qs.values_list("id", flat=True)[1:])
            if extra_ids:
                Cart.objects.filter(id__in=extra_ids).update(
                    status=Cart.Status.CHECKED_OUT,
                )

        _ = _resolve_acting_agent(request, cart, allow_owner_override=True)

        opts = StartCartOptionsSerializer(data=request.data)
        if opts.is_valid():
            order_disc_total = opts.validated_data.get("order_discount_total")
            order_disc_percent = opts.validated_data.get("order_discount_percent")
            is_wholesale_req = (
                bool(opts.validated_data.get("is_wholesale"))
                if "is_wholesale" in opts.validated_data
                else None
            )

            if order_disc_percent is not None:
                cart.order_discount_percent = money(Decimal(str(order_disc_percent)))
                cart.order_discount_total = Decimal("0.00")
            elif order_disc_total is not None:
                cart.order_discount_percent = None
                cart.order_discount_total = money(order_disc_total)
            update_fields = []
            if order_disc_total is not None or order_disc_percent is not None:
                update_fields.extend(["order_discount_total", "order_discount_percent"])
            wholesale_changed = False
            if is_wholesale_req is not None and getattr(cart, "is_wholesale", False) != bool(is_wholesale_req):
                cart.is_wholesale = bool(is_wholesale_req)
                update_fields.append("is_wholesale")
                wholesale_changed = True
            if update_fields:
                cart.save(update_fields=update_fields)
            if created or wholesale_changed:
                _reprice_cart_items_for_mode(cart)

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class AgentSaleScanAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart.objects.select_for_update().select_related("company", "branch", "user", "shift"),
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = ScanRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        barcode = ser.validated_data["barcode"].strip()
        qty = ser.validated_data["quantity"]

        try:
            product, scale_data, lookup_error = _lookup_product_for_pos_scan(
                cart.company_id,
                barcode,
                only_fields=("id", "company_id", "price", "quantity", "barcode", "plu", "code", "is_weight"),
            )
        except AmbiguousBarcode as exc:
            return _ambiguous_barcode_response(exc)
        if not product:
            return Response({"not_found": True, "message": lookup_error or "Товар не найден"}, status=404)

        effective_qty = _effective_qty_from_scale_data(scale_data, qty)

        acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)
        use_main_stock = _should_use_main_stock_in_agent_sale(user=request.user, acting_agent=acting_agent)

        # ✅ типобезопасно: int/Decimal не смешиваем
        available = (
            Decimal(str(getattr(product, "quantity", 0) or 0))
            if use_main_stock
            else Decimal(_agent_available_qty(acting_agent, cart.company, product.id))
        )
        in_cart = _as_decimal(
            CartItem.objects.filter(cart=cart, product=product).aggregate(s=Sum("quantity"))["s"] or 0,
            default=Decimal("0"),
        )
        req = _as_decimal(effective_qty, default=Decimal("0"))

        if req + in_cart > available:
            remaining = max(Decimal("0"), available - in_cart)
            return Response(
                {
                    "detail": (
                        f"Недостаточно на основном складе. Доступно: {qty3(remaining)}."
                        if use_main_stock
                        else f"Недостаточно у агента. Доступно: {qty3(remaining)}."
                    )
                },
                status=400,
            )

        _upsert_scanned_cart_item(cart, product, effective_qty)
        cart.recalc()
        return _cart_response(request, cart.id, status_code=status.HTTP_201_CREATED)


class AgentSaleAddItemAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        product = get_object_or_404(
            Product,
            id=ser.validated_data["product_id"],
            company=cart.company,
        )
        qty = ser.validated_data["quantity"]
        allow_minus = bool(ser.validated_data.get("allow_minus"))
        can_minus = allow_minus and _is_owner_like(request.user)

        acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)
        use_main_stock = _should_use_main_stock_in_agent_sale(user=request.user, acting_agent=acting_agent)

        # Поштучная продажа из пачки — только обычная касса / checkout_cart.
        if ser.validated_data.get("sale_package_id"):
            return Response(
                {
                    "sale_package_id": (
                        "Поштучная продажа из упаковки оформляется только через обычную кассу со сменой, "
                        "не через агентскую корзину."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        unit_price = ser.validated_data.get("unit_price")
        line_discount = ser.validated_data.get("discount_total")

        base_price = (
            money(unit_price)
            if unit_price is not None
            else money(getattr(product, "price", None) or Decimal("0"))
        )
        disc_total = money(Decimal(str(line_discount))) if line_discount is not None else Decimal("0.00")

        if disc_total <= 0:
            min_price = money(Decimal(str(getattr(product, "purchase_price", None) or 0)))
            qty_dec = Decimal(str(qty))
            effective_unit = base_price - (disc_total / qty_dec) if qty_dec else base_price
            if effective_unit < min_price:
                return Response(
                    {"unit_price": f"Цена продажи не может быть ниже закупочной ({min_price})."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        cart = Cart.objects.select_for_update().get(id=cart.id)

        if use_main_stock:
            available = Decimal(str(getattr(product, "quantity", 0) or 0))
            in_cart = _as_decimal(
                CartItem.objects.filter(
                    cart=cart, product=product, sale_package__isnull=True
                ).aggregate(s=Sum("quantity"))["s"]
                or 0,
                default=Decimal("0"),
            )
            req = _as_decimal(qty, default=Decimal("0"))
            if (not can_minus) and req + in_cart > available:
                return Response(
                    {
                        "detail": (
                            f"Недостаточно на основном складе. "
                            f"Доступно: {qty3(max(Decimal('0'), available - in_cart))}."
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            available = Decimal(str(_agent_available_qty(acting_agent, cart.company, product.id)))
            in_cart = _as_decimal(
                CartItem.objects.filter(
                    cart=cart, product=product, sale_package__isnull=True
                ).aggregate(s=Sum("quantity"))["s"]
                or 0,
                default=Decimal("0"),
            )
            req = _as_decimal(qty, default=Decimal("0"))
            if (not can_minus) and req + in_cart > available:
                remaining = max(Decimal("0"), available - in_cart)
                return Response(
                    {"detail": f"Недостаточно у агента. Доступно: {qty3(remaining)}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        item = (
            CartItem.objects.select_for_update()
            .filter(cart=cart, product=product, sale_package__isnull=True)
            .first()
        )
        if item:
            item.quantity = qty3(item.quantity + qty)
            if unit_price is not None:
                item.unit_price = base_price
            if line_discount is not None:
                item.line_discount = (Decimal(str(getattr(item, "line_discount", 0) or 0)) + disc_total)
            update_f = ["quantity"]
            if unit_price is not None:
                update_f.append("unit_price")
            if line_discount is not None:
                update_f.append("line_discount")
            item.save(update_fields=update_f, skip_full_clean=True)
        else:
            item = CartItem(
                cart=cart,
                company=cart.company,
                branch=getattr(cart, "branch", None),
                product=product,
                sale_package=None,
                quantity=qty3(qty),
                unit_price=base_price,
                line_discount=disc_total,
            )
            item.save(skip_full_clean=True)

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class AgentSaleAddCustomItemAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart.objects.select_related("company", "branch", "user", "shift")
            .prefetch_related("items"),
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = CustomCartItemCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        name = ser.validated_data["name"].strip()
        if not name:
            return Response({"name": "Название не может быть пустым."}, status=400)

        price = money(ser.validated_data["price"])
        qty = ser.validated_data.get("quantity", 1)

        item = CartItem.objects.filter(
            cart=cart,
            product__isnull=True,
            custom_name=name,
            unit_price=price,
        ).first()

        if item:
            CartItem.objects.filter(pk=item.pk).update(quantity=F("quantity") + qty)
            item.refresh_from_db(fields=["quantity"])
        else:
            CartItem.objects.create(
                company=cart.company,
                branch=getattr(cart, "branch", None),
                cart=cart,
                product=None,
                custom_name=name,
                unit_price=price,
                quantity=qty,
            )

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class AgentSaleCheckoutAPIView(MarketCashierOnlyMixin, CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        with transaction.atomic():
            company = self._company() or request.user.company
            branch = self._auto_branch()

            cart_qs = Cart.objects.filter(id=pk, company=company, status=Cart.Status.ACTIVE)
            if hasattr(Cart, "branch"):
                cart_qs = cart_qs.filter(branch=branch) if branch is not None else cart_qs.filter(branch__isnull=True)
            cart = get_object_or_404(cart_qs)

            ser = AgentCheckoutSerializer(data=request.data)
            ser.is_valid(raise_exception=True)

            print_receipt = ser.validated_data["print_receipt"]
            allow_minus = bool(ser.validated_data.get("allow_minus"))
            can_minus = allow_minus and _is_owner_like(request.user)
            client_id = ser.validated_data.get("client_id")
            payment_method = ser.validated_data.get("payment_method") or Sale.PaymentMethod.CASH
            cash_received = ser.validated_data.get("cash_received") or Decimal("0.00")
            cashbox_id = ser.validated_data.get("cashbox_id")  # опционально

            resolved_client = None
            if client_id:
                resolved_client = get_object_or_404(Client, id=client_id, company=company)
                if hasattr(cart, "client_id"):
                    cart.client = resolved_client
                    cart.save(update_fields=["client"])

            acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)
            use_main_stock = _should_use_main_stock_in_agent_sale(user=request.user, acting_agent=acting_agent)

            cart.recalc()
            if payment_method == Sale.PaymentMethod.CASH and cash_received < cart.total:
                raise ValidationError({"detail": "Сумма, полученная наличными, меньше суммы продажи."})

            # ✅ ВАЖНО: checkout_agent_cart должен НЕ требовать shift
            try:
                sale = checkout_agent_cart(
                    cart,
                    agent=acting_agent,
                    use_main_stock=use_main_stock,
                    allow_negative_stock=bool(can_minus and use_main_stock),
                    cashbox_id=cashbox_id,  # можно сохранить кассу в Sale, но без смен
                    client=resolved_client,
                )
            except Exception as e:
                raise ValidationError({"detail": str(e)})

            # ✅ гарантируем, что смены нет
            if getattr(sale, "shift_id", None):
                sale.shift = None
                sale.save(update_fields=["shift"])

            pm = payment_method or Sale.PaymentMethod.CASH
            cr = cash_received
            if pm == Sale.PaymentMethod.CASH:
                if cr is None:
                    cr = sale.total
            else:
                cr = Decimal("0.00")

            if hasattr(sale, "mark_paid") and callable(sale.mark_paid):
                sale.mark_paid(payment_method=pm, cash_received=cr)
            else:
                updates = []
                if hasattr(sale, "payment_method"):
                    sale.payment_method = pm
                    updates.append("payment_method")
                if hasattr(sale, "cash_received"):
                    sale.cash_received = cr
                    updates.append("cash_received")
                if hasattr(sale, "paid_at") and not sale.paid_at:
                    sale.paid_at = timezone.now()
                    updates.append("paid_at")
                if hasattr(sale, "status") and sale.status != Sale.Status.PAID:
                    sale.status = Sale.Status.PAID
                    updates.append("status")
                if updates:
                    sale.save(update_fields=updates)

            payload = {
                "sale_id": str(sale.id),
                "status": sale.status,
                "subtotal": f"{sale.subtotal:.2f}",
                "discount_total": f"{sale.discount_total:.2f}",
                "tax_total": f"{sale.tax_total:.2f}",
                "total": f"{sale.total:.2f}",
                "client": str(sale.client_id) if sale.client_id else None,
                "client_name": getattr(sale.client, "full_name", None) if sale.client else None,
                "payment_method": getattr(sale, "payment_method", payment_method),
                "cash_received": f"{getattr(sale, 'cash_received', cash_received):.2f}",
                "change": f"{getattr(sale, 'change', Decimal('0.00')):.2f}",
                "shift_id": None,  # ✅ нет смен у агента
                "cashbox_id": str(getattr(sale, "cashbox_id", None)) if getattr(sale, "cashbox_id", None) else None,
            }

            if print_receipt:
                payload["receipt_print_path"] = (
                    f"/api/main/pos/sales/{sale.id}/receipt/?wait_ekassa=1&receipt_text=1"
                )

        hint = _ekassa_checkout_hint(sale.company)
        if hint:
            payload["ekassa"] = hint

        return Response(payload, status=status.HTTP_201_CREATED)


class AgentCartItemUpdateDestroyAPIView(MarketCashierOnlyMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_active_cart(self, request, cart_id):
        return get_object_or_404(
            Cart,
            id=cart_id,
            company=request.user.company,
            user=request.user,
            status=Cart.Status.ACTIVE,
        )

    def _get_item_in_cart(self, cart, item_or_product_id):
        item = CartItem.objects.filter(cart=cart, id=item_or_product_id).select_related("product").first()
        if item:
            return item
        item = CartItem.objects.filter(cart=cart, product_id=item_or_product_id).select_related("product").first()
        if item:
            return item
        raise Http404("CartItem not found in this cart.")

    @transaction.atomic
    def patch(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)

        ser = CartItemPatchSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        qty = data.get("quantity")
        
        is_admin = getattr(request.user, "role", None) in ["owner", "admin"]
        
        if qty is not None:
            qty = qty3(qty)
            if qty < 0:
                return Response({"quantity": "Количество не может быть отрицательным."}, status=400)
            if qty == 0:
                if not is_admin and getattr(request.user.company, "cashier_password", None):
                    from django.core.cache import cache
                    if not cache.get(f"delete_verified_{request.user.id}"):
                        return Response({"detail": "Требуется код удаления или время проверки истекло"}, status=status.HTTP_403_FORBIDDEN)
                
                log_cart_item_deletion(item=item, deleted_by=request.user)
                item.delete()
                cart.recalc()
                return Response(SaleCartSerializer(cart).data, status=200)
            item.quantity = qty

        unit_price = data.get("unit_price")
        line_discount = data.get("discount_total")

        # Цена и скидка меняются независимо.
        if unit_price is not None:
            item.unit_price = _q2(unit_price)
        if line_discount is not None:
            max_dp = request.user.company.max_discount_percent
            if max_dp is not None and not is_admin:
                current_qty = qty if qty is not None else item.quantity
                current_price = item.unit_price if unit_price is None else (unit_price if not hasattr(self, '_apply_min_price') else self._apply_min_price(item, _q2(unit_price)))
                limit = (current_price * current_qty) * (max_dp / Decimal("100.0"))
                if Decimal(str(line_discount)) > limit:
                    return Response({"detail": f"Максимальная скидка — {max_dp}%", "max_discount_percent": str(max_dp)}, status=status.HTTP_400_BAD_REQUEST)
            item.line_discount = _q2(Decimal(str(line_discount)))

        update_fields = []
        if qty is not None:
            update_fields.append("quantity")
        if unit_price is not None:
            update_fields.append("unit_price")
        if line_discount is not None:
            update_fields.append("line_discount")
        if update_fields:
            item.save(update_fields=update_fields, skip_full_clean=True)
        cart.recalc()
        if item.pk:
            item.refresh_from_db()
        return Response(SaleCartSerializer(cart).data, status=200)

    @transaction.atomic
    def delete(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)
        log_cart_item_deletion(item=item, deleted_by=request.user)
        item.delete()
        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=200)
