# apps/cafe/views/analytics.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from html import escape
from io import BytesIO
from datetime import datetime, time, timedelta

from rest_framework import permissions
from rest_framework.request import Request as DRFRequest
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import BaseRenderer, JSONRenderer

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.http import HttpResponse
from django.db.models import (
    Q, Count, Avg, Sum, Max, F, Case, When,
    ExpressionWrapper, DurationField, DecimalField, Value, IntegerField,
    OuterRef, Subquery,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.cafe.models import (
    KitchenTask, OrderItem, Purchase, Warehouse, Order, MenuItem,
    CafeExpense, CafeWaiterPayProfile, OrderItemRefund, OrderRefund,
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


def _query_params(request):
    """
    QueryDict для аналитики: прямые вызовы API идут с DRF Request (.query_params),
    а CafeUnifiedAnalyticsView делегирует во вложенные APIView с django HttpRequest (только .GET).
    """
    qp = getattr(request, "query_params", None)
    if qp is not None:
        return qp
    return request.GET


def _django_http_request(request):
    """Вложенные APIView.as_view() ожидают django HttpRequest; снимаем обёртки DRF Request."""
    while isinstance(request, DRFRequest):
        request = request._request
    return request


def _resolve_leaf_field(model, field_path: str):
    """Цепочка FK (например order__paid_at) → конечное поле модели."""
    parts = field_path.split("__")
    for bit in parts[:-1]:
        rel = model._meta.get_field(bit)
        related = getattr(rel, "related_model", None)
        if related is None:
            raise FieldDoesNotExist(f"{model.__name__} has no forward relation {bit!r} in {field_path!r}")
        model = related
    return model._meta.get_field(parts[-1])


def _apply_date_range(qs, field_name: str, date_from: str | None, date_to: str | None):
    """
    Фильтр по календарным дням YYYY-MM-DD.
    Для DateTimeField — lookup __date (как раньше).
    Для DateField — прямое __gte/__lte: __date на DateField на части бэкендов даёт неверный SQL / ошибку.
    """
    from django.db.models import DateTimeField

    use_date_transform = True
    try:
        leaf = _resolve_leaf_field(qs.model, field_name)
        use_date_transform = isinstance(leaf, DateTimeField)
    except (FieldDoesNotExist, AttributeError, LookupError, ValueError):
        use_date_transform = True

    if date_from:
        suf = "__date__gte" if use_date_transform else "__gte"
        qs = qs.filter(**{f"{field_name}{suf}": date_from})
    if date_to:
        suf = "__date__lte" if use_date_transform else "__lte"
        qs = qs.filter(**{f"{field_name}{suf}": date_to})
    return qs


def _apply_datetime_range_calendar_days(qs, field_name: str, date_from: str | None, date_to: str | None):
    """
    DateTimeField: включительно по календарным дням YYYY-MM-DD в TIME_ZONE проекта (начало дня … конец дня).
    Устраняет сдвиг границ при lookup вида __date__ на aware-datetime в другой TZ.
    Без обоих параметров — фильтр не накладывается.
    """
    df = (date_from or "").strip() or None
    dt = (date_to or "").strip() or None
    if not df and not dt:
        return qs

    tz = timezone.get_current_timezone()

    def _parse_ymd(s: str):
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()

    try:
        if df:
            lo = _parse_ymd(df)
            start = timezone.make_aware(datetime.combine(lo, time.min), tz)
            qs = qs.filter(**{f"{field_name}__gte": start})
        if dt:
            hi = _parse_ymd(dt)
            end_exclusive = timezone.make_aware(datetime.combine(hi + timedelta(days=1), time.min), tz)
            qs = qs.filter(**{f"{field_name}__lt": end_exclusive})
    except ValueError:
        pass
    return qs


def _rejections_row_sort_key(row: dict):
    """Сортировка строк отчёта отказов/возвратов: сначала по дате (новее выше), затем по сумме."""
    ev = row.get("created_at")
    rev = _to_decimal(row.get("lost_revenue"))
    if isinstance(ev, datetime):
        return (True, ev, rev)
    return (False, None, rev)


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


def _line_net_quantity_expr():
    """Проданное количество за вычетом возвратов по строке (позиционные возвраты)."""
    return ExpressionWrapper(
        F("quantity") - Coalesce(F("refunded_quantity"), Value(0)),
        output_field=IntegerField(),
    )


def _line_net_revenue_expr():
    """Выручка по строке после позиционных возвратов (кол-во нетто × цена)."""
    unit = Coalesce(
        F("unit_price"),
        F("menu_item__price"),
        Value(Decimal("0")),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    return ExpressionWrapper(
        (F("quantity") - Coalesce(F("refunded_quantity"), Value(0))) * unit,
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _order_net_revenue_expr():
    """
    Деньги, оставшиеся по оплаченному заказу: оплата минус возвраты.
    Если paid_amount не заполнен (старые данные), берём итог после скидки минус возвраты.
    """
    ref = Coalesce(
        F("refunded_amount"),
        Value(Decimal("0")),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    paid_net = ExpressionWrapper(F("paid_amount") - ref, output_field=DecimalField(max_digits=14, decimal_places=2))
    final_net = ExpressionWrapper(
        (F("total_amount") - F("discount_amount")) - ref,
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    return Case(
        When(paid_amount__gt=0, then=paid_net),
        default=final_net,
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _order_line_level_net_revenue_expr():
    """
    Нетто по заказу (оплата минус возвраты) в контексте OrderItem: те же правила, что у Order.
    Нужно, чтобы учитывать POST .../refund/ (возврат по сумме чека без привязки к строкам).
    """
    ref = Coalesce(
        F("order__refunded_amount"),
        Value(Decimal("0")),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    paid_net = ExpressionWrapper(
        F("order__paid_amount") - ref,
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    final_net = ExpressionWrapper(
        (F("order__total_amount") - F("order__discount_amount")) - ref,
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    return Case(
        When(order__paid_amount__gt=0, then=paid_net),
        default=final_net,
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _order_lines_net_sum_subquery():
    """
    Сумма «строковой» выручки по всем позициям заказа (после позиционных возвратов).
    Используется как знаменатель для распределения заказового возврата между строками.
    """
    line_net = _line_net_revenue_expr()
    return Subquery(
        OrderItem.objects.filter(
            order_id=OuterRef("order_id"),
            company_id=OuterRef("company_id"),
            order__is_paid=True,
            is_rejected=False,
        )
        .values("order_id")
        .annotate(_oln_sum=Sum(line_net))
        .values("_oln_sum")[:1],
        output_field=DecimalField(max_digits=20, decimal_places=6),
    )


def _annotate_allocated_line_revenue(qs):
    """
    Выручка строки для аналитики:
      - позиционные возвраты (refunded_quantity) как в _line_net_revenue_expr;
      - плюс доля заказового возврата (refunded_amount без разбивки по позициям),
        пропорционально доле строки в сумме нетто-строк заказа.
    """
    line_net = _line_net_revenue_expr()
    net_qty = _line_net_quantity_expr()
    qs = qs.annotate(
        _ord_lines_net_sum=_order_lines_net_sum_subquery(),
        _ord_net_after_refunds=_order_line_level_net_revenue_expr(),
        _line_net_base=line_net,
        _line_net_qty=net_qty,
    )
    return qs.annotate(
        _alloc_line_revenue=Case(
            When(
                Q(_ord_lines_net_sum__isnull=True) | Q(_ord_lines_net_sum=0),
                then=F("_line_net_base"),
            ),
            default=ExpressionWrapper(
                F("_line_net_base") * F("_ord_net_after_refunds") / F("_ord_lines_net_sum"),
                output_field=DecimalField(max_digits=20, decimal_places=6),
            ),
            output_field=DecimalField(max_digits=20, decimal_places=6),
        ),
    )


def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "role", None) in ("owner", "admin"):
        return True
    if getattr(user, "owned_company", None):
        return True
    return False


def _analytics_waiter_scope(request):
    """
    Для сотрудников без owner/admin прав аналитика в кафе должна быть только по их заказам.
    """
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if _is_owner_like(user):
        return None
    return getattr(user, "id", None)


def _apply_waiter_scope(qs, request, field_name: str):
    waiter_id = _analytics_waiter_scope(request)
    if waiter_id:
        qs = qs.filter(**{field_name: waiter_id})
    return qs, waiter_id


def _scoped_purchase_qs(company, branch):
    qs = Purchase.objects.filter(company=company)
    if branch is not None:
        qs = qs.filter(branch=branch)
    else:
        qs = qs.filter(branch__isnull=True)
    return qs


def _scoped_cafe_expense_qs(company, branch):
    qs = CafeExpense.objects.filter(company=company)
    if branch is not None:
        qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
    else:
        qs = qs.filter(branch__isnull=True)
    return qs


def _cogs_sold_sum(company, branch, date_from, date_to, request) -> Decimal:
    """
    Учётная себестоимость проданных блюд: Σ (нетто-шт × menu_item.cost_price).
    """
    qs = _paid_order_lines_qs(company, branch).filter(
        line_kind=OrderItem.LineKind.MENU,
        menu_item_id__isnull=False,
    )
    qs, _ = _apply_waiter_scope(qs, request, "order__waiter_id")
    qs = _apply_date_range(qs, "order__paid_at", date_from, date_to)
    nq = _line_net_quantity_expr()
    cp = Coalesce(
        F("menu_item__cost_price"),
        Value(Decimal("0")),
        output_field=DecimalField(max_digits=14, decimal_places=4),
    )
    qs = qs.annotate(_nq=nq)
    qs = qs.annotate(
        _line_cogs=ExpressionWrapper(
            F("_nq") * cp,
            output_field=DecimalField(max_digits=24, decimal_places=8),
        )
    )
    agg = qs.aggregate(s=Sum("_line_cogs"))
    return _to_decimal(agg.get("s"))


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

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        waiter_scope_id = _analytics_waiter_scope(request) if self.group_field == "waiter" else None

        branch = self._active_branch()
        key = _cache_key(
            f"kitchen:{self.group_field}",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={
                "date_from": df,
                "date_to": dt,
                "waiter_scope_id": str(waiter_scope_id) if waiter_scope_id else None,
            },
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = KitchenTask.objects.filter(company=company)
        qs = _apply_branch_scope_for_kitchen_tasks(qs, self)
        if waiter_scope_id:
            qs = qs.filter(waiter_id=waiter_scope_id)
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

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        waiter_scope_id = _analytics_waiter_scope(request)

        branch = self._active_branch()
        key = _cache_key(
            "sales:summary",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={
                "date_from": df,
                "date_to": dt,
                "waiter_scope_id": str(waiter_scope_id) if waiter_scope_id else None,
            },
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        oq = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            oq = oq.filter(branch=branch)
        else:
            oq = oq.filter(branch__isnull=True)
        oq, _ = _apply_waiter_scope(oq, request, "waiter_id")
        oq = _apply_date_range(oq, "paid_at", df, dt)

        qs = _paid_order_lines_qs(company, branch)
        qs, _ = _apply_waiter_scope(qs, request, "order__waiter_id")
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        net_qty = _line_net_quantity_expr()

        order_agg = oq.aggregate(
            orders_count=Count("id"),
            revenue=Sum(_order_net_revenue_expr()),
        )
        line_agg = qs.aggregate(items_qty=Sum(net_qty))

        revenue = _to_decimal(order_agg.get("revenue"))
        payload = {
            "date_from": df,
            "date_to": dt,
            "basis": "paid_at",
            "orders_count": int(order_agg.get("orders_count") or 0),
            "items_qty": int(line_agg.get("items_qty") or 0),
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

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        limit_raw = _query_params(request).get("limit")
        try:
            limit = max(1, min(int(limit_raw or 10), 200))
        except Exception:
            limit = 10
        waiter_scope_id = _analytics_waiter_scope(request)

        branch = self._active_branch()
        key = _cache_key(
            "sales:items",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={
                "date_from": df,
                "date_to": dt,
                "limit": limit,
                "waiter_scope_id": str(waiter_scope_id) if waiter_scope_id else None,
            },
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = _paid_order_lines_qs(company, branch).filter(
            line_kind=OrderItem.LineKind.MENU,
            menu_item_id__isnull=False,
        )
        qs, _ = _apply_waiter_scope(qs, request, "order__waiter_id")
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        qs = _annotate_allocated_line_revenue(qs)

        data = (qs.values("menu_item_id", "menu_item__title")
                  .annotate(qty=Sum("_line_net_qty"), revenue=Sum("_alloc_line_revenue"))
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


class MenuAnalyticsAllView(CompanyBranchQuerysetMixin, APIView):
    """
    Общая аналитика по меню (все блюда, не только топ).

    Query params:
      - date_from=YYYY-MM-DD (по paid_at)
      - date_to=YYYY-MM-DD
      - limit (default=500, max=5000)
      - offset (default=0)
      - include_inactive=1 (если передано -> включать неактивные; по умолчанию включаем все)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({
                "date_from": _query_params(request).get("date_from"),
                "date_to": _query_params(request).get("date_to"),
                "basis": "paid_at",
                "offset": 0,
                "limit": 0,
                "total_items": 0,
                "rows": [],
                "grand_revenue": "0.00",
                "grand_qty": 0,
            })

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        include_inactive = str(_query_params(request).get("include_inactive") or "").strip() in ("1", "true", "yes", "on")
        limit_raw = _query_params(request).get("limit")
        offset_raw = _query_params(request).get("offset")
        try:
            limit = max(1, min(int(limit_raw or 500), 5000))
        except Exception:
            limit = 500
        try:
            offset = max(0, int(offset_raw or 0))
        except Exception:
            offset = 0

        waiter_scope_id = _analytics_waiter_scope(request)
        branch = self._active_branch()

        key = _cache_key(
            "menu:all",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={
                "date_from": df,
                "date_to": dt,
                "limit": limit,
                "offset": offset,
                "include_inactive": include_inactive,
                "waiter_scope_id": str(waiter_scope_id) if waiter_scope_id else None,
            },
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        # scope menu items
        mi_qs = MenuItem.objects.select_related("category", "kitchen").filter(company=company)
        if branch is not None:
            mi_qs = mi_qs.filter(branch=branch)
        else:
            mi_qs = mi_qs.filter(branch__isnull=True)
        if not include_inactive:
            # по умолчанию показываем всё меню, включая неактивные — но если явно не просили,
            # оставим обратную совместимость: include_inactive=0 -> только активные
            mi_qs = mi_qs.filter(is_active=True)

        line_qs = _paid_order_lines_qs(company, branch).filter(
            line_kind=OrderItem.LineKind.MENU,
            menu_item_id__isnull=False,
        )
        if waiter_scope_id:
            line_qs = line_qs.filter(order__waiter_id=waiter_scope_id)
        line_qs = _apply_date_range(line_qs, "order__paid_at", df, dt)
        line_qs = _annotate_allocated_line_revenue(line_qs)
        stats_rows = (
            line_qs.values("menu_item_id")
            .annotate(qty=Sum("_line_net_qty"), revenue=Sum("_alloc_line_revenue"))
        )
        stats_by_mid = {row["menu_item_id"]: row for row in stats_rows}

        decorated = []
        for mi in mi_qs.select_related("category", "kitchen").order_by("title"):
            st = stats_by_mid.get(mi.id) or {}
            qty = int(st.get("qty") or 0)
            rev = _to_decimal(st.get("revenue"))
            decorated.append((mi, qty, rev))
        decorated.sort(key=lambda t: (-t[2], -t[1], (t[0].title or "").lower()))

        total_items = len(decorated)
        page = decorated[offset: offset + limit]

        rows = []
        grand_revenue = Decimal("0.00")
        grand_qty = 0
        for mi, qty, rev in page:
            grand_revenue += rev
            grand_qty += qty
            avg = (rev / Decimal(qty)) if qty else Decimal("0")
            rows.append({
                "menu_item_id": str(mi.id),
                "title": mi.title,
                "category_id": str(mi.category_id) if mi.category_id else None,
                "category_title": (mi.category.title if mi.category_id else "") or "",
                "kitchen_id": str(mi.kitchen_id) if mi.kitchen_id else None,
                "kitchen_title": (mi.kitchen.title if mi.kitchen_id else "") or "",
                "price": str(mi.price),
                "is_active": bool(mi.is_active),
                "qty": qty,
                "revenue": f"{rev:.2f}",
                "avg_unit_price": f"{avg:.2f}",
            })

        payload = {
            "date_from": df,
            "date_to": dt,
            "basis": "paid_at",
            "offset": offset,
            "limit": limit,
            "total_items": int(total_items),
            "rows": rows,
            "page_revenue": f"{grand_revenue:.2f}",
            "page_qty": int(grand_qty),
        }
        _cache_set(key, payload, _analytics_ttl())
        return Response(payload)


class SalesByCategoryView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        limit_raw = _query_params(request).get("limit")
        try:
            limit = max(1, min(int(limit_raw or 50), 200))
        except Exception:
            limit = 50
        waiter_scope_id = _analytics_waiter_scope(request)

        branch = self._active_branch()
        key = _cache_key(
            "sales:categories",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={
                "date_from": df,
                "date_to": dt,
                "limit": limit,
                "waiter_scope_id": str(waiter_scope_id) if waiter_scope_id else None,
            },
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = _paid_order_lines_qs(company, branch).filter(
            line_kind=OrderItem.LineKind.MENU,
            menu_item_id__isnull=False,
        )
        qs, _ = _apply_waiter_scope(qs, request, "order__waiter_id")
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        qs = _annotate_allocated_line_revenue(qs)

        data = (qs.values("menu_item__category_id", "menu_item__category__title")
                  .annotate(qty=Sum("_line_net_qty"), revenue=Sum("_alloc_line_revenue"))
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
    """Выручка по кухням (из MenuItem.kitchen): оплаченные строки без отказов, кол-во и сумма нетто по возвратам позиций."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        waiter_scope_id = _analytics_waiter_scope(request)
        branch = self._active_branch()
        key = _cache_key(
            "sales:kitchens",
            company_id=str(company.id),
            branch_id=str(branch.id) if branch else None,
            params={
                "date_from": df,
                "date_to": dt,
                "waiter_scope_id": str(waiter_scope_id) if waiter_scope_id else None,
            },
        )
        hit = _cache_get(key)
        if hit is not None:
            return Response(hit)

        qs = _paid_order_lines_qs(company, branch).filter(
            line_kind=OrderItem.LineKind.MENU,
            menu_item_id__isnull=False,
        )
        qs, _ = _apply_waiter_scope(qs, request, "order__waiter_id")
        qs = _apply_date_range(qs, "order__paid_at", df, dt)
        qs = _annotate_allocated_line_revenue(qs)

        data = (
            qs.values("menu_item__kitchen_id", "menu_item__kitchen__title", "menu_item__kitchen__number")
            .annotate(qty=Sum("_line_net_qty"), revenue=Sum("_alloc_line_revenue"))
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


def _refund_rows_by_payment_method(item_refund_qs, order_refund_qs):
    """Суммы и количество операций возврата по способу (нал/карта/перевод)."""
    acc: dict[str, dict] = {}
    for src in (item_refund_qs, order_refund_qs):
        for row in src.values("payment_method").annotate(t=Sum("amount"), c=Count("id")):
            m = str(row.get("payment_method") or "").strip() or "unknown"
            if m not in acc:
                acc[m] = {"total": Decimal("0"), "count": 0}
            acc[m]["total"] += _to_decimal(row.get("t"))
            acc[m]["count"] += int(row.get("c") or 0)
    pm_labels = dict(OrderRefund._meta.get_field("payment_method").choices)
    out = []
    grand = Decimal("0")
    for m, v in sorted(acc.items(), key=lambda x: -x[1]["total"]):
        grand += v["total"]
        out.append({
            "method": m,
            "method_label": pm_labels.get(m, m),
            "count": v["count"],
            "total": f"{v['total']:.2f}",
        })
    return out, grand


class RevenueInflowView(CompanyBranchQuerysetMixin, APIView):
    """
    Приход по способам оплаты: оплаченные заказы по дате paid_at, суммы нетто (оплата минус возвраты по чеку).

    Дополнительно — возвраты денег за период по дате refunded_at и фактическому способу возврата
    (refunds_by_method, refunds_total), чтобы картина по кассе была полной.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"basis": "paid_at", "payment_methods": [], "totals": {}})

        df = (_query_params(request).get("date_from") or "").strip() or None
        dt = (_query_params(request).get("date_to") or "").strip() or None
        branch = self._active_branch()

        qs = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        qs, _ = _apply_waiter_scope(qs, request, "waiter_id")
        qs = _apply_date_range(qs, "paid_at", df, dt)

        net_expr = _order_net_revenue_expr()
        rows = (
            qs.annotate(net_captured=net_expr)
            .values("payment_method")
            .annotate(count=Count("id"), total=Sum("net_captured"))
            .order_by("-total")
        )

        methods = []
        grand = Decimal("0")
        for row in rows:
            m = str(row.get("payment_method") or "").strip() or "unknown"
            t = _to_decimal(row.get("total"))
            grand += t
            methods.append({
                "method": m,
                "method_label": dict(Order.PaymentMethod.choices).get(m, m),
                "count": int(row.get("count") or 0),
                "total": f"{t:.2f}",
            })

        ir_qs = OrderItemRefund.objects.filter(company=company)
        or_qs = OrderRefund.objects.filter(company=company)
        if branch is not None:
            ir_qs = ir_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
            or_qs = or_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
        else:
            ir_qs = ir_qs.filter(order__branch__isnull=True)
            or_qs = or_qs.filter(order__branch__isnull=True)
        ir_qs, _ = _apply_waiter_scope(ir_qs, request, "order__waiter_id")
        or_qs, _ = _apply_waiter_scope(or_qs, request, "order__waiter_id")
        ir_qs = _apply_datetime_range_calendar_days(ir_qs, "refunded_at", df, dt)
        or_qs = _apply_datetime_range_calendar_days(or_qs, "refunded_at", df, dt)

        refunds_by_method, refunds_grand = _refund_rows_by_payment_method(ir_qs, or_qs)

        return Response({
            "date_from": df,
            "date_to": dt,
            "basis": "paid_at",
            "refunds_basis": "refunded_at",
            "payment_methods": methods,
            "grand_total": f"{grand:.2f}",
            "refunds_by_method": refunds_by_method,
            "refunds_total": f"{refunds_grand:.2f}",
        })


class RejectionsAnalyticsView(CompanyBranchQuerysetMixin, APIView):
    """
    Отказы гостя + денежные возвраты (по позиции и по чеку) за период по дате события.

    Ответ: объект с date_from, date_to, totals (в т.ч. суммы возвратов), rows (до 200 строк).
    Обратная совместимость: ?flat=1 — только массив rows (как раньше).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qp = _query_params(request)
        flat_only = str(qp.get("flat") or "").strip().lower() in ("1", "true", "yes", "on")

        company = self._user_company()
        if not company:
            if flat_only:
                return Response([])
            return Response({
                "date_from": None,
                "date_to": None,
                "basis": "rejected_at / refunded_at",
                "totals": {
                    "guest_rejections_lost": "0.00",
                    "item_refunds": "0.00",
                    "order_refunds": "0.00",
                    "refunds_total": "0.00",
                },
                "rows": [],
            })

        df = (qp.get("date_from") or "").strip() or None
        dt = (qp.get("date_to") or "").strip() or None
        branch = self._active_branch()

        qs = OrderItem.objects.select_related("order", "menu_item").filter(
            company=company,
            is_rejected=True,
        )
        if branch is not None:
            qs = qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
        else:
            qs = qs.filter(order__branch__isnull=True)
        qs, _waiter_scope_id = _apply_waiter_scope(qs, request, "order__waiter_id")
        if df or dt:
            qs = qs.filter(rejected_at__isnull=False)
        qs = _apply_datetime_range_calendar_days(qs, "rejected_at", df, dt)

        line_total = _line_revenue_expr()
        guest_lost_total = _to_decimal(qs.aggregate(t=Sum(line_total)).get("t"))
        by_reason = (
            qs.values("rejection_reason")
            .annotate(
                qty=Sum("quantity"),
                lost_revenue=Sum(line_total),
                last_rejected_at=Max("rejected_at"),
            )
            .order_by("-lost_revenue")
        )

        user = getattr(request, "user", None)
        employee_name = ""
        if user and getattr(user, "is_authenticated", False):
            full = getattr(user, "get_full_name", lambda: "")() or ""
            email = getattr(user, "email", "") or ""
            employee_name = full or email or str(getattr(user, "id", "") or "")

        pm_labels = dict(OrderRefund._meta.get_field("payment_method").choices)

        ir_qs = OrderItemRefund.objects.filter(company=company)
        if branch is not None:
            ir_qs = ir_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
        else:
            ir_qs = ir_qs.filter(order__branch__isnull=True)
        ir_qs, _ = _apply_waiter_scope(ir_qs, request, "order__waiter_id")
        ir_qs = _apply_datetime_range_calendar_days(ir_qs, "refunded_at", df, dt)
        by_item_refund = ir_qs.values("note", "payment_method").annotate(
            qty=Sum("quantity"),
            lost_revenue=Sum("amount"),
            last_refunded_at=Max("refunded_at"),
        )

        or_qs = OrderRefund.objects.filter(company=company)
        if branch is not None:
            or_qs = or_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
        else:
            or_qs = or_qs.filter(order__branch__isnull=True)
        or_qs, _ = _apply_waiter_scope(or_qs, request, "order__waiter_id")
        or_qs = _apply_datetime_range_calendar_days(or_qs, "refunded_at", df, dt)
        by_order_refund = or_qs.values("note", "payment_method").annotate(
            qty=Count("id"),
            lost_revenue=Sum("amount"),
            last_refunded_at=Max("refunded_at"),
        )

        rows = [
            {
                "rejection_reason": (row["rejection_reason"] or "").strip() or "—",
                "qty": int(row["qty"] or 0),
                "lost_revenue": f"{_to_decimal(row['lost_revenue']):.2f}",
                "employee_name": employee_name,
                "created_at": row["last_rejected_at"],
                "row_kind": "guest_rejection",
            }
            for row in by_reason
        ]

        for row in by_item_refund:
            note = (row.get("note") or "").strip()
            pm = pm_labels.get(row.get("payment_method") or "", row.get("payment_method") or "")
            reason = f"Возврат по позиции: {note} ({pm})" if note else f"Возврат по позиции ({pm})"
            rows.append({
                "rejection_reason": reason,
                "qty": int(row["qty"] or 0),
                "lost_revenue": f"{_to_decimal(row['lost_revenue']):.2f}",
                "employee_name": employee_name,
                "created_at": row["last_refunded_at"],
                "row_kind": "item_refund",
            })

        for row in by_order_refund:
            note = (row.get("note") or "").strip()
            pm = pm_labels.get(row.get("payment_method") or "", row.get("payment_method") or "")
            reason = f"Возврат по чеку: {note} ({pm})" if note else f"Возврат по чеку ({pm})"
            rows.append({
                "rejection_reason": reason,
                "qty": int(row["qty"] or 0),
                "lost_revenue": f"{_to_decimal(row['lost_revenue']):.2f}",
                "employee_name": employee_name,
                "created_at": row["last_refunded_at"],
                "row_kind": "order_refund",
            })

        rows.sort(key=_rejections_row_sort_key, reverse=True)
        rows = rows[:200]

        item_refunds_total = _to_decimal(ir_qs.aggregate(t=Sum("amount")).get("t"))
        order_refunds_total = _to_decimal(or_qs.aggregate(t=Sum("amount")).get("t"))
        refunds_total = (item_refunds_total + order_refunds_total).quantize(Decimal("0.01"))

        payload = {
            "date_from": df,
            "date_to": dt,
            "basis": "rejected_at / refunded_at",
            "totals": {
                "guest_rejections_lost": f"{guest_lost_total:.2f}",
                "item_refunds": f"{item_refunds_total:.2f}",
                "order_refunds": f"{order_refunds_total:.2f}",
                "refunds_total": f"{refunds_total:.2f}",
            },
            "rows": rows,
        }
        if flat_only:
            return Response(rows)
        return Response(payload)


class CancelledOrdersAnalyticsView(CompanyBranchQuerysetMixin, APIView):
    """
    Отменённые заказы: кто отменил и когда.

    Query params:
      - date_from=YYYY-MM-DD
      - date_to=YYYY-MM-DD
      - limit (default=200, max=1000)
      - offset (default=0)
    Фильтрация по дате идёт по Order.canceled_at (если null — по updated_at как запасной вариант).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"date_from": None, "date_to": None, "basis": "canceled_at", "rows": []})

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        limit_raw = _query_params(request).get("limit")
        offset_raw = _query_params(request).get("offset")
        try:
            limit = max(1, min(int(limit_raw or 200), 1000))
        except Exception:
            limit = 200
        try:
            offset = max(0, int(offset_raw or 0))
        except Exception:
            offset = 0

        branch = self._active_branch()

        qs = Order.objects.select_related("table", "waiter", "canceled_by").filter(
            company=company,
            status=Order.Status.CANCELLED,
        )
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)

        qs, waiter_scope_id = _apply_waiter_scope(qs, request, "waiter_id")

        # Prefer canceled_at; fallback to updated_at for legacy rows without canceled_at.
        if df:
            qs = qs.filter(Q(canceled_at__date__gte=df) | Q(canceled_at__isnull=True, updated_at__date__gte=df))
        if dt:
            qs = qs.filter(Q(canceled_at__date__lte=dt) | Q(canceled_at__isnull=True, updated_at__date__lte=dt))

        rows = []
        for o in qs.order_by("-canceled_at", "-updated_at")[offset: offset + limit]:
            canceled_at = o.canceled_at or o.updated_at
            who = ""
            if getattr(o, "canceled_by_id", None):
                u = o.canceled_by
                if u:
                    full = getattr(u, "get_full_name", lambda: "")() or ""
                    email = getattr(u, "email", "") or ""
                    who = full or email or str(o.canceled_by_id)
                else:
                    who = str(o.canceled_by_id)
            rows.append({
                "order_id": str(o.id),
                "table_number": _safe_order_table_number(o),
                "waiter_id": str(o.waiter_id) if o.waiter_id else None,
                "canceled_at": canceled_at,
                "canceled_by_id": str(o.canceled_by_id) if o.canceled_by_id else None,
                "canceled_by_label": who,
                "total_amount": str(o.total_amount),
                "discount_amount": str(o.discount_amount),
                "final_amount": str(o.final_amount),
                "is_paid": bool(o.is_paid),
                "paid_at": o.paid_at,
            })

        return Response({
            "date_from": df,
            "date_to": dt,
            "basis": "canceled_at",
            "offset": offset,
            "limit": limit,
            "waiter_scope_id": str(waiter_scope_id) if waiter_scope_id else None,
            "rows": rows,
        })


class CafeExpensesSummaryView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"expenses_count": 0, "expenses_sum": "0.00", "items": [], "expenses_by_day": []})

        qp = _query_params(request)
        df = qp.get("date_from")
        dt = qp.get("date_to")
        try:
            limit = max(1, min(int(qp.get("limit") or 500), 2000))
        except Exception:
            limit = 500

        branch = self._active_branch()
        qs = _scoped_cafe_expense_qs(company, branch)
        qs = _apply_date_range(qs, "expense_date", df, dt)

        agg = qs.aggregate(c=Count("id"), s=Sum("amount"))
        expenses_sum = _to_decimal(agg.get("s"))
        expenses_count = int(agg.get("c") or 0)

        by_day_qs = (
            qs.values("expense_date")
            .annotate(day_total=Sum("amount"), day_count=Count("id"))
            .order_by("expense_date")
        )
        expenses_by_day = [
            {
                "date": str(r["expense_date"]),
                "total": f"{_to_decimal(r['day_total']):.2f}",
                "count": int(r["day_count"] or 0),
            }
            for r in by_day_qs
        ]

        items = []
        for e in qs.select_related("created_by").order_by("-expense_date", "-created_at")[:limit]:
            cb = ""
            if e.created_by_id:
                u = e.created_by
                cb = (getattr(u, "get_full_name", lambda: "")() or getattr(u, "email", "") or str(e.created_by_id))
            items.append({
                "id": str(e.id),
                "title": e.title,
                "amount": f"{_to_decimal(e.amount):.2f}",
                "category": e.category or "",
                "expense_date": str(e.expense_date),
                "note": (e.note or "")[:500],
                "created_by": cb,
            })

        return Response({
            "date_from": df,
            "date_to": dt,
            "basis": "expense_date",
            "expenses_count": expenses_count,
            "expenses_sum": f"{expenses_sum:.2f}",
            "other_expenses_section": {
                "title": "Прочие расходы",
                "items": items,
                "expenses_by_day": expenses_by_day,
            },
            "items": items,
            "expenses_by_day": expenses_by_day,
        })


class CafeFinanceAnalyticsView(CompanyBranchQuerysetMixin, APIView):
    """
    Финансы кафе в духе маркета (tab=finance): детальные приходы, прочие расходы, закупки, возвраты,
    валовая прибыль и маржа по учётной себестоимости блюд (cost_price × нетто-продажи).
    Чистая прибыль в cards.net_profit = валовая − прочие операционные расходы (CafeExpense), без суммы закупок.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=403)

        qp = _query_params(request)
        df = (qp.get("date_from") or "").strip() or None
        dt = (qp.get("date_to") or "").strip() or None
        try:
            limit = max(1, min(int(qp.get("limit") or 500), 2000))
        except Exception:
            limit = 500

        branch = self._active_branch()

        oq = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            oq = oq.filter(branch=branch)
        else:
            oq = oq.filter(branch__isnull=True)
        oq, _ = _apply_waiter_scope(oq, request, "waiter_id")
        oq = _apply_date_range(oq, "paid_at", df, dt)

        revenue = _to_decimal(oq.aggregate(s=Sum(_order_net_revenue_expr()))["s"])
        cogs = _cogs_sold_sum(company, branch, df, dt, request)
        gross = revenue - cogs
        margin_pct = float((gross / revenue * Decimal("100")).quantize(Decimal("0.01"))) if revenue > 0 else 0.0

        pqs = _scoped_purchase_qs(company, branch)
        pqs = _apply_date_range(pqs, "created_at", df, dt)
        purchases_sum = _to_decimal(pqs.aggregate(s=Sum("price"))["s"])
        purchases_count = int(pqs.aggregate(c=Count("id"))["c"] or 0)

        eqs = _scoped_cafe_expense_qs(company, branch)
        eqs = _apply_date_range(eqs, "expense_date", df, dt)
        expenses_sum = _to_decimal(eqs.aggregate(s=Sum("amount"))["s"])
        expenses_count = int(eqs.aggregate(c=Count("id"))["c"] or 0)
        net_profit = gross - expenses_sum

        net_expr = _order_net_revenue_expr()
        pm_rows = (
            oq.annotate(net_captured=net_expr)
            .values("payment_method")
            .annotate(count=Count("id"), total=Sum("net_captured"))
            .order_by("-total")
        )
        pm_labels = dict(Order.PaymentMethod.choices)
        income_breakdown = []
        for row in pm_rows:
            m = str(row.get("payment_method") or "").strip() or "unknown"
            t = _to_decimal(row.get("total"))
            income_breakdown.append({
                "method": m,
                "method_label": pm_labels.get(m, m),
                "count": int(row.get("count") or 0),
                "total": f"{t:.2f}",
            })

        income_items = []
        for o in oq.annotate(net_captured=net_expr).select_related("table", "waiter").order_by("-paid_at")[:limit]:
            income_items.append({
                "kind": "order_payment",
                "id": str(o.id),
                "paid_at": o.paid_at.isoformat() if o.paid_at else None,
                "payment_method": o.payment_method or "",
                "payment_method_label": pm_labels.get(o.payment_method or "", o.payment_method or ""),
                "amount": f"{_to_decimal(o.net_captured):.2f}",
                "table_number": _safe_order_table_number(o),
            })

        other_expenses_items = []
        for e in eqs.select_related("created_by").order_by("-expense_date", "-created_at")[:limit]:
            cb = ""
            if e.created_by_id:
                u = e.created_by
                cb = (getattr(u, "get_full_name", lambda: "")() or getattr(u, "email", "") or str(e.created_by_id))
            other_expenses_items.append({
                "id": str(e.id),
                "kind": "cafe_expense",
                "title": e.title,
                "amount": f"{_to_decimal(e.amount):.2f}",
                "category": e.category or "",
                "expense_date": str(e.expense_date),
                "note": (e.note or "")[:500],
                "created_by": cb,
            })

        by_day_qs = (
            eqs.values("expense_date")
            .annotate(day_total=Sum("amount"), day_count=Count("id"))
            .order_by("expense_date")
        )
        expenses_by_day = [
            {
                "date": str(r["expense_date"]),
                "total": f"{_to_decimal(r['day_total']):.2f}",
                "count": int(r["day_count"] or 0),
            }
            for r in by_day_qs
        ]

        purchase_items = []
        for p in pqs.order_by("-created_at")[:limit]:
            purchase_items.append({
                "id": str(p.id),
                "kind": "purchase",
                "supplier": p.supplier,
                "amount": f"{_to_decimal(p.price):.2f}",
                "positions": p.positions,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })

        ir_qs = OrderItemRefund.objects.filter(company=company)
        or_qs = OrderRefund.objects.filter(company=company)
        if branch is not None:
            ir_qs = ir_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
            or_qs = or_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
        else:
            ir_qs = ir_qs.filter(order__branch__isnull=True)
            or_qs = or_qs.filter(order__branch__isnull=True)
        ir_qs, _ = _apply_waiter_scope(ir_qs, request, "order__waiter_id")
        or_qs, _ = _apply_waiter_scope(or_qs, request, "order__waiter_id")
        ir_qs = _apply_datetime_range_calendar_days(ir_qs, "refunded_at", df, dt)
        or_qs = _apply_datetime_range_calendar_days(or_qs, "refunded_at", df, dt)

        refund_items = []
        for r in or_qs.select_related("order").order_by("-refunded_at")[:limit]:
            refund_items.append({
                "kind": "order_refund",
                "id": str(r.id),
                "order_id": str(r.order_id),
                "amount": f"{_to_decimal(r.amount):.2f}",
                "payment_method": r.payment_method,
                "refunded_at": r.refunded_at.isoformat() if r.refunded_at else None,
                "note": r.note or "",
            })
        for r in ir_qs.select_related("order").order_by("-refunded_at")[:limit]:
            refund_items.append({
                "kind": "item_refund",
                "id": str(r.id),
                "order_id": str(r.order_id),
                "amount": f"{_to_decimal(r.amount):.2f}",
                "payment_method": r.payment_method,
                "refunded_at": r.refunded_at.isoformat() if r.refunded_at else None,
                "note": r.note or "",
            })
        refund_items.sort(key=lambda x: x.get("refunded_at") or "", reverse=True)
        refund_items = refund_items[:limit]

        refunds_by_method, refunds_total = _refund_rows_by_payment_method(ir_qs, or_qs)

        return Response({
            "tab": "finance",
            "date_from": df,
            "date_to": dt,
            "basis": "paid_at",
            "refunds_basis": "refunded_at",
            "cards": {
                "revenue": f"{revenue:.2f}",
                "cogs_sold": f"{cogs:.2f}",
                "gross_profit": f"{gross:.2f}",
                "margin_percent": margin_pct,
                "purchases_sum": f"{purchases_sum:.2f}",
                "purchases_count": purchases_count,
                "other_expenses_sum": f"{expenses_sum:.2f}",
                "other_expenses_count": expenses_count,
                "net_profit": f"{net_profit:.2f}",
            },
            "income_breakdown": income_breakdown,
            "income_items": income_items,
            "other_expenses_section": {
                "title": "Прочие расходы",
                "items": other_expenses_items,
                "expenses_by_day": expenses_by_day,
            },
            "purchase_items": purchase_items,
            "refund_items": refund_items,
            "refunds_by_method": refunds_by_method,
            "refunds_total": f"{refunds_total:.2f}",
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
        qs, _ = _apply_waiter_scope(qs, request, "waiter_id")

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

        shift_id = _query_params(request).get("shift")
        if not shift_id:
            return Response({"detail": "Укажите query-параметр shift=<uuid>."}, status=400)

        shift = CashShift.objects.filter(pk=shift_id, company=company).first()
        if not shift:
            return Response({"detail": "Смена не найдена."}, status=404)

        branch = self._active_branch()
        qs = Order.objects.filter(company=company, cash_shift_id=shift.id, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        qs, _ = _apply_waiter_scope(qs, request, "waiter_id")

        net_expr = _order_net_revenue_expr()
        by_pm = (
            qs.annotate(fa=net_expr)
            .values("payment_method")
            .annotate(count=Count("id"), total=Sum("fa"))
        )

        methods = []
        g = Decimal("0")
        for row in by_pm:
            m = str(row.get("payment_method") or "").strip() or "unknown"
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

        day = _query_params(request).get("date")
        if not day:
            return Response({"detail": "Укажите date=YYYY-MM-DD."}, status=400)

        branch = self._active_branch()
        qs = Order.objects.filter(company=company, is_paid=True, paid_at__date=day)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        qs, _ = _apply_waiter_scope(qs, request, "waiter_id")

        net_expr = _order_net_revenue_expr()
        by_pm = (
            qs.annotate(fa=net_expr)
            .values("payment_method")
            .annotate(count=Count("id"), total=Sum("fa"))
        )
        methods = []
        g = Decimal("0")
        for row in by_pm:
            m = str(row.get("payment_method") or "").strip() or "unknown"
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
    по заказам официанта (оплачено, paid_at, нетто после возвратов).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
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
        waiter_scope_id = _analytics_waiter_scope(request)
        if waiter_scope_id:
            prof_qs = prof_qs.filter(user_id=waiter_scope_id)
        if branch is not None:
            prof_qs = prof_qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            prof_qs = prof_qs.filter(branch__isnull=True)

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
            agg = oq.annotate(_nr=_order_net_revenue_expr()).aggregate(s=Sum("_nr"))
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
    Единая точка аналитики кафе (по аналогии с маркетом):
    tab=revenue|finance|dishes|kitchens|waiters|sales_summary|categories|purchases|expenses|debts|rejections|salary|daily_close|shift
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        tab = (_query_params(request).get("tab") or "revenue").strip().lower()
        company = self._user_company()
        if not company:
            return Response({"tab": tab, "detail": "Компания не найдена."}, status=403)

        http_req = _django_http_request(request)

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
        if tab == "finance":
            return CafeFinanceAnalyticsView.as_view()(http_req)
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
    """Выручка по официантам: оплаченные заказы по paid_at, сумма нетто (оплата минус возвраты)."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response([])

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        branch = self._active_branch()

        qs = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        qs, _ = _apply_waiter_scope(qs, request, "waiter_id")
        qs = _apply_date_range(qs, "paid_at", df, dt)

        net_expr = _order_net_revenue_expr()
        data = (
            qs.annotate(net_captured=net_expr)
            .values("waiter_id", "waiter__first_name", "waiter__last_name", "waiter__email")
            .annotate(orders_count=Count("id"), revenue=Sum("net_captured"))
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

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")

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

        qs = _scoped_purchase_qs(company, branch)
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

        df = _query_params(request).get("date_from")
        dt = _query_params(request).get("date_to")
        limit_raw = _query_params(request).get("limit")
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

        qs = _scoped_purchase_qs(company, branch)
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


def _export_html_escape(value) -> str:
    """Экранирование для Word HTML-экспорта + защита от суррогатов при последующем encode('utf-8')."""
    if value is None:
        s = ""
    else:
        s = str(value)
    s = s.encode("utf-8", errors="replace").decode("utf-8")
    return escape(s, quote=True)


def _safe_order_table_number(order) -> int | None:
    if not getattr(order, "table_id", None):
        return None
    try:
        return order.table.number
    except ObjectDoesNotExist:
        return None


def _safe_order_table_number_export(order) -> str:
    n = _safe_order_table_number(order)
    return "" if n is None else str(n)


class CafeAnalyticsExportView(CompanyBranchQuerysetMixin, APIView):
    """
    Экспорт аналитики/кассы:
      GET /cafe/analytics/export/?report=analytics|cash&format=excel|word&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD

    Excel (analytics): листы «Сводка», «Приходы», «Закупки», «Прочие расходы», «Возвраты», «Расходы по дням», «Топ блюд».
    Касса: те же правила филиала и официанта, что и в /analytics/revenue-inflow/ (раньше при глобальном филиале
    попадали все заказы компании — исправлено).
    """
    permission_classes = [permissions.IsAuthenticated]
    renderer_classes = [JSONRenderer, _BinaryExcelRenderer, _BinaryWordRenderer]
    _export_row_limit = 2000

    def _refund_qs_for_export(self, company, branch, date_from, date_to, request):
        ir_qs = OrderItemRefund.objects.filter(company=company)
        or_qs = OrderRefund.objects.filter(company=company)
        if branch is not None:
            ir_qs = ir_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
            or_qs = or_qs.filter(Q(order__branch=branch) | Q(order__branch__isnull=True))
        else:
            ir_qs = ir_qs.filter(order__branch__isnull=True)
            or_qs = or_qs.filter(order__branch__isnull=True)
        ir_qs, _ = _apply_waiter_scope(ir_qs, request, "order__waiter_id")
        or_qs, _ = _apply_waiter_scope(or_qs, request, "order__waiter_id")
        ir_qs = _apply_datetime_range_calendar_days(ir_qs, "refunded_at", date_from, date_to)
        or_qs = _apply_datetime_range_calendar_days(or_qs, "refunded_at", date_from, date_to)
        return ir_qs, or_qs

    def _analytics_payload(self, company, branch, date_from, date_to, request):
        lim = self._export_row_limit
        qs_items = _paid_order_lines_qs(company, branch)
        qs_items, _ = _apply_waiter_scope(qs_items, request, "order__waiter_id")
        qs_purchases = _scoped_purchase_qs(company, branch)
        qs_warehouse = Warehouse.objects.filter(company=company)
        qs_exp = _scoped_cafe_expense_qs(company, branch)

        if branch is not None:
            qs_warehouse = qs_warehouse.filter(branch=branch)

        qs_items = _apply_date_range(qs_items, "order__paid_at", date_from, date_to)
        qs_purchases = _apply_date_range(qs_purchases, "created_at", date_from, date_to)
        qs_exp = _apply_date_range(qs_exp, "expense_date", date_from, date_to)

        oq = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            oq = oq.filter(branch=branch)
        else:
            oq = oq.filter(branch__isnull=True)
        oq, _ = _apply_waiter_scope(oq, request, "waiter_id")
        oq = _apply_date_range(oq, "paid_at", date_from, date_to)

        net_qty = _line_net_quantity_expr()
        line_part = qs_items.aggregate(items_qty=Sum(net_qty))
        order_part = oq.aggregate(
            orders_count=Count("id"),
            revenue=Sum(_order_net_revenue_expr()),
        )
        sales_agg = {**line_part, **order_part}
        purchases_agg = qs_purchases.aggregate(
            purchases_count=Count("id"),
            purchases_sum=Sum("price"),
        )
        exp_agg = qs_exp.aggregate(expenses_count=Count("id"), expenses_sum=Sum("amount"))

        revenue_d = _to_decimal(sales_agg.get("revenue"))
        cogs = _cogs_sold_sum(company, branch, date_from, date_to, request)
        gross = revenue_d - cogs
        margin_pct = float((gross / revenue_d * Decimal("100")).quantize(Decimal("0.01"))) if revenue_d > 0 else 0.0
        expenses_d = _to_decimal(exp_agg.get("expenses_sum"))
        net_profit = gross - expenses_d

        low_stock_count = 0
        for w in qs_warehouse.only("remainder", "minimum"):
            rem = _to_decimal(w.remainder)
            mn = _to_decimal(w.minimum)
            if mn > 0 and rem < mn:
                low_stock_count += 1

        top_lines = _annotate_allocated_line_revenue(
            qs_items.filter(line_kind=OrderItem.LineKind.MENU, menu_item_id__isnull=False)
        )
        top_items_qs = (
            top_lines.values("menu_item__title")
            .annotate(qty=Sum("_line_net_qty"), revenue=Sum("_alloc_line_revenue"))
            .order_by("-revenue", "-qty")[:10]
        )

        net_expr = _order_net_revenue_expr()
        pm_labels = dict(Order.PaymentMethod.choices)
        income_rows = []
        for o in oq.annotate(net_captured=net_expr).select_related("table").order_by("-paid_at")[:lim]:
            income_rows.append({
                "order_id": str(o.id),
                "paid_at": str(o.paid_at or ""),
                "payment_method": (o.payment_method or ""),
                "payment_method_label": pm_labels.get(o.payment_method or "", o.payment_method or ""),
                "amount": f"{_to_decimal(o.net_captured):.2f}",
                "table_number": _safe_order_table_number_export(o),
            })

        purchase_rows = []
        for p in qs_purchases.order_by("-created_at")[:lim]:
            purchase_rows.append({
                "id": str(p.id),
                "created_at": str(p.created_at or ""),
                "supplier": p.supplier,
                "positions": p.positions,
                "amount": f"{_to_decimal(p.price):.2f}",
            })

        expense_rows = []
        for e in qs_exp.order_by("-expense_date", "-created_at")[:lim]:
            expense_rows.append({
                "id": str(e.id),
                "expense_date": str(e.expense_date),
                "title": e.title,
                "category": e.category or "",
                "amount": f"{_to_decimal(e.amount):.2f}",
                "note": (e.note or "")[:200],
            })

        ir_qs, or_qs = self._refund_qs_for_export(company, branch, date_from, date_to, request)
        refunds_by_method, refunds_grand = _refund_rows_by_payment_method(ir_qs, or_qs)

        refund_rows = []
        for r in or_qs.order_by("-refunded_at")[:lim]:
            refund_rows.append({
                "kind": "order_refund",
                "id": str(r.id),
                "order_id": str(r.order_id),
                "refunded_at": str(r.refunded_at or ""),
                "payment_method": r.payment_method,
                "amount": f"{_to_decimal(r.amount):.2f}",
                "note": (r.note or "")[:120],
            })
        for r in ir_qs.order_by("-refunded_at")[:lim]:
            refund_rows.append({
                "kind": "item_refund",
                "id": str(r.id),
                "order_id": str(r.order_id),
                "refunded_at": str(r.refunded_at or ""),
                "payment_method": r.payment_method,
                "amount": f"{_to_decimal(r.amount):.2f}",
                "note": (r.note or "")[:120],
            })

        exp_by_day = (
            qs_exp.values("expense_date")
            .annotate(day_total=Sum("amount"), day_count=Count("id"))
            .order_by("expense_date")
        )
        expenses_by_day_export = [
            {
                "date": str(r["expense_date"]),
                "total": f"{_to_decimal(r['day_total']):.2f}",
                "count": int(r["day_count"] or 0),
            }
            for r in exp_by_day
        ]

        return {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "basis": "paid_at",
            "orders_count": int(sales_agg.get("orders_count") or 0),
            "items_qty": int(sales_agg.get("items_qty") or 0),
            "revenue": f"{revenue_d:.2f}",
            "cogs_sold": f"{cogs:.2f}",
            "gross_profit": f"{gross:.2f}",
            "margin_percent": margin_pct,
            "net_profit": f"{net_profit:.2f}",
            "purchases_count": int(purchases_agg.get("purchases_count") or 0),
            "purchases_sum": f"{_to_decimal(purchases_agg.get('purchases_sum')):.2f}",
            "cafe_expenses_count": int(exp_agg.get("expenses_count") or 0),
            "cafe_expenses_sum": f"{expenses_d:.2f}",
            "refunds_total": f"{refunds_grand:.2f}",
            "low_stock_count": low_stock_count,
            "top_items": [
                {
                    "title": row["menu_item__title"] or "",
                    "qty": int(row["qty"] or 0),
                    "revenue": f"{_to_decimal(row['revenue']):.2f}",
                }
                for row in top_items_qs
            ],
            "income_rows": income_rows,
            "purchase_rows": purchase_rows,
            "expense_rows": expense_rows,
            "refund_rows": refund_rows,
            "refunds_by_method": refunds_by_method,
            "expenses_by_day": expenses_by_day_export,
        }

    def _cash_payload(self, company, branch, date_from, date_to, request):
        lim = self._export_row_limit
        qs = Order.objects.filter(company=company, is_paid=True)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        qs, _ = _apply_waiter_scope(qs, request, "waiter_id")
        qs = _apply_date_range(qs, "paid_at", date_from, date_to)

        orders_qs = (
            qs.annotate(row_net=_order_net_revenue_expr())
            .values("id", "paid_at", "payment_method", "row_net")
            .order_by("-paid_at")[:lim]
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
            method = str(row.get("payment_method") or "").strip().lower()
            amount = _to_decimal(row.get("row_net"))
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

        ir_qs, or_qs = self._refund_qs_for_export(company, branch, date_from, date_to, request)
        refunds_by_method, refunds_grand = _refund_rows_by_payment_method(ir_qs, or_qs)

        refund_rows = []
        for r in or_qs.order_by("-refunded_at")[:lim]:
            refund_rows.append({
                "kind": "order_refund",
                "id": str(r.id),
                "order_id": str(r.order_id),
                "refunded_at": str(r.refunded_at or ""),
                "payment_method": r.payment_method,
                "amount": f"{_to_decimal(r.amount):.2f}",
                "note": (r.note or "")[:120],
            })
        for r in ir_qs.order_by("-refunded_at")[:lim]:
            refund_rows.append({
                "kind": "item_refund",
                "id": str(r.id),
                "order_id": str(r.order_id),
                "refunded_at": str(r.refunded_at or ""),
                "payment_method": r.payment_method,
                "amount": f"{_to_decimal(r.amount):.2f}",
                "note": (r.note or "")[:120],
            })

        return {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "totals": {k: f"{v:.2f}" for k, v in totals.items()},
            "rows": rows,
            "refunds_total": f"{refunds_grand:.2f}",
            "refunds_by_method": refunds_by_method,
            "refund_rows": refund_rows,
        }

    def _build_excel(self, report_type: str, payload: dict) -> bytes:
        wb = Workbook()

        if report_type == "analytics":
            ws = wb.active
            ws.title = "Сводка"
            ws.append(["Аналитика кафе — сводка"])
            ws.append(["date_from", payload["date_from"]])
            ws.append(["date_to", payload["date_to"]])
            ws.append(["basis", payload.get("basis", "paid_at")])
            ws.append(["orders_count", payload["orders_count"]])
            ws.append(["items_qty", payload["items_qty"]])
            ws.append(["revenue", payload["revenue"]])
            ws.append(["cogs_sold", payload.get("cogs_sold", "0.00")])
            ws.append(["gross_profit", payload.get("gross_profit", "0.00")])
            ws.append(["margin_percent", payload.get("margin_percent", 0)])
            ws.append(["net_profit", payload.get("net_profit", "0.00")])
            ws.append(["purchases_count", payload["purchases_count"]])
            ws.append(["purchases_sum", payload["purchases_sum"]])
            ws.append(["cafe_expenses_count", payload.get("cafe_expenses_count", 0)])
            ws.append(["cafe_expenses_sum", payload.get("cafe_expenses_sum", "0.00")])
            ws.append(["refunds_total", payload.get("refunds_total", "0.00")])
            ws.append(["low_stock_count", payload["low_stock_count"]])
            ws.append([])
            ws.append(["Возвраты по способу (refunded_at)"])
            ws.append(["method", "method_label", "count", "total"])
            for r in payload.get("refunds_by_method") or []:
                ws.append([r.get("method"), r.get("method_label"), r.get("count"), r.get("total")])

            w_in = wb.create_sheet("Приходы")
            w_in.append(["order_id", "paid_at", "payment_method", "payment_method_label", "amount", "table_number"])
            for row in payload.get("income_rows") or []:
                w_in.append([
                    row["order_id"],
                    row["paid_at"],
                    row["payment_method"],
                    row.get("payment_method_label", ""),
                    row["amount"],
                    row.get("table_number", ""),
                ])

            w_pu = wb.create_sheet("Закупки")
            w_pu.append(["id", "created_at", "supplier", "positions", "amount"])
            for row in payload.get("purchase_rows") or []:
                w_pu.append([row["id"], row["created_at"], row["supplier"], row["positions"], row["amount"]])

            w_ex = wb.create_sheet("Прочие расходы")
            w_ex.append(["id", "expense_date", "title", "category", "amount", "note"])
            for row in payload.get("expense_rows") or []:
                w_ex.append([row["id"], row["expense_date"], row["title"], row["category"], row["amount"], row["note"]])

            w_rf = wb.create_sheet("Возвраты")
            w_rf.append(["kind", "id", "order_id", "refunded_at", "payment_method", "amount", "note"])
            for row in payload.get("refund_rows") or []:
                w_rf.append([
                    row["kind"],
                    row["id"],
                    row["order_id"],
                    row["refunded_at"],
                    row["payment_method"],
                    row["amount"],
                    row.get("note", ""),
                ])

            w_ed = wb.create_sheet("Расходы по дням")
            w_ed.append(["date", "total", "count"])
            for row in payload.get("expenses_by_day") or []:
                w_ed.append([row["date"], row["total"], row["count"]])

            w_top = wb.create_sheet("Топ блюд")
            w_top.append(["title", "qty", "revenue"])
            for row in payload["top_items"]:
                w_top.append([row["title"], row["qty"], row["revenue"]])
        else:
            ws = wb.active
            ws.title = "Сводка"
            ws.append(["Касса — сводка"])
            ws.append(["date_from", payload["date_from"]])
            ws.append(["date_to", payload["date_to"]])
            ws.append(["total_all", payload["totals"]["all"]])
            ws.append(["total_cash", payload["totals"]["cash"]])
            ws.append(["total_card", payload["totals"]["card"]])
            ws.append(["total_transfer", payload["totals"]["transfer"]])
            ws.append(["total_other", payload["totals"]["other"]])
            ws.append(["refunds_total", payload.get("refunds_total", "0.00")])
            ws.append([])
            ws.append(["Возвраты по способу"])
            ws.append(["method", "method_label", "count", "total"])
            for r in payload.get("refunds_by_method") or []:
                ws.append([r.get("method"), r.get("method_label"), r.get("count"), r.get("total")])

            w_ord = wb.create_sheet("Оплаты")
            w_ord.append(["order_id", "paid_at", "payment_method", "final_amount"])
            for row in payload["rows"]:
                w_ord.append([row["order_id"], str(row["paid_at"] or ""), row["payment_method"], row["final_amount"]])

            w_rf = wb.create_sheet("Возвраты")
            w_rf.append(["kind", "id", "order_id", "refunded_at", "payment_method", "amount", "note"])
            for row in payload.get("refund_rows") or []:
                w_rf.append([
                    row["kind"],
                    row["id"],
                    row["order_id"],
                    row["refunded_at"],
                    row["payment_method"],
                    row["amount"],
                    row.get("note", ""),
                ])

        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def _build_word_html(self, report_type: str, payload: dict) -> bytes:
        h = _export_html_escape
        if report_type == "analytics":
            rows = "".join(
                f"<tr><td>{h(r['title'])}</td><td>{h(r['qty'])}</td><td>{h(r['revenue'])}</td></tr>"
                for r in payload["top_items"]
            )
            inc = "".join(
                f"<tr><td>{h(r['order_id'])}</td><td>{h(r['paid_at'])}</td>"
                f"<td>{h(r.get('payment_method_label') or r.get('payment_method') or '')}</td>"
                f"<td>{h(r['amount'])}</td></tr>"
                for r in (payload.get("income_rows") or [])[:200]
            )
            html = f"""
<html><head><meta charset="utf-8"></head><body>
<h2>Cafe analytics report</h2>
<p>date_from: {h(payload["date_from"])}</p>
<p>date_to: {h(payload["date_to"])}</p>
<p>basis: {h(payload.get("basis", "paid_at"))}</p>
<p>orders_count: {h(payload["orders_count"])}</p>
<p>items_qty: {h(payload["items_qty"])}</p>
<p>revenue: {h(payload["revenue"])}</p>
<p>cogs_sold: {h(payload.get("cogs_sold", "0.00"))}</p>
<p>gross_profit: {h(payload.get("gross_profit", "0.00"))}</p>
<p>margin_percent: {h(payload.get("margin_percent", 0))}</p>
<p>net_profit: {h(payload.get("net_profit", "0.00"))}</p>
<p>purchases_count: {h(payload["purchases_count"])}</p>
<p>purchases_sum: {h(payload["purchases_sum"])}</p>
<p>cafe_expenses_count: {h(payload.get("cafe_expenses_count", 0))}</p>
<p>cafe_expenses_sum: {h(payload.get("cafe_expenses_sum", "0.00"))}</p>
<p>refunds_total: {h(payload.get("refunds_total", "0.00"))}</p>
<p>low_stock_count: {h(payload["low_stock_count"])}</p>
<h3>Приходы (фрагмент)</h3>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>order_id</th><th>paid_at</th><th>method</th><th>amount</th></tr>
{inc}
</table>
<h3>Top menu items</h3>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>title</th><th>qty</th><th>revenue</th></tr>
{rows}
</table>
</body></html>
"""
        else:
            rows = "".join(
                f"<tr><td>{h(r['order_id'])}</td><td>{h(r['paid_at'])}</td>"
                f"<td>{h(r['payment_method'])}</td><td>{h(r['final_amount'])}</td></tr>"
                for r in payload["rows"]
            )
            rrows = "".join(
                f"<tr><td>{h(r.get('kind'))}</td><td>{h(r['order_id'])}</td>"
                f"<td>{h(r['refunded_at'])}</td><td>{h(r['amount'])}</td></tr>"
                for r in (payload.get("refund_rows") or [])[:200]
            )
            html = f"""
<html><head><meta charset="utf-8"></head><body>
<h2>Cafe cash report</h2>
<p>date_from: {h(payload["date_from"])}</p>
<p>date_to: {h(payload["date_to"])}</p>
<p>total_all: {h(payload["totals"]["all"])}</p>
<p>total_cash: {h(payload["totals"]["cash"])}</p>
<p>total_card: {h(payload["totals"]["card"])}</p>
<p>total_transfer: {h(payload["totals"]["transfer"])}</p>
<p>total_other: {h(payload["totals"]["other"])}</p>
<p>refunds_total: {h(payload.get("refunds_total", "0.00"))}</p>
<h3>Orders</h3>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>order_id</th><th>paid_at</th><th>payment_method</th><th>final_amount</th></tr>
{rows}
</table>
<h3>Возвраты</h3>
<table border="1" cellspacing="0" cellpadding="4">
<tr><th>kind</th><th>order_id</th><th>refunded_at</th><th>amount</th></tr>
{rrows}
</table>
</body></html>
"""
        return html.encode("utf-8", errors="replace")

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=403)

        report_type = (_query_params(request).get("report") or "analytics").strip().lower()
        export_format = (_query_params(request).get("format") or "excel").strip().lower()
        date_from = _query_params(request).get("date_from")
        date_to = _query_params(request).get("date_to")
        branch = self._active_branch()

        if report_type not in {"analytics", "cash"}:
            return Response({"detail": "report должен быть analytics или cash"}, status=400)
        if export_format not in {"excel", "word"}:
            return Response({"detail": "format должен быть excel или word"}, status=400)

        payload = (
            self._analytics_payload(company, branch, date_from, date_to, request)
            if report_type == "analytics"
            else self._cash_payload(company, branch, date_from, date_to, request)
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
