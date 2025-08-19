# cafe/urls.py
from django.urls import path
from .views import (
    ZoneListCreateView, ZoneRetrieveUpdateDestroyView,
    TableListCreateView, TableRetrieveUpdateDestroyView,
    BookingListCreateView, BookingRetrieveUpdateDestroyView,
    WarehouseListCreateView, WarehouseRetrieveUpdateDestroyView,
    PurchaseListCreateView, PurchaseRetrieveUpdateDestroyView,
    StaffListCreateView, StaffRetrieveUpdateDestroyView,
    CategoryListCreateView, CategoryRetrieveUpdateDestroyView,
    MenuItemListCreateView, MenuItemRetrieveUpdateDestroyView,
    IngredientListCreateView, IngredientRetrieveUpdateDestroyView,
    OrderListCreateView, OrderRetrieveUpdateDestroyView,
    OrderItemListCreateView, OrderItemRetrieveUpdateDestroyView,
)

app_name = "cafe"

urlpatterns = [
    # Zones
    path("zones/", ZoneListCreateView.as_view(), name="zone-list"),
    path("zones/<uuid:pk>/", ZoneRetrieveUpdateDestroyView.as_view(), name="zone-detail"),

    # Tables
    path("tables/", TableListCreateView.as_view(), name="table-list"),
    path("tables/<uuid:pk>/", TableRetrieveUpdateDestroyView.as_view(), name="table-detail"),

    # Bookings
    path("bookings/", BookingListCreateView.as_view(), name="booking-list"),
    path("bookings/<uuid:pk>/", BookingRetrieveUpdateDestroyView.as_view(), name="booking-detail"),

    # Warehouse
    path("warehouse/", WarehouseListCreateView.as_view(), name="warehouse-list"),
    path("warehouse/<uuid:pk>/", WarehouseRetrieveUpdateDestroyView.as_view(), name="warehouse-detail"),

    # Purchases
    path("purchases/", PurchaseListCreateView.as_view(), name="purchase-list"),
    path("purchases/<uuid:pk>/", PurchaseRetrieveUpdateDestroyView.as_view(), name="purchase-detail"),

    # Staff
    path("staff/", StaffListCreateView.as_view(), name="staff-list"),
    path("staff/<uuid:pk>/", StaffRetrieveUpdateDestroyView.as_view(), name="staff-detail"),

    # Categories
    path("categories/", CategoryListCreateView.as_view(), name="category-list"),
    path("categories/<uuid:pk>/", CategoryRetrieveUpdateDestroyView.as_view(), name="category-detail"),

    # Menu items
    path("menu-items/", MenuItemListCreateView.as_view(), name="menuitem-list"),
    path("menu-items/<uuid:pk>/", MenuItemRetrieveUpdateDestroyView.as_view(), name="menuitem-detail"),

    # Ingredients
    path("ingredients/", IngredientListCreateView.as_view(), name="ingredient-list"),
    path("ingredients/<uuid:pk>/", IngredientRetrieveUpdateDestroyView.as_view(), name="ingredient-detail"),

    # Orders
    path("orders/", OrderListCreateView.as_view(), name="order-list"),
    path("orders/<uuid:pk>/", OrderRetrieveUpdateDestroyView.as_view(), name="order-detail"),

    # Order items
    path("order-items/", OrderItemListCreateView.as_view(), name="orderitem-list"),
    path("order-items/<uuid:pk>/", OrderItemRetrieveUpdateDestroyView.as_view(), name="orderitem-detail"),
]
