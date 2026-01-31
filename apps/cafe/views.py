# apps/cafe/views.py
from decimal import Decimal, InvalidOperation
import json
import uuid
import re

from django.db import transaction, IntegrityError
from django.db.models import Q, Count, Avg, ExpressionWrapper, DurationField, F
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
import django_filters
from rest_framework import filters, generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import logging

logger = logging.getLogger(__name__)

from apps.users.models import Branch
from .models import (
    Zone, Table, Booking, Warehouse, Purchase,
    Category, MenuItem, Ingredient,
    Order, OrderItem, CafeClient,
    OrderHistory, OrderItemHistory,
    KitchenTask, NotificationCafe,
    InventorySession, Equipment, EquipmentInventorySession, Kitchen
)
from .serializers import (
    ZoneSerializer, TableSerializer, BookingSerializer,
    WarehouseSerializer, PurchaseSerializer,
    CategorySerializer, MenuItemSerializer, IngredientInlineSerializer,
    OrderSerializer, OrderItemInlineSerializer,
    CafeClientSerializer,
    OrderHistorySerializer,
    KitchenTaskSerializer, NotificationCafeSerializer,
    InventorySessionSerializer, EquipmentSerializer,
    EquipmentInventorySessionSerializer, KitchenSerializer,
    OrderPaySerializer
)


_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _decimal_from_warehouse_remainder(raw) -> Decimal:
    """
    Warehouse.remainder в cafe — CharField. На практике там часто лежит число строкой,
    иногда с пробелами/единицами. Пробуем вытащить первое число.
    """
    if raw is None:
        return Decimal("0")
    if isinstance(raw, Decimal):
        return raw
    s = str(raw).strip()
    if not s:
        return Decimal("0")
    m = _NUM_RE.search(s)
    if not m:
        return Decimal("0")
    try:
        return Decimal(m.group(0).replace(",", "."))
    except InvalidOperation:
        return Decimal("0")


def deduct_ingredients_for_order(order: Order):
    """
    Списывает со склада ингредиенты по заказу.
    Запускать ТОЛЬКО внутри transaction.atomic().
    """
    # Соберём расход по каждому складу (Warehouse)
    usage_by_product_id: dict = {}

    items = (
        order.items
        .select_related("menu_item")
        .prefetch_related("menu_item__ingredients__product")
        .all()
    )
    for it in items:
        qty = Decimal(str(it.quantity or 0))
        if qty <= 0:
            continue
        for ing in it.menu_item.ingredients.all():
            need = (ing.amount or Decimal("0")) * qty
            if need <= 0:
                continue
            usage_by_product_id[ing.product_id] = usage_by_product_id.get(ing.product_id, Decimal("0")) + need

    if not usage_by_product_id:
        return

    # Залочим нужные строки склада
    products = (
        Warehouse.objects
        .select_for_update()
        .filter(id__in=list(usage_by_product_id.keys()))
    )
    products_by_id = {p.id: p for p in products}

    # Проверка: все ингредиенты должны существовать на складе
    missing = [str(pid) for pid in usage_by_product_id.keys() if pid not in products_by_id]
    if missing:
        raise ValidationError({"detail": f"Не найдены товары склада для ингредиентов: {', '.join(missing)}"})

    # Проверяем остатки и применяем списание
    for pid, need in usage_by_product_id.items():
        p = products_by_id[pid]
        have = _decimal_from_warehouse_remainder(p.remainder)
        new_val = have - need
        if new_val < 0:
            raise ValidationError({
                "detail": (
                    f"Недостаточно на складе: {p.title}. "
                    f"Нужно: {need} {p.unit or ''}, доступно: {have} {p.unit or ''}"
                ).strip()
            })

        # remainder хранится строкой
        p.remainder = str(new_val)
        p.save(update_fields=["remainder"])

try:
    from apps.users.permissions import IsCompanyOwnerOrAdmin
except Exception:
    class IsCompanyOwnerOrAdmin(permissions.BasePermission):  # fallback, пускает только staff/superuser
        def has_permission(self, request, view):
            u = request.user
            return bool(u and u.is_authenticated and (u.is_staff or u.is_superuser))


