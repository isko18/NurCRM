# apps/cafe/views.py
from rest_framework import generics, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q

from .models import (
    Zone, Table, Booking, Warehouse, Purchase,
    Category, MenuItem, Ingredient,
    Order, OrderItem, CafeClient,
    OrderHistory,
)
from .serializers import (
    ZoneSerializer, TableSerializer, BookingSerializer,
    WarehouseSerializer, PurchaseSerializer,
    CategorySerializer, MenuItemSerializer, IngredientInlineSerializer,
    OrderSerializer, OrderItemInlineSerializer,
    CafeClientSerializer,
    OrderHistorySerializer,
)


# --------- company + branch (как в барбере/букинге) ---------
class CompanyBranchQuerysetMixin:
    permission_classes = [permissions.IsAuthenticated]

    # --- helpers ---
    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        u = self._user()
        if not u or not getattr(u, "is_authenticated", False):
            return None
        return getattr(u, "company", None) or getattr(u, "owned_company", None)

    def _model_has_field(self, qs, field_name: str) -> bool:
        return any(getattr(f, "name", None) == field_name for f in qs.model._meta.get_fields())

    def _active_branch(self):
        """
        Определяем активный филиал:
          1) user.primary_branch() / user.primary_branch
          2) request.branch (если уже проставлен middleware-ом)
          3) None
        Возвращаем только если филиал принадлежит компании пользователя.
        """
        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            return None

        user = self._user()
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and val.company_id == company.id:
                    setattr(request, "branch", val)
                    return val
            except Exception:
                pass
        if primary and getattr(primary, "company_id", None) == company.id:
            setattr(request, "branch", primary)
            return primary

        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == company.id:
                return b

        setattr(request, "branch", None)
        return None

    # --- queryset / save hooks ---
    def get_queryset(self):
        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return qs.none()

        # фильтруем по компании
        if self._model_has_field(qs, "company"):
            qs = qs.filter(company=company)

        # если у модели есть поле branch — применяем логику видимости
        if self._model_has_field(qs, "branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
            else:
                qs = qs.filter(branch__isnull=True)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        # если у модели есть branch — проставим, иначе только company
        try:
            model_fields = set(f.name for f in serializer.Meta.model._meta.get_fields())
        except Exception:
            model_fields = set()

        if "branch" in model_fields:
            active_branch = self._active_branch()
            serializer.save(company=company, branch=active_branch if active_branch is not None else None)
        else:
            serializer.save(company=company)

    def perform_update(self, serializer):
        company = self._user_company()
        try:
            model_fields = set(f.name for f in serializer.Meta.model._meta.get_fields())
        except Exception:
            model_fields = set()

        if "branch" in model_fields:
            active_branch = self._active_branch()
            # не перетираем branch, если филиал неопределён
            if active_branch is not None:
                serializer.save(company=company, branch=active_branch)
            else:
                serializer.save(company=company)
        else:
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


class ClientOrderListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    /cafe/clients/<uuid:pk>/orders/
    GET  — список заказов клиента
    POST — создать заказ этому клиенту (company и client проставляются автоматически)
    """
    queryset = (Order.objects
                .select_related("table", "waiter", "company", "client")
                .prefetch_related("items__menu_item"))
    serializer_class = OrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["table", "waiter", "guests", "created_at"]
    ordering_fields = ["created_at", "guests", "id"]

    def _get_client(self):
        company = self._user_company()
        return generics.get_object_or_404(CafeClient, pk=self.kwargs["pk"], company=company)

    def get_queryset(self):
        # базовый get_queryset уже отфильтровал company + branch/global
        return super().get_queryset().filter(client=self._get_client())

    def perform_create(self, serializer):
        client = self._get_client()
        # company/branch поставит миксин; закрепим клиента и компанию явно
        super().perform_create(serializer)
        serializer.instance.client = client
        serializer.instance.company = client.company
        serializer.instance.save(update_fields=["client", "company"])


# -------- История заказов клиента (вложенно) --------
class ClientOrderHistoryListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    /cafe/clients/<uuid:pk>/orders/history/
    История (архив) заказов конкретного клиента в рамках компании пользователя.
    """
    queryset = (OrderHistory.objects
                .select_related("client", "table", "waiter", "company"))
    serializer_class = OrderHistorySerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["table", "waiter", "created_at", "archived_at", "guests"]
    ordering_fields = ["created_at", "archived_at", "id"]

    def get_queryset(self):
        qs = super().get_queryset()
        company = self._user_company()
        client = generics.get_object_or_404(CafeClient, pk=self.kwargs["pk"], company=company)
        # учтём филиал: у OrderHistory есть поле branch
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
        else:
            qs = qs.filter(branch__isnull=True)
        return qs.filter(client=client).order_by("-created_at")


# -------- Общая история заказов по компании --------
class OrderHistoryListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    /cafe/orders/history/
    История (архив) всех заказов компании.
    """
    queryset = (OrderHistory.objects
                .select_related("client", "table", "waiter", "company"))
    serializer_class = OrderHistorySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["client", "table", "waiter", "created_at", "archived_at", "guests"]
    search_fields = ["client__name", "client__phone"]
    ordering_fields = ["created_at", "archived_at", "id"]

    def get_queryset(self):
        qs = super().get_queryset()
        # миксин уже применил company; добавим видимость по branch истории
        active_branch = self._active_branch()
        if active_branch is not None:
            return qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
        return qs.filter(branch__isnull=True)


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


class TableRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Table.objects.select_related("zone", "company").all()
    serializer_class = TableSerializer


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


class WarehouseRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer


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
    queryset = (MenuItem.objects
                .select_related("category", "company")
                .prefetch_related("ingredients__product")
                .all())
    serializer_class = MenuItemSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["category", "is_active", "title"]
    search_fields = ["title", "category__title"]
    ordering_fields = ["title", "price", "is_active", "id"]


class MenuItemRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = (MenuItem.objects
                .select_related("category", "company")
                .prefetch_related("ingredients__product")
                .all())
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
        # учтём филиал по обеим связям (глобальные/этого филиала)
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
    queryset = (Order.objects
                .select_related("table", "waiter", "company", "client")
                .prefetch_related("items__menu_item"))
    serializer_class = OrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["table", "waiter", "client", "guests", "created_at"]
    ordering_fields = ["created_at", "guests", "id"]


class OrderRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = (Order.objects
                .select_related("table", "waiter", "company", "client")
                .prefetch_related("items__menu_item"))
    serializer_class = OrderSerializer


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
