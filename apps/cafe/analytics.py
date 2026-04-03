# apps/cafe/views/analytics.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from io import BytesIO
from datetime import datetime

from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import BaseRenderer, JSONRenderer

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.db.models import (
    Q, Count, Avg, Sum, F,
    ExpressionWrapper, DurationField, DecimalField, Value,
)
from django.db.models.functions import Coalesce

from apps.cafe.models import (
    KitchenTask, OrderItem, Purchase, Warehouse, Order,
    CafeExpense, CafeWaiterPayProfile,
)
from apps.cafe.views import CompanyBranchQuerysetMixin
from openpyxl import Workbook


class _BinaryExcelRenderer(BaseRenderer):
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    format = "excel"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            return data.encode("utf-8")
        return json.dumps(data, ensure_ascii=False).encode("utf-8")


class _BinaryWordRenderer(BaseRenderer):
    media_type = "application/msword"
    format = "word"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            return data.encode("utf-8")
        return json.dumps(data, ensure_ascii=False).encode("utf-8")


# ==========================
# helpers (numbers)
# ==========================
def _to_decimal(x) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    s = str(x).strip().replace(",", ".")
    if s == "":
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def _apply_date_range(qs, field_name: str, date_from: str | None, date_to: str | None):
    if date_from:
        qs = qs.filter(**{f"{field_name}__date__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{field_name}__date__lte": date_to})
    return qs


def _paid_order_lines_qs(company, branch):
    """Оплаченные заказы: выручка по факту оплаты, без отказов гостя."""
    qs = OrderItem.objects.select_related(
        "order", "menu_item", "menu_item__category", "menu_item__kitchen",
    ).filter(
        order__company=company,
        order__is_paid=True,
        is_rejected=False,
    )
    if branch is not None:
        qs = qs.filter(order__branch=branch)
    else:
        qs = qs.filter(order__branch__isnull=True)
    return qs


