# views.py
from rest_framework import generics, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Zone, Table, Booking, Warehouse, Purchase, Staff,
    Category, MenuItem, Ingredient,
    Order, OrderItem,
)
from .serializers import (
    ZoneSerializer, TableSerializer, BookingSerializer,
    WarehouseSerializer, PurchaseSerializer, StaffSerializer,
    CategorySerializer, MenuItemSerializer, IngredientInlineSerializer,
    OrderSerializer, OrderItemInlineSerializer,
)


# --------- Общий миксин для фильтрации по компании ---------
class CompanyQuerysetMixin:
    permission_classes = [permissions.IsAuthenticated]

    def get_company(self):
        user = getattr(self.request, "user", None)
        return getattr(user, "company", None)

    def get_queryset(self):
        qs = super().get_queryset()
        company = self.get_company()
        if company is None:
            return qs.none()
        model = qs.model
        # проверяем, есть ли у модели поле company
        if any(f.name == "company" for f in model._meta.fields):
            return qs.filter(company=company)
        return qs


# ==================== Zone ====================
class ZoneListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["title"]
    search_fields = ["title"]
    ordering_fields = ["title", "id"]


class ZoneRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer


# ==================== Table ====================
class TableListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Table.objects.select_related("zone", "company").all()
    serializer_class = TableSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["zone", "number", "places", "status"]
    search_fields = ["zone__title"]
    ordering_fields = ["number", "places", "status", "id"]


class TableRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Table.objects.select_related("zone", "company").all()
    serializer_class = TableSerializer


# ==================== Booking ====================
class BookingListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Booking.objects.select_related("table", "company").all()
    serializer_class = BookingSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["date", "time", "table", "status", "guests"]
    search_fields = ["guest", "phone"]
    ordering_fields = ["date", "time", "guests", "id"]


class BookingRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Booking.objects.select_related("table", "company").all()
    serializer_class = BookingSerializer


# ==================== Warehouse ====================
class WarehouseListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["title", "unit"]
    search_fields = ["title", "unit"]
    ordering_fields = ["title", "id"]


class WarehouseRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer


# ==================== Purchase ====================
class PurchaseListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Purchase.objects.all()
    serializer_class = PurchaseSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["supplier"]
    search_fields = ["supplier"]
    ordering_fields = ["price", "id"]


class PurchaseRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Purchase.objects.all()
    serializer_class = PurchaseSerializer


# ==================== Staff ====================
class StaffListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Staff.objects.all()
    serializer_class = StaffSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["role", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "role", "is_active", "id"]


class StaffRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Staff.objects.all()
    serializer_class = StaffSerializer


# ==================== Category ====================
class CategoryListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["title"]
    search_fields = ["title"]
    ordering_fields = ["title", "id"]


class CategoryRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


# ==================== MenuItem (+ ingredients nested) ====================
class MenuItemListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = MenuItem.objects.select_related("category", "company").prefetch_related("ingredients__product").all()
    serializer_class = MenuItemSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["category", "is_active", "title"]
    search_fields = ["title", "category__title"]
    ordering_fields = ["title", "price", "is_active", "id"]


class MenuItemRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = MenuItem.objects.select_related("category", "company").prefetch_related("ingredients__product").all()
    serializer_class = MenuItemSerializer


# ==================== Ingredient (отдельный CRUD при необходимости) ====================
class IngredientListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = IngredientInlineSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["menu_item", "product"]
    ordering_fields = ["id"]

    def get_queryset(self):
        company = self.get_company()
        qs = Ingredient.objects.select_related("menu_item", "product")
        if company is None:
            return qs.none()
        return qs.filter(menu_item__company=company)


class IngredientRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IngredientInlineSerializer

    def get_queryset(self):
        company = self.get_company()
        qs = Ingredient.objects.select_related("menu_item", "product")
        if company is None:
            return qs.none()
        return qs.filter(menu_item__company=company)


# ==================== Order (с вложенными items) ====================
class OrderListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    # ВНИМАНИЕ: related_name у OrderItem — 'cafe_items'
    queryset = Order.objects.select_related("table", "waiter", "company") \
                            .prefetch_related("cafe_items__menu_item") \
                            .all()
    serializer_class = OrderSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["table", "waiter", "guests", "created_at"]
    ordering_fields = ["created_at", "guests", "id"]


class OrderRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Order.objects.select_related("table", "waiter", "company") \
                            .prefetch_related("cafe_items__menu_item") \
                            .all()
    serializer_class = OrderSerializer


# ==================== OrderItem (отдельный CRUD при необходимости) ====================
class OrderItemListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = OrderItemInlineSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["order", "menu_item"]
    ordering_fields = ["id"]

    def get_queryset(self):
        company = self.get_company()
        qs = OrderItem.objects.select_related("order", "menu_item")
        if company is None:
            return qs.none()
        return qs.filter(order__company=company)


class OrderItemRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderItemInlineSerializer

    def get_queryset(self):
        company = self.get_company()
        qs = OrderItem.objects.select_related("order", "menu_item")
        if company is None:
            return qs.none()
        return qs.filter(order__company=company)