# --------- company + branch (как в барбере/букинге) ---------
class CompanyBranchQuerysetMixin:
    permission_classes = [permissions.IsAuthenticated]

    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        """
        Компания пользователя:
          - сначала owned_company / company
          - если нет, пробуем взять через user.branch.company
        """
        u = self._user()
        if not u or not getattr(u, "is_authenticated", False):
            return None

        company = getattr(u, "company", None) or getattr(u, "owned_company", None)
        if company:
            return company

        br = getattr(u, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        return None

    def _model_has_field(self, qs, field_name: str) -> bool:
        model = getattr(qs, "model", None)
        if not model:
            return False
        return any(getattr(f, "name", None) == field_name for f in model._meta.get_fields())

    def _fixed_branch_from_user(self, company):
        """
        «Жёсткий» филиал сотрудника (который нельзя менять ?branch):

          - user.primary_branch() / user.primary_branch (если принадлежит company)
          - user.branch (если принадлежит company)
          - если есть branch_ids и там ровно один филиал этой компании — считаем его фиксированным
        """
        user = self._user()
        if not user or not company:
            return None

        company_id = getattr(company, "id", None)

        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == company_id:
                    return val
            except Exception:
                pass

        if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
            return primary

        if hasattr(user, "branch"):
            b = getattr(user, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        branch_ids = getattr(user, "branch_ids", None)
        if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
            try:
                return Branch.objects.get(id=branch_ids[0], company_id=company_id)
            except Branch.DoesNotExist:
                pass

        return None

    def _active_branch(self):
        """
        Определяем активный филиал:

          1) жёстко назначенный филиал (primary / user.branch / единственный из branch_ids)
          2) если жёсткого нет — ?branch=<uuid>, если филиал принадлежит компании
          3) иначе None (работаем по всей компании)

        ВАЖНО: если нашли филиал — он точно из компании пользователя.
        """
        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            return None

        company_id = getattr(company, "id", None)

        fixed = self._fixed_branch_from_user(company)
        if fixed is not None:
            setattr(request, "branch", fixed)
            return fixed

        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                pass

        setattr(request, "branch", None)
        return None

    def get_queryset(self):
        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return qs.none()

        if self._model_has_field(qs, "company"):
            qs = qs.filter(company=company)

        # ВНИМАНИЕ: текущая логика — "жёстко только филиал" для branch-моделей.
        # История заказов обходит это ограничение (см. OrderHistoryListView/ClientOrderHistoryListView).
        if self._model_has_field(qs, "branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                qs = qs.filter(branch=active_branch)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise permissions.PermissionDenied("У пользователя не задана компания.")

        try:
            model_fields = set(f.name for f in serializer.Meta.model._meta.get_fields())
        except Exception:
            model_fields = set()

        kwargs = {"company": company}

        if "branch" in model_fields:
            active_branch = self._active_branch()
            if active_branch is not None:
                kwargs["branch"] = active_branch

        serializer.save(**kwargs)

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise permissions.PermissionDenied("У пользователя не задана компания.")
        serializer.save(company=company)


# ==================== CafeClient ====================
class CafeClientListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = CafeClient.objects.all()
    serializer_class = CafeClientSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["name", "phone"]
    search_fields = ["name", "phone"]
    ordering_fields = ["name", "id"]


class CafeClientRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = CafeClient.objects.all()
    serializer_class = CafeClientSerializer


# --------- Filters ---------
class _CharInFilter(django_filters.BaseInFilter, django_filters.CharFilter):
    """Поддержка query param вида: ?status__in=open,closed"""


class OrderFilter(django_filters.FilterSet):
    # статус (точно)
    status = django_filters.CharFilter(field_name="status")
    # статус (несколько)
    status__in = _CharInFilter(field_name="status", lookup_expr="in")

    # диапазон дат (удобные имена параметров)
    created_at_from = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_at_to = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = Order
        fields = ["table", "waiter", "client", "guests", "status"]


class ClientOrderListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    /cafe/clients/<uuid:pk>/orders/
    GET  — список заказов клиента
    POST — создать заказ этому клиенту (company и client проставляются автоматически)
    """
    queryset = (
        Order.objects
        .select_related("table", "waiter", "company", "client")
        .prefetch_related("items__menu_item")
    )
    serializer_class = OrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = OrderFilter
    ordering_fields = ["created_at", "guests", "id"]

    def _get_client(self):
        company = self._user_company()
        return generics.get_object_or_404(CafeClient, pk=self.kwargs["pk"], company=company)

    def get_queryset(self):
        return super().get_queryset().filter(client=self._get_client())

    def perform_create(self, serializer):
        client = self._get_client()
        super().perform_create(serializer)
        order = serializer.instance
        order.client = client
        order.company = client.company
        order.save(update_fields=["client", "company"])
        # Пересчитываем сумму заказа
        order.recalc_total()
        order.save(update_fields=["total_amount"])
        
        # Устанавливаем стол как занятый при создании заказа
        if order.table_id:
            with transaction.atomic():
                table = Table.objects.select_for_update().get(id=order.table_id)
                if table.status != Table.Status.BUSY:
                    table.status = Table.Status.BUSY
                    table.save(update_fields=["status"])
                    # Отправляем уведомление об изменении статуса стола
                    send_table_status_changed_notification(table)
        
        # Отправляем WebSocket уведомление о создании заказа
        send_order_created_notification(order)


# -------- История заказов клиента (вложенно) --------
class ClientOrderHistoryListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    /cafe/clients/<uuid:pk>/orders/history/
    История (архив) заказов конкретного клиента в рамках компании пользователя.

    ВАЖНО: здесь НЕ используем super().get_queryset(), потому что миксин
    режет branch до "только branch=active_branch" и убивает global-историю.
    """
    serializer_class = OrderHistorySerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["table", "waiter", "created_at", "archived_at", "guests"]
    ordering_fields = ["created_at", "archived_at", "id"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return OrderHistory.objects.none()

        client = generics.get_object_or_404(CafeClient, pk=self.kwargs["pk"], company=company)

        qs = (
            OrderHistory.objects
            .select_related("client", "table", "waiter", "company")
            .filter(company=company, client=client)
        )

        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))

        return qs.order_by("-created_at")


# -------- Общая история заказов по компании --------
class OrderHistoryListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    /cafe/orders/history/
    История (архив) всех заказов компании.

    ВАЖНО: здесь НЕ используем super().get_queryset() по той же причине:
    миксин "жёстко только филиал" убивает global-историю и/или историю других филиалов.
    """
    serializer_class = OrderHistorySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["client", "table", "waiter", "created_at", "archived_at", "guests"]
    search_fields = ["client__name", "client__phone"]
    ordering_fields = ["created_at", "archived_at", "id"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return OrderHistory.objects.none()

        qs = (
            OrderHistory.objects
            .select_related("client", "table", "waiter", "company")
            .filter(company=company)
        )

        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))

        return qs.order_by("-created_at")


# ==================== Zone ====================
class ZoneListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["title"]
    search_fields = ["title"]
    ordering_fields = ["title", "id"]


class ZoneRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer


# ==================== Table ====================
class TableListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Table.objects.select_related("zone", "company").all()
    serializer_class = TableSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["zone", "number", "places", "status"]
    search_fields = ["zone__title"]
    ordering_fields = ["number", "places", "status", "id"]
    
    def perform_create(self, serializer):
        super().perform_create(serializer)
        table = serializer.instance
        # Отправляем WebSocket уведомление о создании стола
        send_table_created_notification(table)


class TableRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Table.objects.select_related("zone", "company").all()
    serializer_class = TableSerializer
    
    def perform_update(self, serializer):
        super().perform_update(serializer)
        table = serializer.instance
        # Отправляем WebSocket уведомление об обновлении стола
        send_table_updated_notification(table)


# ==================== Booking ====================
class BookingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Booking.objects.select_related("table", "company").all()
    serializer_class = BookingSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["date", "time", "table", "status", "guests"]
    search_fields = ["guest", "phone"]
    ordering_fields = ["date", "time", "guests", "id"]


class BookingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Booking.objects.select_related("table", "company").all()
    serializer_class = BookingSerializer


# ==================== Warehouse ====================
class WarehouseListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["title", "unit"]
    search_fields = ["title", "unit"]
    ordering_fields = ["title", "id"]

    def perform_create(self, serializer):
        try:
            serializer.save()
        except IntegrityError as e:
            msg = str(e)
            if "uniq_warehouse_title_" in msg:
                raise ValidationError({"title": "Склад с таким названием уже существует в этой компании или филиале."})
            raise


class WarehouseRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer

    def perform_update(self, serializer):
        try:
            serializer.save()
        except IntegrityError as e:
            msg = str(e)
            if "uniq_warehouse_title_" in msg:
                raise ValidationError({"title": "Склад с таким названием уже существует в этой компании или филиале."})
            raise


# ==================== Purchase ====================
class PurchaseListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Purchase.objects.all()
    serializer_class = PurchaseSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["supplier"]
    search_fields = ["supplier"]
    ordering_fields = ["price", "id"]


class PurchaseRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Purchase.objects.all()
    serializer_class = PurchaseSerializer


# ==================== Category ====================
class CategoryListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["title"]
    search_fields = ["title"]
    ordering_fields = ["title", "id"]


class CategoryRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


# ==================== MenuItem (+ ingredients nested) ====================
class MenuItemListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = (
        MenuItem.objects
        .select_related("category", "company")
        .prefetch_related("ingredients__product")
        .all()
    )
    serializer_class = MenuItemSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["category", "kitchen", "is_active", "title"]
    search_fields = ["title", "category__title"]
    ordering_fields = ["title", "price", "is_active", "id"]

    def get_queryset(self):
        """
        Для меню обычно нужно видеть и "глобальные" позиции (branch=NULL),
        даже когда выбран конкретный филиал (?branch=...).
        """
        qs = self.queryset.all()
        company = self._user_company()
        if not company:
            return qs.none()

        qs = qs.filter(company=company)

        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))

        return qs


class MenuItemRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = (
        MenuItem.objects
        .select_related("category", "company")
        .prefetch_related("ingredients__product")
        .all()
    )
    serializer_class = MenuItemSerializer


# ==================== Ingredient ====================
class IngredientListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = IngredientInlineSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["menu_item", "product"]
    ordering_fields = ["id"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return Ingredient.objects.none()
        active_branch = self._active_branch()
        qs = Ingredient.objects.select_related("menu_item", "product")
        qs = qs.filter(menu_item__company=company, product__company=company)
        if active_branch is not None:
            return qs.filter(
                Q(menu_item__branch=active_branch) | Q(menu_item__branch__isnull=True),
                Q(product__branch=active_branch) | Q(product__branch__isnull=True),
            )
        return qs.filter(menu_item__branch__isnull=True, product__branch__isnull=True)


class IngredientRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IngredientInlineSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return Ingredient.objects.none()
        active_branch = self._active_branch()
        qs = Ingredient.objects.select_related("menu_item", "product").filter(
            menu_item__company=company, product__company=company
        )
        if active_branch is not None:
            return qs.filter(
                Q(menu_item__branch=active_branch) | Q(menu_item__branch__isnull=True),
                Q(product__branch=active_branch) | Q(product__branch__isnull=True),
            )
        return qs.filter(menu_item__branch__isnull=True, product__branch__isnull=True)


# ==================== Order ====================
class OrderListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = (
        Order.objects
        .select_related("table", "waiter", "company", "client")
        .prefetch_related("items__menu_item")
    )
    serializer_class = OrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = OrderFilter
    ordering_fields = ["created_at", "guests", "id"]
    
    def perform_create(self, serializer):
        super().perform_create(serializer)
        order = serializer.instance
        # Пересчитываем сумму заказа
        order.recalc_total()
        order.save(update_fields=["total_amount"])
        
        # Устанавливаем стол как занятый при создании заказа
        if order.table_id:
            with transaction.atomic():
                table = Table.objects.select_for_update().get(id=order.table_id)
                if table.status != Table.Status.BUSY:
                    table.status = Table.Status.BUSY
                    table.save(update_fields=["status"])
                    # Отправляем уведомление об изменении статуса стола
                    send_table_status_changed_notification(table)
        
        # Отправляем WebSocket уведомление о создании заказа
        send_order_created_notification(order)


class OrderRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = (
        Order.objects
        .select_related("table", "waiter", "company", "client")
        .prefetch_related("items__menu_item")
    )
    serializer_class = OrderSerializer
    
    def perform_update(self, serializer):
        old_status = None
        old_table_id = None
        if serializer.instance:
            old_status = serializer.instance.status
            old_table_id = serializer.instance.table_id
        
        super().perform_update(serializer)
        order = serializer.instance
        # Пересчитываем сумму заказа при обновлении
        order.recalc_total()
        order.save(update_fields=["total_amount"])
        
        # Если заказ закрыт или отменен - освобождаем стол и отменяем незавершенные задачи кухни
        if order.status in [Order.Status.CLOSED, Order.Status.CANCELLED]:
            # Отменяем незавершенные задачи кухни (pending и in_progress)
            if old_status not in [Order.Status.CLOSED, Order.Status.CANCELLED]:
                unfinished_tasks = KitchenTask.objects.filter(
                    order=order,
                    status__in=[KitchenTask.Status.PENDING, KitchenTask.Status.IN_PROGRESS]
                )
                if unfinished_tasks.exists():
                    unfinished_tasks.update(status=KitchenTask.Status.CANCELLED)
            
            if order.table_id:
                with transaction.atomic():
                    table = Table.objects.select_for_update().get(id=order.table_id)
                    if table.status != Table.Status.FREE:
                        table.status = Table.Status.FREE
                        table.save(update_fields=["status"])
                        # Отправляем уведомление об изменении статуса стола
                        send_table_status_changed_notification(table)
        
        # Если статус изменился с закрытого/отмененного на открытый - занимаем стол
        elif order.status == Order.Status.OPEN and old_status in [Order.Status.CLOSED, Order.Status.CANCELLED]:
            if order.table_id:
                with transaction.atomic():
                    table = Table.objects.select_for_update().get(id=order.table_id)
                    if table.status != Table.Status.BUSY:
                        table.status = Table.Status.BUSY
                        table.save(update_fields=["status"])
                        # Отправляем уведомление об изменении статуса стола
                        send_table_status_changed_notification(table)
        
        # Отправляем WebSocket уведомление об обновлении заказа
        send_order_updated_notification(order)
    
    def perform_destroy(self, instance):
        table_id = instance.table_id
        super().perform_destroy(instance)
        
        # При удалении заказа освобождаем стол, если нет других открытых заказов на этот стол
        if table_id:
            with transaction.atomic():
                # Проверяем, есть ли другие открытые заказы на этот стол
                has_open_orders = Order.objects.filter(
                    table_id=table_id,
                    status=Order.Status.OPEN
                ).exists()
                
                if not has_open_orders:
                    table = Table.objects.select_for_update().get(id=table_id)
                    if table.status != Table.Status.FREE:
                        table.status = Table.Status.FREE
                        table.save(update_fields=["status"])
                        # Отправляем уведомление об изменении статуса стола
                        send_table_status_changed_notification(table)


# ==================== OrderItem ====================
class OrderItemListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = OrderItemInlineSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["order", "menu_item"]
    ordering_fields = ["id"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return OrderItem.objects.none()
        active_branch = self._active_branch()
        qs = OrderItem.objects.select_related("order", "menu_item")
        qs = qs.filter(order__company=company, menu_item__company=company)
        if active_branch is not None:
            return qs.filter(
                Q(order__branch=active_branch) | Q(order__branch__isnull=True),
                Q(menu_item__branch=active_branch) | Q(menu_item__branch__isnull=True),
            )
        return qs.filter(order__branch__isnull=True, menu_item__branch__isnull=True)


class OrderItemRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderItemInlineSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return OrderItem.objects.none()
        active_branch = self._active_branch()
        qs = OrderItem.objects.select_related("order", "menu_item").filter(
            order__company=company, menu_item__company=company
        )
        if active_branch is not None:
            return qs.filter(
                Q(order__branch=active_branch) | Q(order__branch__isnull=True),
                Q(menu_item__branch=active_branch) | Q(menu_item__branch__isnull=True),
            )
        return qs.filter(order__branch__isnull=True, menu_item__branch__isnull=True)


class OrderPayView(CompanyBranchQuerysetMixin, APIView):
    """
    POST /cafe/orders/<uuid:pk>/pay/
    body:
      {
        "payment_method": "cash|card|transfer",
        "discount_amount": "0.00",
        "close_order": true
      }

    ВАЖНО: архивируем в OrderHistory сразу (история НЕ зависит от удаления заказа).
    """
    def post(self, request, pk):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            Order.objects
            .select_related("table", "client", "waiter")
            .prefetch_related("items__menu_item")
            .filter(company=company)
        )

        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(branch=active_branch)

        ser = OrderPaySerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        payment_method = ser.validated_data.get("payment_method", "cash")
        discount_amount = ser.validated_data.get("discount_amount", Decimal("0"))
        close_order = ser.validated_data.get("close_order", True)

        with transaction.atomic():
            # Забираем заказ с блокировкой, чтобы не было двойной оплаты/списания
            locked_qs = (
                Order.objects
                # В Postgres нельзя FOR UPDATE на nullable side OUTER JOIN,
                # поэтому лочим только сам Order (без client/waiter).
                .select_for_update(of=("self",))
                .select_related("table")
                .prefetch_related("items__menu_item__ingredients__product")
                .filter(company=company)
            )
            if active_branch is not None:
                locked_qs = locked_qs.filter(branch=active_branch)
            order = generics.get_object_or_404(locked_qs, pk=pk)

            if getattr(order, "is_paid", False):
                return Response({"detail": "Заказ уже оплачен."}, status=status.HTTP_400_BAD_REQUEST)

            order.recalc_total()

            if discount_amount and discount_amount > order.total_amount:
                return Response({"detail": "Скидка больше суммы заказа."}, status=status.HTTP_400_BAD_REQUEST)

            # Списываем ингредиенты "по продаже" — в момент оплаты (один раз).
            deduct_ingredients_for_order(order)

            order.discount_amount = discount_amount or Decimal("0")
            order.is_paid = True
            order.paid_at = timezone.now()
            order.payment_method = payment_method

            if close_order:
                order.status = Order.Status.CLOSED

            order.save(update_fields=[
                "total_amount", "discount_amount",
                "is_paid", "paid_at", "payment_method",
                "status", "updated_at",
            ])

            # При закрытии заказа отменяем незавершенные задачи кухни
            if close_order:
                # Отменяем задачи в статусе PENDING и IN_PROGRESS
                unfinished_tasks = KitchenTask.objects.filter(
                    order=order,
                    status__in=[KitchenTask.Status.PENDING, KitchenTask.Status.IN_PROGRESS]
                )
                if unfinished_tasks.exists():
                    unfinished_tasks.update(status=KitchenTask.Status.CANCELLED)

            if close_order and order.table_id:
                with transaction.atomic():
                    table = Table.objects.select_for_update().get(id=order.table_id)
                    table.status = Table.Status.FREE
                    table.save(update_fields=["status"])
                    # Отправляем уведомление об изменении статуса стола
                    send_table_status_changed_notification(table)

            # ---------- ARCHIVE (OrderHistory) ----------
            waiter_label = ""
            if order.waiter_id:
                full = getattr(order.waiter, "get_full_name", lambda: "")() or ""
                email = getattr(order.waiter, "email", "") or ""
                waiter_label = full or email or str(order.waiter_id)

            oh, _created = OrderHistory.objects.update_or_create(
                original_order_id=order.id,
                defaults={
                    "company": order.company,
                    "branch": order.branch,
                    "client": order.client,
                    "table": order.table,
                    "table_number": (order.table.number if order.table_id else None),
                    "waiter": order.waiter,
                    "waiter_label": waiter_label,
                    "guests": order.guests,
                    "created_at": order.created_at,
                    "status": order.status,
                    "is_paid": order.is_paid,
                    "paid_at": order.paid_at,
                    "payment_method": order.payment_method,
                    "total_amount": order.total_amount,
                    "discount_amount": order.discount_amount,
                }
            )

            OrderItemHistory.objects.filter(order_history=oh).delete()
            items = [
                OrderItemHistory(
                    order_history=oh,
                    menu_item=it.menu_item,
                    menu_item_title=it.menu_item.title,
                    menu_item_price=it.menu_item.price,
                    quantity=it.quantity,
                )
                for it in order.items.select_related("menu_item")
            ]
            if items:
                OrderItemHistory.objects.bulk_create(items)

        # Отправляем WebSocket уведомление об обновлении заказа
        send_order_updated_notification(order)
        
        return Response({
            "id": str(order.id),
            "status": order.status,
            "is_paid": order.is_paid,
            "paid_at": order.paid_at,
            "payment_method": order.payment_method,
            "total_amount": str(order.total_amount),
            "discount_amount": str(order.discount_amount),
            "final_amount": str(order.total_amount - (order.discount_amount or Decimal("0"))),
        }, status=status.HTTP_200_OK)


class OrderClosedListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    GET /cafe/orders/closed/
    Живые закрытые/отменённые/оплаченные заказы (не архив).
    """
    queryset = (
        Order.objects
        .select_related("table", "waiter", "company", "client")
        .prefetch_related("items__menu_item")
    )
    serializer_class = OrderSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["table", "waiter", "client", "guests", "created_at", "status", "is_paid"]
    search_fields = ["client__name", "client__phone"]
    ordering_fields = ["created_at", "id"]

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(Q(status__in=[Order.Status.CLOSED, Order.Status.CANCELLED]) | Q(is_paid=True))


# ==================== Kitchen (повар) ====================
class KitchenTaskListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    Лента задач для повара:
      - по умолчанию pending + in_progress
      - ?mine=1 — только мои
      - ?status=... — конкретный статус
    """
    queryset = KitchenTask.objects.select_related('order__table', 'menu_item', 'waiter', 'cook')
    serializer_class = KitchenTaskSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    # status фильтруем вручную (поддержка списка через запятую), поэтому тут не включаем
    filterset_fields = ['cook', 'waiter', 'menu_item', 'order']
    ordering_fields = ['created_at', 'started_at', 'finished_at', 'status']

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        # Поддерживаем:
        #   - ?status=pending            (один статус)
        #   - ?status=pending,in_progress (список через запятую)
        #   - ?status=pending&status=in_progress (повторяющийся параметр)
        status_list = self.request.query_params.getlist('status')
        if len(status_list) == 1 and status_list[0] and ',' in status_list[0]:
            status_list = [s.strip() for s in status_list[0].split(',') if s.strip()]
        mine = self.request.query_params.get('mine')

        if status_list:
            if len(status_list) == 1:
                qs = qs.filter(status=status_list[0])
            else:
                qs = qs.filter(status__in=status_list)
        else:
            qs = qs.filter(status__in=[KitchenTask.Status.PENDING, KitchenTask.Status.IN_PROGRESS])

        if mine in ('1', 'true', 'True'):
            qs = qs.filter(cook=user)
        else:
            qs = qs.filter(Q(cook__isnull=True) | Q(cook=user))
        return qs


class KitchenTaskClaimView(CompanyBranchQuerysetMixin, APIView):
    """
    POST /cafe/kitchen/tasks/<uuid:pk>/claim/
    Берёт задачу в работу: pending -> in_progress, cook = request.user
    """
    def post(self, request, pk):
        company = self._user_company()
        user = request.user
        with transaction.atomic():
            updated = (
                KitchenTask.objects
                .select_for_update()
                .filter(
                    pk=pk, company=company,
                    status=KitchenTask.Status.PENDING, cook__isnull=True
                )
                .update(
                    status=KitchenTask.Status.IN_PROGRESS,
                    cook=user, started_at=timezone.now()
                )
            )
            if not updated:
                return Response(
                    {"detail": "Задачу уже взяли или статус не 'pending'."},
                    status=status.HTTP_409_CONFLICT
                )

        obj = KitchenTask.objects.select_related('order__table', 'menu_item', 'waiter', 'cook').get(pk=pk)
        return Response(KitchenTaskSerializer(obj, context={'request': request}).data)


class KitchenTaskReadyView(CompanyBranchQuerysetMixin, APIView):
    """
    POST /cafe/kitchen/tasks/<uuid:pk>/ready/
    Отмечает задачу как готовую: in_progress -> ready, уведомляет официанта.
    """
    def post(self, request, pk):
        company = self._user_company()
        user = request.user
        with transaction.atomic():
            task = generics.get_object_or_404(
                KitchenTask.objects.select_for_update(),
                pk=pk, company=company, cook=user, status=KitchenTask.Status.IN_PROGRESS
            )
            task.status = KitchenTask.Status.READY
            task.finished_at = timezone.now()
            task.save(update_fields=['status', 'finished_at'])

            if task.waiter_id:
                NotificationCafe.objects.create(
                    company=task.company,
                    branch=task.branch,
                    recipient=task.waiter,
                    type='kitchen_ready',
                    message=f'Готово: {task.menu_item.title} (стол {task.order.table.number})',
                    payload={
                        "task_id": str(task.id),
                        "order_id": str(task.order_id),
                        "table": task.order.table.number,
                        "menu_item": task.menu_item.title,
                        "unit_index": task.unit_index,
                    }
                )

        # Отправляем WebSocket событие о готовности блюда
        send_kitchen_task_ready_notification(task)

        return Response(KitchenTaskSerializer(task, context={'request': request}).data)


class KitchenTaskRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/DELETE /cafe/kitchen/tasks/<uuid:pk>/
    Позволяет получать, обновлять (включая статус) и удалять задачи кухни.
    """
    queryset = KitchenTask.objects.select_related('order__table', 'menu_item', 'waiter', 'cook')
    serializer_class = KitchenTaskSerializer

    def perform_update(self, serializer):
        old_status = None
        old_started_at = None
        old_finished_at = None
        if serializer.instance:
            old_status = serializer.instance.status
            old_started_at = serializer.instance.started_at
            old_finished_at = serializer.instance.finished_at
        
        # Определяем, нужно ли устанавливать started_at или finished_at
        new_status = serializer.validated_data.get('status', old_status)
        update_fields = []
        
        # Если статус меняется на IN_PROGRESS, устанавливаем started_at и cook
        if new_status == KitchenTask.Status.IN_PROGRESS and old_status != new_status:
            if not old_started_at:
                serializer.validated_data['started_at'] = timezone.now()
                update_fields.append('started_at')
            if not serializer.instance.cook_id:
                serializer.validated_data['cook'] = self.request.user
                update_fields.append('cook')
        
        # Если статус меняется на READY, устанавливаем finished_at
        if new_status == KitchenTask.Status.READY and old_status != new_status:
            if not old_finished_at:
                serializer.validated_data['finished_at'] = timezone.now()
                update_fields.append('finished_at')
        
        # Выполняем обновление
        super().perform_update(serializer)
        task = serializer.instance
        
        # Отправляем уведомление официанту при переходе в READY
        if new_status == KitchenTask.Status.READY and old_status != new_status and task.waiter_id:
            NotificationCafe.objects.create(
                company=task.company,
                branch=task.branch,
                recipient=task.waiter,
                type='kitchen_ready',
                message=f'Готово: {task.menu_item.title} (стол {task.order.table.number})',
                payload={
                    "task_id": str(task.id),
                    "order_id": str(task.order_id),
                    "table": task.order.table.number,
                    "menu_item": task.menu_item.title,
                    "unit_index": task.unit_index,
                }
            )

        # WebSocket событие о готовности блюда (для поваров/монитора)
        if new_status == KitchenTask.Status.READY and old_status != new_status:
            send_kitchen_task_ready_notification(task)


class KitchenTaskMonitorView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    /cafe/kitchen/tasks/monitor/
    Видят владелец/админ/staff. Все задачи по компании.
    """
    permission_classes = [permissions.IsAuthenticated, IsCompanyOwnerOrAdmin]
    queryset = KitchenTask.objects.select_related('order__table', 'menu_item', 'waiter', 'cook')
    serializer_class = KitchenTaskSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'cook', 'waiter', 'menu_item', 'order']
    ordering_fields = ['created_at', 'started_at', 'finished_at', 'status']

    def get_queryset(self):
        qs = super().get_queryset()
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs


class KitchenAnalyticsBaseView(CompanyBranchQuerysetMixin, APIView):
    """Агрегация по cook или waiter. ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD"""
    group_field = None  # 'cook' или 'waiter'

    def get(self, request):
        if not self.group_field:
            return Response({"detail": "group_field not set"}, status=500)

        qs = KitchenTask.objects.filter(company=self._user_company())
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
        else:
            qs = qs.filter(branch__isnull=True)

        df = request.query_params.get('date_from')
        dt = request.query_params.get('date_to')
        if df:
            qs = qs.filter(created_at__date__gte=df)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

        lead_time = ExpressionWrapper(F('finished_at') - F('started_at'), output_field=DurationField())
        data = (
            qs.values(self.group_field)
            .annotate(
                total=Count('id'),
                taken=Count('id', filter=Q(status__in=[KitchenTask.Status.IN_PROGRESS, KitchenTask.Status.READY])),
                ready=Count('id', filter=Q(status=KitchenTask.Status.READY)),
                avg_lead=Avg(lead_time, filter=Q(status=KitchenTask.Status.READY)),
            )
            .order_by('-ready', '-total')
        )

        result = []
        for row in data:
            uid = row[self.group_field]
            result.append({
                self.group_field: uid,
                "total": row["total"],
                "taken": row["taken"],
                "ready": row["ready"],
                "avg_lead_seconds": (row["avg_lead"].total_seconds() if row["avg_lead"] else None),
            })
        return Response(result)


class KitchenAnalyticsByCookView(KitchenAnalyticsBaseView):
    group_field = 'cook'


class KitchenAnalyticsByWaiterView(KitchenAnalyticsBaseView):
    group_field = 'waiter'


class NotificationListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    serializer_class = NotificationCafeSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    ordering_fields = ['created_at']

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return NotificationCafe.objects.none()
        return NotificationCafe.objects.filter(company=company, recipient=self.request.user).order_by('-created_at')


class InventorySessionListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /cafe/inventory/sessions/      — список актов инвентаризации (по компании/филиалу)
    POST /cafe/inventory/sessions/      — создать акт с items
    """
    queryset = InventorySession.objects.select_related("company", "branch", "created_by").all()
    serializer_class = InventorySessionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_confirmed", "created_at", "confirmed_at"]
    search_fields = ["comment"]
    ordering_fields = ["created_at", "confirmed_at", "id"]


class InventorySessionRetrieveView(CompanyBranchQuerysetMixin, generics.RetrieveAPIView):
    queryset = InventorySession.objects.select_related("company", "branch", "created_by").prefetch_related("items__product")
    serializer_class = InventorySessionSerializer


class InventorySessionConfirmView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    queryset = InventorySession.objects.all()
    serializer_class = InventorySessionSerializer

    def post(self, request, *args, **kwargs):
        session = self.get_object()
        if session.is_confirmed:
            return Response({"detail": "Уже подтверждено."}, status=status.HTTP_200_OK)
        session.confirm(user=request.user)
        data = self.get_serializer(session).data
        return Response(data, status=status.HTTP_200_OK)


# ==================== INVENTORY: оборудование ====================
class EquipmentListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Equipment.objects.select_related("company", "branch")
    serializer_class = EquipmentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["category", "condition", "is_active"]
    search_fields = ["title", "serial_number", "category", "notes"]
    ordering_fields = ["title", "condition", "is_active", "purchase_date", "id"]


class EquipmentRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Equipment.objects.select_related("company", "branch")
    serializer_class = EquipmentSerializer


class EquipmentInventorySessionListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = EquipmentInventorySession.objects.select_related("company", "branch", "created_by")
    serializer_class = EquipmentInventorySessionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_confirmed", "created_at", "confirmed_at"]
    search_fields = ["comment"]
    ordering_fields = ["created_at", "confirmed_at", "id"]


class EquipmentInventorySessionRetrieveView(CompanyBranchQuerysetMixin, generics.RetrieveAPIView):
    queryset = (
        EquipmentInventorySession.objects
        .select_related("company", "branch", "created_by")
        .prefetch_related("items__equipment")
    )
    serializer_class = EquipmentInventorySessionSerializer


class EquipmentInventorySessionConfirmView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    queryset = EquipmentInventorySession.objects.all()
    serializer_class = EquipmentInventorySessionSerializer

    def post(self, request, *args, **kwargs):
        session = self.get_object()
        if session.is_confirmed:
            return Response({"detail": "Уже подтверждено."}, status=status.HTTP_200_OK)
        session.confirm(user=request.user)
        data = self.get_serializer(session).data
        return Response(data, status=status.HTTP_200_OK)


# ==================== Kitchen ====================
class KitchenListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Kitchen.objects.all()
    serializer_class = KitchenSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["title", "number"]
    search_fields = ["title"]
    ordering_fields = ["number", "title", "id"]

    def get_queryset(self):
        """
        Аналогично menu-items: при выбранном филиале показываем и кухни компании
        без филиала (branch=NULL), чтобы их можно было использовать во всех филиалах.
        """
        qs = self.queryset.all()
        company = self._user_company()
        if not company:
            return qs.none()

        qs = qs.filter(company=company)

        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))

        return qs

    def perform_create(self, serializer):
        try:
            serializer.save()
        except IntegrityError as e:
            msg = str(e)
            if "uniq_kitchen_number_" in msg:
                raise ValidationError({"number": "Кухня с таким номером уже существует."})
            if "uniq_kitchen_title_" in msg:
                raise ValidationError({"title": "Кухня с таким названием уже существует."})
            raise


class KitchenRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Kitchen.objects.all()
    serializer_class = KitchenSerializer


# ==================== WebSocket уведомления ====================
def send_order_created_notification(order):
    """
    Отправляет WebSocket уведомление о создании заказа.
    """
    try:
        logger.info(f"[send_order_created_notification] Starting: order_id={order.id}")
        channel_layer = get_channel_layer()
        if not channel_layer:
            logger.warning(f"[send_order_created_notification] Channel layer not configured")
            return
        
        company_id = str(order.company_id)
        branch_id = str(order.branch_id) if order.branch_id else None
        
        # Формируем имя группы
        if branch_id:
            group_name = f"cafe_orders_{company_id}_{branch_id}"
        else:
            group_name = f"cafe_orders_{company_id}"
        
        logger.info(f"[send_order_created_notification] Sending to group: {group_name}, order_id={order.id}")
        
        # Сериализуем данные заказа
        from .serializers import OrderSerializer
        serializer = OrderSerializer(order)
        order_data = serializer.data
        
        # Конвертируем UUID и Decimal в строки для msgpack сериализации
        order_data = json.loads(json.dumps(order_data, default=str))
        
        logger.debug(f"[send_order_created_notification] Order data serialized: {len(str(order_data))} chars")
        
        # Отправляем уведомление в группу
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "order_created",
                "payload": {
                    "order": order_data,
                    "company_id": company_id,
                    "branch_id": branch_id,
                }
            }
        )
        logger.info(f"[send_order_created_notification] Message sent to channel layer: group={group_name}")
    except Exception as e:
        logger.error(f"[send_order_created_notification] Error sending notification: {e}", exc_info=True)


