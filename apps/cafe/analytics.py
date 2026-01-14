# apps/cafe/views/analytics.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json

from rest_framework import permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Q, Count, Avg, Sum, F,
    ExpressionWrapper, DurationField, DecimalField
)

from apps.cafe.models import KitchenTask, OrderItem, Purchase, Warehouse
from apps.cafe.views import CompanyBranchQuerysetMixin


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

        qs = (OrderItem.objects
              .select_related("order", "menu_item")
              .filter(order__company=company, menu_item__company=company))

        # продажи — строгий branch (как большинство CRUD у тебя)
        if branch is not None:
            qs = qs.filter(order__branch=branch)

        qs = _apply_date_range(qs, "order__created_at", df, dt)

        line_total = ExpressionWrapper(
            F("quantity") * F("menu_item__price"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

        agg = qs.aggregate(
            orders_count=Count("order_id", distinct=True),
            items_qty=Sum("quantity"),
            revenue=Sum(line_total),
        )

        revenue = _to_decimal(agg.get("revenue"))
        payload = {
            "date_from": df,
            "date_to": dt,
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

        qs = (OrderItem.objects
              .select_related("order", "menu_item")
              .filter(order__company=company, menu_item__company=company))

        if branch is not None:
            qs = qs.filter(order__branch=branch)

        qs = _apply_date_range(qs, "order__created_at", df, dt)

        line_total = ExpressionWrapper(
            F("quantity") * F("menu_item__price"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

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
        for w in qs.only("id", "title", "unit", "remainder", "minimum"):
            rem = _to_decimal(w.remainder)
            mn = _to_decimal(w.minimum)
            if mn > 0 and rem < mn:
                out.append({
                    "id": str(w.id),
                    "title": w.title,
                    "unit": w.unit,
                    "remainder": str(w.remainder or ""),
                    "minimum": str(w.minimum or ""),
                })

        # сортируем “самые проблемные сверху”
        out.sort(key=lambda x: (_to_decimal(x["remainder"]) - _to_decimal(x["minimum"])))

        _cache_set(key, out, _analytics_ttl())
        return Response(out)
