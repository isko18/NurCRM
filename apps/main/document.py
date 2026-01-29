from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from apps.main.models import Sale
from apps.main.utils_numbers import ensure_sale_doc_number

Q2 = Decimal("0.01")      # деньги
QTY = Decimal("0.001")    # количество (3 знака)

def q2(x):
    return Decimal(str(x or "0")).quantize(Q2, rounding=ROUND_HALF_UP)

def q_qty(x):
    return Decimal(str(x or "0")).quantize(QTY, rounding=ROUND_HALF_UP)

def fmt_decimal(d: Decimal) -> str:
    # 1.000 -> "1", 0.500 -> "0.5", 2.125 -> "2.125"
    s = format(d, "f")
    return s.rstrip("0").rstrip(".")

def safe_str(v, dash=None):
    s = "" if v is None else str(v).strip()
    return s if s else dash

def _enforce_market_cashier(request):
    """
    Документы продажи (чек/накладная) — часть интерфейса кассира.
    Разрешаем только пользователям с доступом кассира.
    """
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return
    if getattr(user, "is_superuser", False):
        return

    role = getattr(user, "role", None)
    if not (
        getattr(user, "can_view_cashier", False)
        or role in ("owner", "admin")
        or getattr(user, "is_staff", False)
    ):
        raise PermissionDenied("Нет доступа к интерфейсу кассира.")


class SaleReceiptAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        _enforce_market_cashier(request)
        sale = get_object_or_404(
            Sale.objects.select_related("company", "user", "client").prefetch_related("items"),
            id=pk,
            company=request.user.company,
        )

        doc_no = ensure_sale_doc_number(sale)
        dt = timezone.localtime(sale.created_at) if sale.created_at else timezone.localtime()

        company = sale.company
        client = sale.client

        items = []
        for it in sale.items.all():
            qty = q_qty(getattr(it, "quantity", 0))
            unit_price = q2(getattr(it, "unit_price", 0))
            total = q2(unit_price * qty)

            items.append(
                {
                    "id": str(it.id),
                    "product_id": str(it.product_id) if getattr(it, "product_id", None) else None,
                    "name": safe_str(
                        getattr(it, "name_snapshot", None) or getattr(it, "custom_name", None),
                        dash="",
                    ),
                    "qty": fmt_decimal(qty),
                    "unit_price": fmt_decimal(unit_price),
                    "total": fmt_decimal(total),
                }
            )

        payload = {
            "sale": {
                "id": str(sale.id),
                "doc_no": str(doc_no),
                "created_at": dt.isoformat(),
                "status": getattr(sale, "status", None),
            },
            "company": {
                "id": str(company.id),
                "name": safe_str(getattr(company, "llc", None) or getattr(company, "name", None), dash=""),
                "inn": safe_str(getattr(company, "inn", None)),
                "address": safe_str(getattr(company, "address", None)),
                "phone": safe_str(getattr(company, "phone", None)),
            },
            "cashier": {
                "id": str(sale.user_id) if getattr(sale, "user_id", None) else None,
                "name": safe_str(
                    getattr(sale.user, "full_name", None),
                    dash=safe_str(getattr(sale.user, "username", None), dash=""),
                )
                if getattr(sale, "user", None)
                else "",
            },
            "client": (
                {
                    "id": str(client.id),
                    "name": safe_str(
                        getattr(client, "llc", None)
                        or getattr(client, "enterprise", None)
                        or getattr(client, "full_name", None),
                        dash="",
                    ),
                    "phone": safe_str(getattr(client, "phone", None)),
                }
                if client
                else None
            ),
            "items": items,
            "totals": {
                "subtotal": fmt_decimal(q2(getattr(sale, "subtotal", 0))),
                "discount_total": fmt_decimal(q2(getattr(sale, "discount_total", 0))),
                "tax_total": fmt_decimal(q2(getattr(sale, "tax_total", 0))),
                "total": fmt_decimal(q2(getattr(sale, "total", 0))),
            },
            "payment": {
                "method": getattr(sale, "payment_method", None),
                "cash_received": fmt_decimal(q2(getattr(sale, "cash_received", 0))),
                "change": fmt_decimal(q2(getattr(sale, "change", 0))),
                "paid_at": timezone.localtime(sale.paid_at).isoformat() if getattr(sale, "paid_at", None) else None,
            },
        }

        return Response(payload, status=200)


class SaleInvoiceAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        _enforce_market_cashier(request)
        sale = get_object_or_404(
            Sale.objects.select_related("company", "user", "client").prefetch_related("items"),
            id=pk,
            company=request.user.company,
        )

        doc_no = ensure_sale_doc_number(sale)
        dt = timezone.localtime(sale.created_at) if sale.created_at else timezone.localtime()

        company = sale.company
        client = sale.client

        items = []
        for it in sale.items.all():
            qty = q_qty(getattr(it, "quantity", 0))
            unit_price = q2(getattr(it, "unit_price", 0))
            line_total = q2(unit_price * qty)

            items.append(
                {
                    "id": str(it.id),
                    "product_id": str(it.product_id) if getattr(it, "product_id", None) else None,
                    "name": safe_str(
                        getattr(it, "name_snapshot", None) or getattr(it, "custom_name", None),
                        dash="",
                    ),
                    "qty": fmt_decimal(qty),
                    "unit_price": fmt_decimal(unit_price),
                    "total": fmt_decimal(line_total),
                }
            )

        payload = {
            "document": {
                "type": "sale_invoice",
                "title": "Накладная",
                "id": str(sale.id),
                "number": str(doc_no),
                "date": dt.date().isoformat(),
                "datetime": dt.isoformat(),
            },
            "seller": {
                "id": str(company.id),
                "name": safe_str(getattr(company, "llc", None) or getattr(company, "name", None), dash=""),
                "inn": safe_str(getattr(company, "inn", None)),
                "okpo": safe_str(getattr(company, "okpo", None)),
                "score": safe_str(getattr(company, "score", None)),
                "bik": safe_str(getattr(company, "bik", None)),
                "address": safe_str(getattr(company, "address", None)),
                "phone": safe_str(getattr(company, "phone", None)),
                "email": safe_str(getattr(company, "email", None)),
            },
            "buyer": (
                {
                    "id": str(client.id),
                    "name": safe_str(
                        getattr(client, "llc", None)
                        or getattr(client, "enterprise", None)
                        or getattr(client, "full_name", None),
                        dash="",
                    ),
                    "inn": safe_str(getattr(client, "inn", None)),
                    "okpo": safe_str(getattr(client, "okpo", None)),
                    "score": safe_str(getattr(client, "score", None)),
                    "bik": safe_str(getattr(client, "bik", None)),
                    "address": safe_str(getattr(client, "address", None)),
                    "phone": safe_str(getattr(client, "phone", None)),
                    "email": safe_str(getattr(client, "email", None)),
                }
                if client
                else None
            ),
            "items": items,
            "totals": {
                "subtotal": fmt_decimal(q2(getattr(sale, "subtotal", 0))),
                "discount_total": fmt_decimal(q2(getattr(sale, "discount_total", 0))),
                "tax_total": fmt_decimal(q2(getattr(sale, "tax_total", 0))),
                "total": fmt_decimal(q2(getattr(sale, "total", 0))),
            },
            "meta": {
                "cashier_id": str(sale.user_id) if getattr(sale, "user_id", None) else None,
                "cashier_name": safe_str(
                    getattr(sale.user, "full_name", None),
                    dash=safe_str(getattr(sale.user, "username", None), dash=""),
                )
                if getattr(sale, "user", None)
                else "",
            },
        }

        return Response(payload, status=200)