def send_order_updated_notification(order):
    """
    Отправляет WebSocket уведомление об обновлении заказа.
    """
    try:
        logger.info(f"[send_order_updated_notification] Starting: order_id={order.id}")
        channel_layer = get_channel_layer()
        if not channel_layer:
            logger.warning(f"[send_order_updated_notification] Channel layer not configured")
            return
        
        company_id = str(order.company_id)
        branch_id = str(order.branch_id) if order.branch_id else None
        
        # Формируем имя группы
        if branch_id:
            group_name = f"cafe_orders_{company_id}_{branch_id}"
        else:
            group_name = f"cafe_orders_{company_id}"
        
        logger.info(f"[send_order_updated_notification] Sending to group: {group_name}, order_id={order.id}")
        
        # Сериализуем данные заказа
        from .serializers import OrderSerializer
        serializer = OrderSerializer(order)
        order_data = serializer.data
        
        # Конвертируем UUID и Decimal в строки для msgpack сериализации
        order_data = json.loads(json.dumps(order_data, default=str))
        
        logger.debug(f"[send_order_updated_notification] Order data serialized: {len(str(order_data))} chars")
        
        # Отправляем уведомление в группу
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "order_updated",
                "payload": {
                    "order": order_data,
                    "company_id": company_id,
                    "branch_id": branch_id,
                }
            }
        )
        logger.info(f"[send_order_updated_notification] Message sent to channel layer: group={group_name}")
    except Exception as e:
        logger.error(f"[send_order_updated_notification] Error sending notification: {e}", exc_info=True)


