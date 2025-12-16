# apps/construction/views/shift_sales.py
from datetime import datetime, time

from django.db.models import Q, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.construction.models import CashShift
from apps.main.models import Sale, SaleItem
from apps.construction.sale_history_serializer import SaleHistorySerializer


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
def _get_company(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None

    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company

    br = getattr(user, "branch", None)
    if br is not None and getattr(br, "company", None):
        return br.company

    memberships = getattr(user, "branch_memberships", None)
    if memberships is not None:
        m = memberships.select_related("branch__company").first()
        if m and m.branch and m.branch.company:
            return m.branch.company

    return None


def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    if getattr(user, "owned_company", None) is not None:
        return True

    if getattr(user, "is_admin", False):
        return True

    role = getattr(user, "role", None)
    return role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор")


def assert_shift_access(user, shift: CashShift):
    if _is_owner_like(user):
        return
    if shift.cashier_id != getattr(user, "id", None):
        raise PermissionDenied("Нельзя смотреть продажи чужой смены.")


def _parse_dt_or_date(s: str, *, end_of_day: bool = False):
    """
    Принимает:
      - YYYY-MM-DD
      - ISO datetime (YYYY-MM-DDTHH:MM[:SS])
    Возвращает aware datetime (в timezone проекта) или None.
    """
    if not s:
        return None

    s = s.strip()
    try:
        if len(s) == 10:
            d = datetime.fromisoformat(s).date()
            dt = datetime.combine(d, time.max if end_of_day else time.min)
        else:
            dt = datetime.fromisoformat(s)
    except ValueError:
        return None

    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


# ─────────────────────────────────────────────────────────────
# view
# ─────────────────────────────────────────────────────────────
class CashShiftSalesListView(generics.ListAPIView):
    """
    GET /api/cash/shifts/<shift_id>/sales/

    query params:
      - status: new|paid|canceled
      - payment_method: cash|transfer
      - q: поиск по doc_number или по части uuid
      - date_from: YYYY-MM-DD или ISO datetime
      - date_to: YYYY-MM-DD или ISO datetime (если дата — включительно до конца дня)
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SaleHistorySerializer

    def get_queryset(self):
        user = self.request.user
        company = _get_company(user)
        if not company:
            return Sale.objects.none()

        shift = get_object_or_404(
            CashShift.objects.select_related("company", "cashier", "cashbox"),
            id=self.kwargs["pk"],
            company=company,
        )

        assert_shift_access(user, shift)

        # базовый qs продаж смены
        qs = (
            Sale.objects
            .filter(company=company, shift=shift)
            .select_related("cashbox", "shift", "client", "user")
            .only(
                "id", "company_id", "branch_id", "shift_id", "cashbox_id",
                "status", "doc_number",
                "payment_method", "cash_received",
                "subtotal", "discount_total", "tax_total", "total",
                "created_at", "paid_at",
                "client_id", "user_id",
            )
            .order_by("-created_at")
        )

        # items prefetch: один раз, сразу оптимальный
        item_qs = (
            SaleItem.objects
            .select_related("product")
            .only(
                "id", "sale_id", "product_id",
                "name_snapshot", "barcode_snapshot",
                "unit_price", "quantity",
            )
            .order_by("id")
        )
        qs = qs.prefetch_related(Prefetch("items", queryset=item_qs))

        # ---- filters ----
        p = self.request.query_params

        status = (p.get("status") or "").strip()
        if status:
            allowed = {Sale.Status.NEW, Sale.Status.PAID, Sale.Status.CANCELED, "new", "paid", "canceled"}
            if status not in allowed:
                raise ValidationError({"status": "Допустимо: new, paid, canceled"})
            qs = qs.filter(status=status)

        pm = (p.get("payment_method") or "").strip()
        if pm:
            allowed = {Sale.PaymentMethod.CASH, Sale.PaymentMethod.TRANSFER, "cash", "transfer"}
            if pm not in allowed:
                raise ValidationError({"payment_method": "Допустимо: cash, transfer"})
            qs = qs.filter(payment_method=pm)

        q = (p.get("q") or "").strip()
        if q:
            cond = Q()
            if q.isdigit():
                cond |= Q(doc_number=int(q))
            cond |= Q(id__icontains=q)
            qs = qs.filter(cond)

        raw_from = p.get("date_from") or ""
        raw_to = p.get("date_to") or ""

        date_from = _parse_dt_or_date(raw_from, end_of_day=False) if raw_from else None
        date_to = _parse_dt_or_date(raw_to, end_of_day=(len(raw_to.strip()) == 10)) if raw_to else None

        if raw_from and date_from is None:
            raise ValidationError({"date_from": "Неверный формат. Пример: 2025-12-16 или 2025-12-16T10:30:00"})
        if raw_to and date_to is None:
            raise ValidationError({"date_to": "Неверный формат. Пример: 2025-12-16 или 2025-12-16T10:30:00"})

        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__lte=date_to)

        return qs