def _line_revenue_expr():
    return ExpressionWrapper(
        F("quantity")
        * Coalesce(
            F("unit_price"),
            F("menu_item__price"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _order_final_amount_expr():
    return ExpressionWrapper(
        F("total_amount") - F("discount_amount"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _apply_branch_scope_for_kitchen_tasks(qs, mixin: CompanyBranchQuerysetMixin):
    """
    Оставляем твою старую логику для kitchen analytics:
      - active_branch -> (branch=active_branch OR branch is null)
      - no branch -> only branch is null
    """
    b = mixin._active_branch()
    if b is not None:
        return qs.filter(Q(branch=b) | Q(branch__isnull=True))
    return qs.filter(branch__isnull=True)


# ==========================
# helpers (cache)
# ==========================
def _json_stable(obj) -> str:
    """
    Стабильная сериализация для ключа.
    """
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        return str(obj)


def _cache_key(prefix: str, *, company_id: str, branch_id: str | None, params: dict) -> str:
    """
    Итоговый ключ:
      nurcrm:cafe:analytics:<prefix>:<company>:<branch|global>:<md5(params)>
    settings.KEY_PREFIX уже nurcrm, но мы не полагаемся на него — делаем ключ явным.
    """
    base = {
        "company_id": company_id,
        "branch_id": branch_id or "global",
        "params": params,
    }
    raw = _json_stable(base)
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return f"nurcrm:cafe:analytics:{prefix}:{company_id}:{branch_id or 'global'}:{h}"


def _cache_get(key: str):
    try:
        return cache.get(key)
    except Exception:
        return None


def _cache_set(key: str, value, ttl: int):
    try:
        cache.set(key, value, ttl)
    except Exception:
        # IGNORE_EXCEPTIONS=True -> Redis может лежать, не валим API
        pass


def _analytics_ttl() -> int:
    return int(getattr(settings, "CACHE_TIMEOUT_ANALYTICS", getattr(settings, "CACHE_TIMEOUT_MEDIUM", 300)))


# ==========================
# KITCHEN ANALYTICS
# ==========================
class KitchenAnalyticsBaseView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    group_field = None  # 'cook' или 'waiter'

    def get(self, request):
        if not self.group_field:
            return Response({"detail": "group_field not set"}, status=500)

        company = self._user_company()
        if not company:
            return Response([])

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")

        branch = self._active_branch()
        key = _cache_key(
            f"kitchen:{self.group_field}",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={"date_from": df, "date_to": dt},
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = KitchenTask.objects.filter(company=company)
        qs = _apply_branch_scope_for_kitchen_tasks(qs, self)
        qs = _apply_date_range(qs, "created_at", df, dt)

        lead_time = ExpressionWrapper(F("finished_at") - F("started_at"), output_field=DurationField())

        data = (
            qs.values(self.group_field)
            .annotate(
                total=Count("id"),
                taken=Count(
                    "id",
                    filter=Q(status__in=[KitchenTask.Status.IN_PROGRESS, KitchenTask.Status.READY]),
                ),
                ready=Count("id", filter=Q(status=KitchenTask.Status.READY)),
                avg_lead=Avg(lead_time, filter=Q(status=KitchenTask.Status.READY)),
            )
            .order_by("-ready", "-total")
        )

        result = []
        for row in data:
            avg = row["avg_lead"]
            result.append(
                {
                    self.group_field: row[self.group_field],
                    "total": int(row["total"] or 0),
                    "taken": int(row["taken"] or 0),
                    "ready": int(row["ready"] or 0),
                    "avg_lead_seconds": (avg.total_seconds() if avg else None),
                }
            )

        _cache_set(key, result, _analytics_ttl())
        return Response(result)


class KitchenAnalyticsByCookView(KitchenAnalyticsBaseView):
    group_field = "cook"


class KitchenAnalyticsByWaiterView(KitchenAnalyticsBaseView):
    group_field = "waiter"


# ==========================
# SALES ANALYTICS
# ==========================
class SalesSummaryView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"orders_count": 0, "items_qty": 0, "revenue": "0.00"})

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")

        branch = self._active_branch()
        key = _cache_key(
            "sales:summary",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={"date_from": df, "date_to": dt},
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = _paid_order_lines_qs(company, branch)
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        line_total = _line_revenue_expr()

        agg = qs.aggregate(
            orders_count=Count("order_id", distinct=True),
            items_qty=Sum("quantity"),
            revenue=Sum(line_total),
        )

        revenue = _to_decimal(agg.get("revenue"))
        payload = {
            "date_from": df,
            "date_to": dt,
            "basis": "paid_at",
            "orders_count": int(agg.get("orders_count") or 0),
            "items_qty": int(agg.get("items_qty") or 0),
            "revenue": f"{revenue:.2f}",
        }

        _cache_set(key, payload, _analytics_ttl())
        return Response(payload)


class SalesByMenuItemView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        limit_raw = request.query_params.get("limit")
        try:
            limit = max(1, min(int(limit_raw or 10), 200))
        except Exception:
            limit = 10

        branch = self._active_branch()
        key = _cache_key(
            "sales:items",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={"date_from": df, "date_to": dt, "limit": limit},
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = _paid_order_lines_qs(company, branch).filter(
            line_kind=OrderItem.LineKind.MENU,
            menu_item_id__isnull=False,
        )
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        line_total = _line_revenue_expr()

        data = (qs.values("menu_item_id", "menu_item__title")
                  .annotate(qty=Sum("quantity"), revenue=Sum(line_total))
                  .order_by("-revenue", "-qty")[:limit])

        result = []
        for row in data:
            result.append({
                "menu_item_id": row["menu_item_id"],
                "title": row["menu_item__title"],
                "qty": int(row["qty"] or 0),
                "revenue": f"{_to_decimal(row['revenue']):.2f}",
            })

        _cache_set(key, result, _analytics_ttl())
        return Response(result)


class SalesByCategoryView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        limit_raw = request.query_params.get("limit")
        try:
            limit = max(1, min(int(limit_raw or 50), 200))
        except Exception:
            limit = 50

        branch = self._active_branch()
        key = _cache_key(
            "sales:categories",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={"date_from": df, "date_to": dt, "limit": limit},
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = _paid_order_lines_qs(company, branch).filter(
            line_kind=OrderItem.LineKind.MENU,
            menu_item_id__isnull=False,
        )
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        line_total = _line_revenue_expr()

        data = (qs.values("menu_item__category_id", "menu_item__category__title")
                  .annotate(qty=Sum("quantity"), revenue=Sum(line_total))
                  .order_by("-revenue", "-qty")[:limit])

        result = []
        for row in data:
            cid = row["menu_item__category_id"]
            result.append({
                "category_id": str(cid) if cid is not None else None,
                "title": row["menu_item__category__title"] or "",
                "qty": int(row["qty"] or 0),
                "revenue": f"{_to_decimal(row['revenue']):.2f}",
            })

        _cache_set(key, result, _analytics_ttl())
        return Response(result)


class SalesByKitchenView(CompanyBranchQuerysetMixin, APIView):
    """Выручка по кухням (из MenuItem.kitchen), только оплаченные заказы без отказов."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        branch = self._active_branch()
        key = _cache_key(
            "sales:kitchens",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={"date_from": df, "date_to": dt},
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = _paid_order_lines_qs(company, branch).filter(
            line_kind=OrderItem.LineKind.MENU,
            menu_item_id__isnull=False,
        )
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        line_total = _line_revenue_expr()

        data = (
            qs.values("menu_item__kitchen_id", "menu_item__kitchen__title", "menu_item__kitchen__number")
            .annotate(qty=Sum("quantity"), revenue=Sum(line_total))
            .order_by("-revenue", "-qty")
        )

        result = []
        for row in data:
            kid = row["menu_item__kitchen_id"]
            result.append({
                "kitchen_id": str(kid) if kid else None,
                "title": row["menu_item__kitchen__title"] or "—",
                "number": row["menu_item__kitchen__number"],
                "qty": int(row["qty"] or 0),
                "revenue": f"{_to_decimal(row['revenue']):.2f}",
            })

        _cache_set(key, result, _analytics_ttl())
        return Response(result)


class RevenueInflowView(CompanyBranchQuerysetMixin, APIView):
    """
    Приход по способам оплаты (как сводка маркета): только полностью оплаченные заказы, по дате paid_at.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"basis": "paid_at", "payment_methods": [], "totals": {}})

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        branch = self._active_branch()

        qs = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        qs = _apply_date_range(qs, "paid_at", df, dt)

        final_expr = _order_final_amount_expr()
        rows = (
            qs.annotate(final_amount=final_expr)
            .values("payment_method")
            .annotate(count=Count("id"), total=Sum("final_amount"))
            .order_by("-total")
        )

        methods = []
        grand = Decimal("0")
        for row in rows:
            m = (row.get("payment_method") or "").strip() or "unknown"
            t = _to_decimal(row.get("total"))
            grand += t
            methods.append({
                "method": m,
                "method_label": dict(Order.PaymentMethod.choices).get(m, m),
                "count": int(row.get("count") or 0),
                "total": f"{t:.2f}",
            })

        return Response({
            "date_from": df,
            "date_to": dt,
            "basis": "paid_at",
            "payment_methods": methods,
            "grand_total": f"{grand:.2f}",
        })


class RejectionsAnalyticsView(CompanyBranchQuerysetMixin, APIView):
    """Отказы гостя: количество и суммы (по цене на момент отказа не пересчитываем — считаем потенциальную выручку)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        branch = self._active_branch()

        qs = OrderItem.objects.select_related("order", "menu_item").filter(
            company=company,
            is_rejected=True,
        )
        if branch is not None:
            qs = qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
        else:
            qs = qs.filter(order__branch__isnull=True)
        qs = _apply_date_range(qs, "rejected_at", df, dt)

        line_total = _line_revenue_expr()
        by_reason = (
            qs.values("rejection_reason")
            .annotate(qty=Sum("quantity"), lost_revenue=Sum(line_total))
            .order_by("-lost_revenue")[:200]
        )

        return Response([
            {
                "rejection_reason": (row["rejection_reason"] or "").strip() or "—",
                "qty": int(row["qty"] or 0),
                "lost_revenue": f"{_to_decimal(row['lost_revenue']):.2f}",
            }
            for row in by_reason
        ])


class CafeExpensesSummaryView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"expenses_count": 0, "expenses_sum": "0.00"})

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        branch = self._active_branch()

        qs = CafeExpense.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            qs = qs.filter(branch__isnull=True)
        qs = _apply_date_range(qs, "expense_date", df, dt)

        agg = qs.aggregate(c=Count("id"), s=Sum("amount"))
        return Response({
            "date_from": df,
            "date_to": dt,
            "expenses_count": int(agg.get("c") or 0),
            "expenses_sum": f"{_to_decimal(agg.get('s')):.2f}",
        })


class CafeDebtAnalyticsView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"open_debt_orders": 0, "balance_due_total": "0.00", "rows": []})

        branch = self._active_branch()
        qs = (
            Order.objects.filter(company=company, is_paid=False)
            .exclude(status=Order.Status.CANCELLED)
            .select_related("client", "table", "waiter")
        )
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)

        rows_out = []
        total_due = Decimal("0")
        for o in qs.order_by("-created_at")[:500]:
            due = o.balance_due
            if due <= 0:
                continue
            total_due += due
            rows_out.append({
                "order_id": str(o.id),
                "created_at": o.created_at,
                "client_id": str(o.client_id) if o.client_id else None,
                "table_id": str(o.table_id) if o.table_id else None,
                "waiter_id": str(o.waiter_id) if o.waiter_id else None,
                "final_amount": str(o.final_amount),
                "paid_amount": str(o.paid_amount or Decimal("0")),
                "balance_due": str(due),
                "payment_method": o.payment_method,
            })

        return Response({
            "open_debt_orders": len(rows_out),
            "balance_due_total": f"{total_due:.2f}",
            "rows": rows_out,
        })


class CafeShiftReportView(CompanyBranchQuerysetMixin, APIView):
    """Сводка по кафе для кассовой смены: заказы с привязкой cash_shift_id."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from apps.construction.models import CashShift

        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=403)

        shift_id = request.query_params.get("shift")
        if not shift_id:
            return Response({"detail": "Укажите query-параметр shift=<uuid>."}, status=400)

        shift = CashShift.objects.filter(pk=shift_id, company=company).first()
        if not shift:
            return Response({"detail": "Смена не найдена."}, status=404)

        branch = self._active_branch()
        qs = Order.objects.filter(company=company, cash_shift_id=shift.id, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)

        final_expr = _order_final_amount_expr()
        by_pm = (
            qs.annotate(fa=final_expr)
            .values("payment_method")
            .annotate(count=Count("id"), total=Sum("fa"))
        )

        methods = []
        g = Decimal("0")
        for row in by_pm:
            m = (row.get("payment_method") or "").strip() or "unknown"
            t = _to_decimal(row.get("total"))
            g += t
            methods.append({
                "method": m,
                "method_label": dict(Order.PaymentMethod.choices).get(m, m),
                "count": int(row.get("count") or 0),
                "total": f"{t:.2f}",
            })

        return Response({
            "shift_id": str(shift.id),
            "shift_status": shift.status,
            "opened_at": shift.opened_at,
            "closed_at": shift.closed_at,
            "cafe_orders_paid": qs.count(),
            "revenue_total": f"{g:.2f}",
            "by_payment_method": methods,
        })


class CafeDailyCloseReportView(CompanyBranchQuerysetMixin, APIView):
    """Ежедневный отчёт: оплаченные заказы кафе за календарную дату (paid_at)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=403)

        day = request.query_params.get("date")
        if not day:
            return Response({"detail": "Укажите date=YYYY-MM-DD."}, status=400)

        branch = self._active_branch()
        qs = Order.objects.filter(company=company, is_paid=True, paid_at__date=day)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)

        final_expr = _order_final_amount_expr()
        by_pm = (
            qs.annotate(fa=final_expr)
            .values("payment_method")
            .annotate(count=Count("id"), total=Sum("fa"))
        )
        methods = []
        g = Decimal("0")
        for row in by_pm:
            m = (row.get("payment_method") or "").strip() or "unknown"
            t = _to_decimal(row.get("total"))
            g += t
            methods.append({
                "method": m,
                "method_label": dict(Order.PaymentMethod.choices).get(m, m),
                "count": int(row.get("count") or 0),
                "total": f"{t:.2f}",
            })

        return Response({
            "date": day,
            "orders_count": qs.count(),
            "revenue_total": f"{g:.2f}",
            "by_payment_method": methods,
        })


class CafeWaiterSalaryReportView(CompanyBranchQuerysetMixin, APIView):
    """
    Расчёт: пропорциональный оклад за период (monthly_base * days / 30) + revenue_percent% от выручки
    по заказам официанта (оплачено, paid_at, итог после скидки).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        if not company:
            return Response({"date_from": df, "date_to": dt, "rows": []})

        if not df or not dt:
            return Response({"detail": "Нужны date_from и date_to (YYYY-MM-DD)."}, status=400)

        branch = self._active_branch()
        from datetime import date as date_cls

        try:
            d0 = date_cls.fromisoformat(df)
            d1 = date_cls.fromisoformat(dt)
        except ValueError:
            return Response({"detail": "Неверный формат даты."}, status=400)

        days = (d1 - d0).days + 1
        if days < 1:
            return Response({"detail": "date_to раньше date_from."}, status=400)

        prof_qs = CafeWaiterPayProfile.objects.filter(company=company)
        if branch is not None:
            prof_qs = prof_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            prof_qs = prof_qs.filter(branch__isnull=True)

        final_expr = _order_final_amount_expr()
        out = []
        effective_profiles = {}
        for prof in prof_qs.select_related("user").order_by("user_id", "-branch_id"):
            # Для филиала предпочитаем branch-specific профиль над глобальным.
            if prof.user_id not in effective_profiles or prof.branch_id is not None:
                effective_profiles[prof.user_id] = prof

        for prof in effective_profiles.values():
            oq = Order.objects.filter(
                company=company,
                waiter_id=prof.user_id,
                is_paid=True,
            )
            if branch is not None:
                oq = oq.filter(branch=branch)
            else:
                oq = oq.filter(branch__isnull=True)
            oq = _apply_date_range(oq, "paid_at", df, dt)
            agg = oq.aggregate(s=Sum(final_expr))
            waiter_rev = _to_decimal(agg.get("s"))
            base_part = (prof.monthly_base_salary or Decimal("0")) * Decimal(days) / Decimal("30")
            pct = (prof.revenue_percent or Decimal("0")) / Decimal("100")
            bonus = (waiter_rev * pct).quantize(Decimal("0.01"))
            total_pay = (base_part + bonus).quantize(Decimal("0.01"))
            user = prof.user
            waiter_label = (
                getattr(user, "get_full_name", lambda: "")() or getattr(user, "email", "") or str(prof.user_id)
            )
            out.append({
                "user_id": str(prof.user_id),
                "waiter_label": waiter_label,
                "profile_scope": "branch" if prof.branch_id else "global",
                "monthly_base_salary": str(prof.monthly_base_salary),
                "revenue_percent": str(prof.revenue_percent),
                "period_days": days,
                "base_prorated": f"{base_part.quantize(Decimal('0.01')):.2f}",
                "waiter_revenue_period": f"{waiter_rev:.2f}",
                "percent_bonus": f"{bonus:.2f}",
                "total": f"{total_pay:.2f}",
            })

        return Response({"date_from": df, "date_to": dt, "rows": out})


class CafeUnifiedAnalyticsView(CompanyBranchQuerysetMixin, APIView):
    """
    Единая точка аналитики кафе (по аналогии с маркетом): tab=revenue|dishes|kitchens|waiters|...
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        tab = (request.query_params.get("tab") or "revenue").strip().lower()
        company = self._user_company()
        if not company:
            return Response({"tab": tab, "detail": "Компания не найдена."}, status=403)

        http_req = getattr(request, "_request", request)

        if tab == "revenue":
            return RevenueInflowView.as_view()(http_req)
        if tab == "dishes":
            return SalesByMenuItemView.as_view()(http_req)
        if tab == "kitchens":
            return SalesByKitchenView.as_view()(http_req)
        if tab == "waiters":
            return CafeWaiterSalesView.as_view()(http_req)
        if tab == "sales_summary":
            return SalesSummaryView.as_view()(http_req)
        if tab == "categories":
            return SalesByCategoryView.as_view()(http_req)
        if tab == "purchases":
            return PurchasesSummaryView.as_view()(http_req)
        if tab == "expenses":
            return CafeExpensesSummaryView.as_view()(http_req)
        if tab == "debts":
            return CafeDebtAnalyticsView.as_view()(http_req)
        if tab == "rejections":
            return RejectionsAnalyticsView.as_view()(http_req)
        if tab == "salary":
            return CafeWaiterSalaryReportView.as_view()(http_req)
        if tab == "daily_close":
            return CafeDailyCloseReportView.as_view()(http_req)
        if tab == "shift":
            return CafeShiftReportView.as_view()(http_req)

        return Response({"detail": f"Неизвестный tab={tab}"}, status=400)


class CafeWaiterSalesView(CompanyBranchQuerysetMixin, APIView):
    """Выручка по официантам (оплаченные заказы, итог после скидки, paid_at)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        branch = self._active_branch()

        qs = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        qs = _apply_date_range(qs, "paid_at", df, dt)

        final_expr = _order_final_amount_expr()
        data = (
            qs.values("waiter_id", "waiter__first_name", "waiter__last_name", "waiter__email")
            .annotate(orders_count=Count("id"), revenue=Sum(final_expr))
            .order_by("-revenue")
        )

        result = []
        for row in data:
            wid = row["waiter_id"]
            full_name = " ".join(
                part for part in [row.get("waiter__first_name") or "", row.get("waiter__last_name") or ""] if part
            ).strip()
            result.append({
                "waiter_id": str(wid) if wid else None,
                "waiter_label": full_name or (row.get("waiter__email") or "") or (str(wid) if wid else "—"),
                "orders_count": int(row["orders_count"] or 0),
                "revenue": f"{_to_decimal(row['revenue']):.2f}",
            })
        return Response(result)


# ==========================
# PURCHASES ANALYTICS (created_at exists now)
# ==========================
class PurchasesSummaryView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"purchases_count": 0, "purchases_sum": "0.00"})

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")

        branch = self._active_branch()
        key = _cache_key(
            "purchases:summary",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={"date_from": df, "date_to": dt},
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = Purchase.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)

        qs = _apply_date_range(qs, "created_at", df, dt)

        agg = qs.aggregate(
            purchases_count=Count("id"),
            purchases_sum=Sum("price"),
        )

        payload = {
            "date_from": df,
            "date_to": dt,
            "purchases_count": int(agg.get("purchases_count") or 0),
            "purchases_sum": f"{_to_decimal(agg.get('purchases_sum')):.2f}",
        }

        _cache_set(key, payload, _analytics_ttl())
        return Response(payload)


class PurchasesBySupplierView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        limit_raw = request.query_params.get("limit")
        try:
            limit = max(1, min(int(limit_raw or 10), 200))
        except Exception:
            limit = 10

        branch = self._active_branch()
        key = _cache_key(
            "purchases:suppliers",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={"date_from": df, "date_to": dt, "limit": limit},
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = Purchase.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)

        qs = _apply_date_range(qs, "created_at", df, dt)

        data = (qs.values("supplier")
                  .annotate(total=Sum("price"), count=Count("id"))
                  .order_by("-total", "-count")[:limit])

        result = []
        for row in data:
            result.append({
                "supplier": row["supplier"],
                "count": int(row["count"] or 0),
                "total": f"{_to_decimal(row['total']):.2f}",
            })

        _cache_set(key, result, _analytics_ttl())
        return Response(result)


# ==========================
# WAREHOUSE ANALYTICS
# ==========================
class WarehouseLowStockView(CompanyBranchQuerysetMixin, APIView):
    """
    Позиции склада ниже минимума.
    remainder/minimum CharField -> сравнение python-side.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        branch = self._active_branch()
        key = _cache_key(
            "warehouse:low-stock",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={},  # тут нет параметров
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = Warehouse.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)

        out = []
        for w in qs.only("id", "title", "supplier", "unit", "remainder", "minimum"):
            rem = _to_decimal(w.remainder)
            mn = _to_decimal(w.minimum)
            if mn > 0 and rem < mn:
                out.append({
                    "id": str(w.id),
                    "title": w.title,
                    "supplier": str(w.supplier or ""),
                    "unit": w.unit,
                    "remainder": str(w.remainder or ""),
                    "minimum": str(w.minimum or ""),
                })

        # сортируем “самые проблемные сверху”
        out.sort(key=lambda x: (_to_decimal(x["remainder"]) - _to_decimal(x["minimum"])))

        _cache_set(key, out, _analytics_ttl())
        return Response(out)


def _safe_filename_part(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw)
    return cleaned[:48] or fallback


class CafeAnalyticsExportView(CompanyBranchQuerysetMixin, APIView):
    """
    Экспорт аналитики/кассы:
      GET /cafe/analytics/export/?report=analytics|cash&format=excel|word&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    """
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer, _BinaryExcelRenderer, _BinaryWordRenderer]

    def _analytics_payload(self, company, branch, date_from, date_to):
        qs_items = _paid_order_lines_qs(company, branch)
        qs_purchases = Purchase.objects.filter(company=company)
        qs_warehouse = Warehouse.objects.filter(company=company)
        qs_exp = CafeExpense.objects.filter(company=company)

        if branch is not None:
            qs_purchases = qs_purchases.filter(branch=branch)
            qs_warehouse = qs_warehouse.filter(branch=branch)
            qs_exp = qs_exp.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            qs_exp = qs_exp.filter(branch__isnull=True)

        qs_items = _apply_date_range(qs_items, "order__paid_at", date_from, date_to)
        qs_purchases = _apply_date_range(qs_purchases, "created_at", date_from, date_to)
        qs_exp = _apply_date_range(qs_exp, "expense_date", date_from, date_to)

        line_total = _line_revenue_expr()

        sales_agg = qs_items.aggregate(
            orders_count=Count("order_id", distinct=True),
            items_qty=Sum("quantity"),
            revenue=Sum(line_total),
        )
        purchases_agg = qs_purchases.aggregate(
            purchases_count=Count("id"),
            purchases_sum=Sum("price"),
        )
        exp_agg = qs_exp.aggregate(expenses_count=Count("id"), expenses_sum=Sum("amount"))

        low_stock_count = 0
        for w in qs_warehouse.only("remainder", "minimum"):
            rem = _to_decimal(w.remainder)
            mn = _to_decimal(w.minimum)
            if mn > 0 and rem < mn:
                low_stock_count += 1

        top_items_qs = (
            qs_items.filter(line_kind=OrderItem.LineKind.MENU, menu_item_id__isnull=False)
            .values("menu_item__title")
            .annotate(qty=Sum("quantity"), revenue=Sum(line_total))
            .order_by("-revenue", "-qty")[:10]
        )

        return {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "basis": "paid_at",
            "orders_count": int(sales_agg.get("orders_count") or 0),
            "items_qty": int(sales_agg.get("items_qty") or 0),
            "revenue": f"{_to_decimal(sales_agg.get('revenue')):.2f}",
            "purchases_count": int(purchases_agg.get("purchases_count") or 0),
            "purchases_sum": f"{_to_decimal(purchases_agg.get('purchases_sum')):.2f}",
            "cafe_expenses_count": int(exp_agg.get("expenses_count") or 0),
            "cafe_expenses_sum": f"{_to_decimal(exp_agg.get('expenses_sum')):.2f}",
            "low_stock_count": low_stock_count,
            "top_items": [
                {
                    "title": row["menu_item__title"] or "",
                    "qty": int(row["qty"] or 0),
                    "revenue": f"{_to_decimal(row['revenue']):.2f}",
                }
                for row in top_items_qs
            ],
        }

    def _cash_payload(self, company, branch, date_from, date_to):
        qs = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        qs = _apply_date_range(qs, "paid_at", date_from, date_to)

        final_total_expr = ExpressionWrapper(
            F("total_amount") - F("discount_amount"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

        orders_qs = (
            qs.annotate(final_amount=final_total_expr)
            .values("id", "paid_at", "payment_method", "final_amount")
            .order_by("-paid_at")[:500]
        )

        totals = {
            "cash": Decimal("0"),
            "card": Decimal("0"),
            "transfer": Decimal("0"),
            "other": Decimal("0"),
            "all": Decimal("0"),
        }

        rows = []
        for row in orders_qs:
            method = (row.get("payment_method") or "").strip().lower()
            amount = _to_decimal(row.get("final_amount"))
            if method in ("cash", "card", "transfer"):
                totals[method] += amount
            else:
                totals["other"] += amount
            totals["all"] += amount
            rows.append(
                {
                    "order_id": str(row["id"]),
                    "paid_at": row["paid_at"],
                    "payment_method": method or "unknown",
                    "final_amount": f"{amount:.2f}",
                }
            )

        return {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "totals": {k: f"{v:.2f}" for k, v in totals.items()},
            "rows": rows,
        }

    def _build_excel(self, report_type: str, payload: dict) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Report"

        if report_type == "analytics":
            ws.append(["Cafe analytics report"])
            ws.append(["date_from", payload["date_from"]])
            ws.append(["date_to", payload["date_to"]])
            ws.append(["basis", payload.get("basis", "paid_at")])
            ws.append(["orders_count", payload["orders_count"]])
            ws.append(["items_qty", payload["items_qty"]])
            ws.append(["revenue", payload["revenue"]])
            ws.append(["purchases_count", payload["purchases_count"]])
            ws.append(["purchases_sum", payload["purchases_sum"]])
            ws.append(["cafe_expenses_count", payload.get("cafe_expenses_count", 0)])
            ws.append(["cafe_expenses_sum", payload.get("cafe_expenses_sum", "0.00")])
            ws.append(["low_stock_count", payload["low_stock_count"]])
            ws.append([])
            ws.append(["Top menu items"])
            ws.append(["title", "qty", "revenue"])
            for row in payload["top_items"]:
                ws.append([row["title"], row["qty"], row["revenue"]])
        else:
            ws.append(["Cafe cash report"])
            ws.append(["date_from", payload["date_from"]])
            ws.append(["date_to", payload["date_to"]])
            ws.append(["total_all", payload["totals"]["all"]])
            ws.append(["total_cash", payload["totals"]["cash"]])
            ws.append(["total_card", payload["totals"]["card"]])
            ws.append(["total_transfer", payload["totals"]["transfer"]])
            ws.append(["total_other", payload["totals"]["other"]])
            ws.append([])
            ws.append(["order_id", "paid_at", "payment_method", "final_amount"])
            for row in payload["rows"]:
                ws.append([row["order_id"], str(row["paid_at"] or ""), row["payment_method"], row["final_amount"]])

        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def _build_word_html(self, report_type: str, payload: dict) -> bytes:
        if report_type == "analytics":
            rows = "".join(
                f"<tr><td>{r['title']}</td><td>{r['qty']}</td><td>{r['revenue']}</td></tr>"
                for r in payload["top_items"]
            )
            html = f"""
<html><head><meta charset="utf-8"></head><body>
<h2>Cafe analytics report</h2>
<p>date_from: {payload["date_from"]}</p>
<p>date_to: {payload["date_to"]}</p>
<p>basis: {payload.get("basis", "paid_at")}</p>
<p>orders_count: {payload["orders_count"]}</p>
<p>items_qty: {payload["items_qty"]}</p>
<p>revenue: {payload["revenue"]}</p>
<p>purchases_count: {payload["purchases_count"]}</p>
<p>purchases_sum: {payload["purchases_sum"]}</p>
<p>cafe_expenses_count: {payload.get("cafe_expenses_count", 0)}</p>
<p>cafe_expenses_sum: {payload.get("cafe_expenses_sum", "0.00")}</p>
<p>low_stock_count: {payload["low_stock_count"]}</p>
<h3>Top menu items</h3>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>title</th><th>qty</th><th>revenue</th></tr>
{rows}
</table>
</body></html>
"""
        else:
            rows = "".join(
                f"<tr><td>{r['order_id']}</td><td>{r['paid_at']}</td><td>{r['payment_method']}</td><td>{r['final_amount']}</td></tr>"
                for r in payload["rows"]
            )
            html = f"""
<html><head><meta charset="utf-8"></head><body>
<h2>Cafe cash report</h2>
<p>date_from: {payload["date_from"]}</p>
<p>date_to: {payload["date_to"]}</p>
<p>total_all: {payload["totals"]["all"]}</p>
<p>total_cash: {payload["totals"]["cash"]}</p>
<p>total_card: {payload["totals"]["card"]}</p>
<p>total_transfer: {payload["totals"]["transfer"]}</p>
<p>total_other: {payload["totals"]["other"]}</p>
<h3>Orders</h3>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>order_id</th><th>paid_at</th><th>payment_method</th><th>final_amount</th></tr>
{rows}
</table>
</body></html>
"""
        return html.encode("utf-8")

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=403)

        report_type = (request.query_params.get("report") or "analytics").strip().lower()
        export_format = (request.query_params.get("format") or "excel").strip().lower()
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        branch = self._active_branch()

        if report_type not in {"analytics", "cash"}:
            return Response({"detail": "report должен быть analytics или cash"}, status=400)
        if export_format not in {"excel", "word"}:
            return Response({"detail": "format должен быть excel или word"}, status=400)

        payload = (
            self._analytics_payload(company, branch, date_from, date_to)
            if report_type == "analytics"
            else self._cash_payload(company, branch, date_from, date_to)
        )

        date_tag = datetime.now().strftime("%Y%m%d_%H%M")
        branch_tag = _safe_filename_part(str(getattr(branch, "id", "") or "global"), "global")
        base_name = f"cafe_{report_type}_{branch_tag}_{date_tag}"

        if export_format == "excel":
            content = self._build_excel(report_type, payload)
            response = HttpResponse(
                content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = f'attachment; filename="{base_name}.xlsx"'
            return response

        content = self._build_word_html(report_type, payload)
        response = HttpResponse(content, content_type="application/msword; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{base_name}.doc"'
        return response
