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

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.db.models import (
    Q, Count, Avg, Sum, F,
    ExpressionWrapper, DurationField, DecimalField
)

from apps.cafe.models import KitchenTask, OrderItem, Purchase, Warehouse, Order
from apps.cafe.views import CompanyBranchQuerysetMixin
from openpyxl import Workbook


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

        qs = (OrderItem.objects
              .select_related("order", "menu_item", "menu_item__category")
              .filter(order__company=company, menu_item__company=company))

        if branch is not None:
            qs = qs.filter(order__branch=branch)

        qs = _apply_date_range(qs, "order__created_at", df, dt)

        line_total = ExpressionWrapper(
            F("quantity") * F("menu_item__price"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

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

    def _analytics_payload(self, company, branch, date_from, date_to):
        qs_items = (
            OrderItem.objects
            .select_related("order", "menu_item")
            .filter(order__company=company, menu_item__company=company)
        )
        qs_purchases = Purchase.objects.filter(company=company)
        qs_warehouse = Warehouse.objects.filter(company=company)

        if branch is not None:
            qs_items = qs_items.filter(order__branch=branch)
            qs_purchases = qs_purchases.filter(branch=branch)
            qs_warehouse = qs_warehouse.filter(branch=branch)

        qs_items = _apply_date_range(qs_items, "order__created_at", date_from, date_to)
        qs_purchases = _apply_date_range(qs_purchases, "created_at", date_from, date_to)

        line_total = ExpressionWrapper(
            F("quantity") * F("menu_item__price"),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

        sales_agg = qs_items.aggregate(
            orders_count=Count("order_id", distinct=True),
            items_qty=Sum("quantity"),
            revenue=Sum(line_total),
        )
        purchases_agg = qs_purchases.aggregate(
            purchases_count=Count("id"),
            purchases_sum=Sum("price"),
        )

        low_stock_count = 0
        for w in qs_warehouse.only("remainder", "minimum"):
            rem = _to_decimal(w.remainder)
            mn = _to_decimal(w.minimum)
            if mn > 0 and rem < mn:
                low_stock_count += 1

        top_items_qs = (
            qs_items.values("menu_item__title")
            .annotate(qty=Sum("quantity"), revenue=Sum(line_total))
            .order_by("-revenue", "-qty")[:10]
        )

        return {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "orders_count": int(sales_agg.get("orders_count") or 0),
            "items_qty": int(sales_agg.get("items_qty") or 0),
            "revenue": f"{_to_decimal(sales_agg.get('revenue')):.2f}",
            "purchases_count": int(purchases_agg.get("purchases_count") or 0),
            "purchases_sum": f"{_to_decimal(purchases_agg.get('purchases_sum')):.2f}",
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
            ws.append(["orders_count", payload["orders_count"]])
            ws.append(["items_qty", payload["items_qty"]])
            ws.append(["revenue", payload["revenue"]])
            ws.append(["purchases_count", payload["purchases_count"]])
            ws.append(["purchases_sum", payload["purchases_sum"]])
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
<p>orders_count: {payload["orders_count"]}</p>
<p>items_qty: {payload["items_qty"]}</p>
<p>revenue: {payload["revenue"]}</p>
<p>purchases_count: {payload["purchases_count"]}</p>
<p>purchases_sum: {payload["purchases_sum"]}</p>
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