def send_kitchen_task_ready_notification(task):
    """
    Отправляет WebSocket уведомление о готовности блюда (задачи кухни).
    Событие уходит:
      - в группу заказов (для официантов/зала): cafe_orders_{company_id}_{branch_id?}
      - в группу кухни (для поваров/монитора кухни): cafe_kitchen_{company_id}_{branch_id?}
    """
    try:
        logger.info(f"[send_kitchen_task_ready_notification] Starting: task_id={task.id}")
        channel_layer = get_channel_layer()
        if not channel_layer:
            logger.warning("[send_kitchen_task_ready_notification] Channel layer not configured")
            return

        company_id = str(task.company_id)
        branch_id = str(task.branch_id) if task.branch_id else None

        # Формируем имя группы заказов
        if branch_id:
            orders_group_name = f"cafe_orders_{company_id}_{branch_id}"
        else:
            orders_group_name = f"cafe_orders_{company_id}"

        # Формируем имя группы кухни
        if branch_id:
            kitchen_group_name = f"cafe_kitchen_{company_id}_{branch_id}"
        else:
            kitchen_group_name = f"cafe_kitchen_{company_id}"

        # Сериализуем данные задачи кухни
        from .serializers import KitchenTaskSerializer
        serializer = KitchenTaskSerializer(task)
        task_data = serializer.data

        # Конвертируем UUID и Decimal в строки для msgpack сериализации
        task_data = json.loads(json.dumps(task_data, default=str))

        payload = {
            "task": task_data,
            "task_id": str(task.id),
            "order_id": str(task.order_id),
            "table": (task.order.table.number if task.order_id and task.order.table_id else None),
            "menu_item": (task.menu_item.title if task.menu_item_id else None),
            "unit_index": task.unit_index,
            "company_id": company_id,
            "branch_id": branch_id,
        }

        message = {"type": "kitchen_task_ready", "payload": payload}

        # 1) официанты/заказы
        async_to_sync(channel_layer.group_send)(orders_group_name, message)
        logger.info(f"[send_kitchen_task_ready_notification] Message sent to channel layer: group={orders_group_name}")

        # 2) кухня/повара
        async_to_sync(channel_layer.group_send)(kitchen_group_name, message)
        logger.info(f"[send_kitchen_task_ready_notification] Message sent to channel layer: group={kitchen_group_name}")
    except Exception as e:
        logger.error(f"[send_kitchen_task_ready_notification] Error sending notification: {e}", exc_info=True)


