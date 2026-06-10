"""
Офлайн-режим кафе:

  GET  /api/cafe/offline-snapshot/ — всё для работы без интернета одним запросом
  POST /api/cafe/offline-sync/     — применить очередь действий, накопленных офлайн

Ничего существующего не ломаем: новые view в отдельном модуле, бизнес-логику
(списание склада, закрытие, синхронизацию статуса стола, архив) переиспользуем
через импорт хелперов из apps/cafe/views.py.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Booking, Category, KitchenTask, MenuItem, Order, OrderItem, Table
from .offline_serializers import (
    OfflineCategorySerializer,
    OfflineMenuItemSerializer,
    OfflineOrderSerializer,
    OfflineSyncRequestSerializer,
    OfflineTableSerializer,
    money,
)
# Переиспользуем готовую бизнес-логику — НЕ дублируем учёт.
from .views import (
    CompanyBranchQuerysetMixin,
    _cafe_archive_order_snapshot,
    _sync_table_status,
    deduct_ingredients_for_order,
    send_order_created_notification,
    send_order_updated_notification,
)
from .cache_utils import invalidate_cafe_analytics_cache


class SyncActionError(Exception):
    """Ошибка применения одного action — попадает в failed, не валит весь sync."""


def _user_display(user) -> str:
    if not user:
        return ""
    full = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    if full:
        return full
    return getattr(user, "email", "") or str(getattr(user, "id", ""))


# ============================================================
# 1) SNAPSHOT
# ============================================================
class CafeOfflineSnapshotView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN)

        active_branch = self._active_branch()

        def scope(qs):
            # Тот же принцип, что в CompanyBranchQuerysetMixin.get_queryset:
            # company обязателен, branch — строго активный филиал (если он есть).
            qs = qs.filter(company=company)
            if active_branch is not None:
                qs = qs.filter(branch=active_branch)
            return qs

        now = timezone.now()

        # --- Меню: категории ---
        categories = list(scope(Category.objects.all()).order_by("title"))
        for i, cat in enumerate(categories, start=1):
            cat._sort_order = i

        # --- Меню: позиции (только доступные) ---
        items = (
            scope(MenuItem.objects.filter(is_active=True))
            .select_related("category")
            .order_by("title")
        )

        # --- Столы ---
        tables = list(scope(Table.objects.all()).select_related("zone").order_by("number"))
        reserved_ids = set(
            scope(Booking.objects.filter(date=timezone.localdate()))
            .filter(status__in=[Booking.Status.BOOKED, Booking.Status.ARRIVED])
            .values_list("table_id", flat=True)
        )
        for t in tables:
            t._reserved = t.id in reserved_ids

        # --- Открытые заказы ---
        open_orders = (
            scope(Order.objects.filter(status=Order.Status.OPEN))
            .select_related("table")
            .prefetch_related("items__menu_item")
            .order_by("created_at")
        )

        # --- Текущая кассовая смена (construction.CashShift) ---
        current_shift = self._current_shift(company, active_branch)

        ctx = {"request": request}
        data = {
            "snapshot_at": now,
            "menu": {
                "categories": OfflineCategorySerializer(categories, many=True, context=ctx).data,
                "items": OfflineMenuItemSerializer(items, many=True, context=ctx).data,
            },
            "tables": OfflineTableSerializer(tables, many=True, context=ctx).data,
            "open_orders": OfflineOrderSerializer(open_orders, many=True, context=ctx).data,
            "current_shift": current_shift,
        }
        return Response(data, status=status.HTTP_200_OK)

    @staticmethod
    def _current_shift(company, active_branch):
        try:
            from apps.construction.models import CashShift
        except Exception:
            return None
        qs = CashShift.objects.filter(company=company, status=CashShift.Status.OPEN)
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        shift = qs.select_related("cashier").order_by("-opened_at").first()
        if not shift:
            return None
        return {
            "id": str(shift.id),
            "opened_at": shift.opened_at,
            "employee_id": str(shift.cashier_id) if shift.cashier_id else None,
            "employee_name": _user_display(shift.cashier),
        }


# ============================================================
# 2) SYNC
# ============================================================
class CafeOfflineSyncView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN)

        active_branch = self._active_branch()

        ser = OfflineSyncRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        actions = ser.validated_data["actions"]

        # Сохраняем исходный индекс и сортируем по created_at (вторичный ключ — индекс).
        ordered = sorted(enumerate(actions), key=lambda pair: (pair[1]["created_at"], pair[0]))

        synced = 0
        failed = []
        created_order_ids = []

        for original_index, action in ordered:
            atype = action["type"]
            try:
                created_id = self._apply_action(action, company, active_branch, request.user)
                synced += 1
                if created_id:
                    created_order_ids.append(created_id)
            except SyncActionError as exc:
                failed.append({"action_index": original_index, "type": atype, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 — любой сбой одного action не валит sync
                failed.append({"action_index": original_index, "type": atype, "error": str(exc)})

        return Response(
            {"synced": synced, "failed": failed, "created_order_ids": created_order_ids},
            status=status.HTTP_200_OK,
        )

    # ---------- scoping helpers (защита от чужой компании/филиала) ----------
    def _order_in_scope(self, order_id, company, active_branch):
        if not order_id:
            raise SyncActionError("order_id is required")
        qs = Order.objects.filter(company=company)
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        order = qs.select_for_update().filter(pk=order_id).first()
        if not order:
            raise SyncActionError(f"Order {order_id} not found")
        return order

    def _table_in_scope(self, table_id, company, active_branch):
        if not table_id:
            raise SyncActionError("table_id is required")
        qs = Table.objects.filter(company=company)
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)
        table = qs.filter(pk=table_id).first()
        if not table:
            raise SyncActionError(f"Table {table_id} not found")
        return table

    def _menu_item_for_order(self, menu_item_id, company, order_branch_id):
        if not menu_item_id:
            raise SyncActionError("menu_item_id is required")
        mi = MenuItem.objects.filter(company=company, pk=menu_item_id).first()
        if not mi:
            raise SyncActionError(f"Menu item {menu_item_id} not found")
        if (mi.branch_id or None) != (order_branch_id or None):
            raise SyncActionError(f"Menu item {menu_item_id} belongs to another branch")
        return mi

    @staticmethod
    def _qty(raw):
        try:
            q = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            raise SyncActionError("Invalid quantity")
        if q <= 0:
            raise SyncActionError("Quantity must be positive")
        return q

    # ---------- dispatch ----------
    def _apply_action(self, action, company, active_branch, user):
        atype = action["type"]
        payload = action.get("payload") or {}
        created_at = action["created_at"]

        handler = {
            "CREATE_ORDER": self._create_order,
            "ADD_ITEM_TO_ORDER": self._add_item,
            "REMOVE_ITEM_FROM_ORDER": self._remove_item,
            "CLOSE_ORDER": self._close_order,
            "CANCEL_ORDER": self._cancel_order,
        }[atype]

        with transaction.atomic():
            return handler(payload, created_at, company, active_branch, user)

    # ---------- handlers ----------
    def _create_order(self, payload, created_at, company, active_branch, user):
        table = self._table_in_scope(payload.get("table_id"), company, active_branch)

        # Идемпотентность: если уже есть открытый заказ на этом столе, созданный
        # в пределах ±60с от created_at действия — не плодим дубль.
        window_lo = created_at - timedelta(seconds=60)
        window_hi = created_at + timedelta(seconds=60)
        dup_qs = Order.objects.filter(
            company=company,
            table=table,
            status=Order.Status.OPEN,
            created_at__gte=window_lo,
            created_at__lte=window_hi,
        )
        if active_branch is not None:
            dup_qs = dup_qs.filter(branch=active_branch)
        existing = dup_qs.order_by("created_at").first()
        if existing:
            return str(existing.id)

        order = Order.objects.create(
            company=company,
            branch=active_branch,
            table=table,
            status=Order.Status.OPEN,
            waiter=user if getattr(user, "is_authenticated", False) else None,
        )

        items = payload.get("items") or []
        for row in items:
            mi = self._menu_item_for_order(row.get("menu_item_id"), company, order.branch_id)
            qty = self._qty(row.get("quantity", 1))
            existing_line = OrderItem.objects.filter(order=order, menu_item=mi).first()
            if existing_line:
                existing_line.quantity = (existing_line.quantity or Decimal("0")) + qty
                existing_line.save(update_fields=["quantity"])
            else:
                OrderItem.objects.create(order=order, menu_item=mi, quantity=qty)

        order.recalc_total()
        order.save(update_fields=["total_amount", "updated_at"])

        _sync_table_status(table.id)
        self._notify(send_order_created_notification, order)
        return str(order.id)

    def _add_item(self, payload, created_at, company, active_branch, user):
        order = self._order_in_scope(payload.get("order_id"), company, active_branch)
        if order.status != Order.Status.OPEN:
            raise SyncActionError(f"Order {order.id} is not open")

        mi = self._menu_item_for_order(payload.get("menu_item_id"), company, order.branch_id)
        qty = self._qty(payload.get("quantity", 1))

        # UniqueConstraint(order, menu_item): дублей не создаём — инкрементим кол-во.
        line = OrderItem.objects.filter(order=order, menu_item=mi).first()
        if line:
            line.quantity = (line.quantity or Decimal("0")) + qty
            line.save(update_fields=["quantity"])
        else:
            OrderItem.objects.create(order=order, menu_item=mi, quantity=qty)

        order.recalc_total()
        order.save(update_fields=["total_amount", "updated_at"])
        self._notify(send_order_updated_notification, order)
        return None

    def _remove_item(self, payload, created_at, company, active_branch, user):
        order = self._order_in_scope(payload.get("order_id"), company, active_branch)
        if order.status != Order.Status.OPEN:
            raise SyncActionError(f"Order {order.id} is not open")

        item_id = payload.get("order_item_id")
        if not item_id:
            raise SyncActionError("order_item_id is required")
        line = OrderItem.objects.filter(order=order, pk=item_id).first()
        if not line:
            raise SyncActionError(f"Order item {item_id} not found")
        line.delete()

        order.recalc_total()
        order.save(update_fields=["total_amount", "updated_at"])
        self._notify(send_order_updated_notification, order)
        return None

    def _close_order(self, payload, created_at, company, active_branch, user):
        order = self._order_in_scope(payload.get("order_id"), company, active_branch)
        if order.status == Order.Status.CANCELLED:
            raise SyncActionError(f"Order {order.id} is cancelled")
        if order.status == Order.Status.CLOSED or order.is_paid:
            raise SyncActionError(f"Order {order.id} is already closed")

        pm_map = {
            "cash": Order.PaymentMethod.CASH,
            "card": Order.PaymentMethod.CARD,
            "mixed": Order.PaymentMethod.SPLIT,
        }
        pm = pm_map.get(str(payload.get("payment_method", "cash")).lower())
        if pm is None:
            raise SyncActionError("Invalid payment_method")

        order.recalc_total()
        final_amt = order.final_amount

        # Списываем склад так же, как обычное закрытие (один раз).
        if not order.stock_deducted:
            deduct_ingredients_for_order(order)
            order.stock_deducted = True

        order.payment_method = pm
        order.paid_amount = final_amt
        order.is_paid = True
        order.paid_at = created_at
        order.status = Order.Status.CLOSED
        order.save(update_fields=[
            "total_amount", "paid_amount", "stock_deducted",
            "is_paid", "paid_at", "payment_method", "status", "updated_at",
        ])

        self._cancel_kitchen_tasks(order)
        if order.table_id:
            _sync_table_status(order.table_id)
        _cafe_archive_order_snapshot(order)
        invalidate_cafe_analytics_cache(order.company_id)
        self._notify(send_order_updated_notification, order)
        return None

    def _cancel_order(self, payload, created_at, company, active_branch, user):
        order = self._order_in_scope(payload.get("order_id"), company, active_branch)
        if order.status == Order.Status.CLOSED:
            raise SyncActionError(f"Order {order.id} is closed and cannot be cancelled")
        if order.status == Order.Status.CANCELLED:
            raise SyncActionError(f"Order {order.id} is already cancelled")

        order.status = Order.Status.CANCELLED
        order.canceled_at = created_at
        if getattr(user, "is_authenticated", False):
            order.canceled_by = user
        order.save(update_fields=["status", "canceled_at", "canceled_by", "updated_at"])

        self._cancel_kitchen_tasks(order)
        if order.table_id:
            _sync_table_status(order.table_id)
        _cafe_archive_order_snapshot(order)
        invalidate_cafe_analytics_cache(order.company_id)
        self._notify(send_order_updated_notification, order)
        return None

    # ---------- side effects ----------
    @staticmethod
    def _cancel_kitchen_tasks(order):
        KitchenTask.objects.filter(
            order=order,
            status__in=[KitchenTask.Status.PENDING, KitchenTask.Status.IN_PROGRESS],
        ).update(status=KitchenTask.Status.CANCELLED)

    @staticmethod
    def _notify(fn, order):
        # Сбой WebSocket-уведомления не должен валить применённый action.
        try:
            fn(order)
        except Exception:  # noqa: BLE001
            pass