def send_table_created_notification(table):
    """
    Отправляет WebSocket уведомление о создании стола.
    """
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        
        company_id = str(table.company_id)
        branch_id = str(table.branch_id) if table.branch_id else None
        
        # Формируем имя группы
        if branch_id:
            group_name = f"cafe_tables_{company_id}_{branch_id}"
        else:
            group_name = f"cafe_tables_{company_id}"
        
        # Сериализуем данные стола
        from .serializers import TableSerializer
        serializer = TableSerializer(table)
        table_data = serializer.data
        
        # Конвертируем UUID и Decimal в строки для msgpack сериализации
        table_data = json.loads(json.dumps(table_data, default=str))
        
        # Отправляем уведомление в группу
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "table_created",
                "payload": {
                    "table": table_data,
                    "company_id": company_id,
                    "branch_id": branch_id,
                }
            }
        )
    except Exception as e:
        logger.error(f"[send_table_created_notification] Error: {e}", exc_info=True)


def send_table_updated_notification(table):
    """
    Отправляет WebSocket уведомление об обновлении стола.
    """
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        
        company_id = str(table.company_id)
        branch_id = str(table.branch_id) if table.branch_id else None
        
        # Формируем имя группы
        if branch_id:
            group_name = f"cafe_tables_{company_id}_{branch_id}"
        else:
            group_name = f"cafe_tables_{company_id}"
        
        # Сериализуем данные стола
        from .serializers import TableSerializer
        serializer = TableSerializer(table)
        table_data = serializer.data
        
        # Конвертируем UUID и Decimal в строки для msgpack сериализации
        table_data = json.loads(json.dumps(table_data, default=str))
        
        # Отправляем уведомление в группу
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "table_updated",
                "payload": {
                    "table": table_data,
                    "company_id": company_id,
                    "branch_id": branch_id,
                }
            }
        )
    except Exception as e:
        logger.error(f"[send_table_updated_notification] Error: {e}", exc_info=True)


def send_table_status_changed_notification(table):
    """
    Отправляет WebSocket уведомление об изменении статуса стола (FREE/BUSY).
    Это специальное уведомление для отслеживания занятости столов в реальном времени.
    """
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        
        company_id = str(table.company_id)
        branch_id = str(table.branch_id) if table.branch_id else None
        
        # Формируем имя группы для столов
        if branch_id:
            group_name = f"cafe_tables_{company_id}_{branch_id}"
        else:
            group_name = f"cafe_tables_{company_id}"
        
        # Также отправляем в группу заказов, так как изменение статуса стола связано с заказами
        if branch_id:
            orders_group_name = f"cafe_orders_{company_id}_{branch_id}"
        else:
            orders_group_name = f"cafe_orders_{company_id}"
        
        # Сериализуем данные стола
        from .serializers import TableSerializer
        serializer = TableSerializer(table)
        table_data = serializer.data
        
        # Конвертируем UUID и Decimal в строки для msgpack сериализации
        table_data = json.loads(json.dumps(table_data, default=str))
        
        # Отправляем уведомление в группу столов
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "table_status_changed",
                "payload": {
                    "table": table_data,
                    "table_id": str(table.id),
                    "table_number": table.number,
                    "status": table.status,
                    "status_display": table.get_status_display(),
                    "company_id": company_id,
                    "branch_id": branch_id,
                }
            }
        )
        
        # Отправляем уведомление в группу заказов (чтобы официанты видели изменения)
        async_to_sync(channel_layer.group_send)(
            orders_group_name,
            {
                "type": "table_status_changed",
                "payload": {
                    "table": table_data,
                    "table_id": str(table.id),
                    "table_number": table.number,
                    "status": table.status,
                    "status_display": table.get_status_display(),
                    "company_id": company_id,
                    "branch_id": branch_id,
                }
            }
        )
    except Exception as e:
        logger.error(f"[send_table_status_changed_notification] Error: {e}", exc_info=True)
