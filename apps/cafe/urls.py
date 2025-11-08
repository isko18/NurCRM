# cafe/urls.py
from django.urls import path

from .views import (
    # Clients + nested client orders
    CafeClientListCreateView, CafeClientRetrieveUpdateDestroyView, ClientOrderListCreateView,
    ClientOrderHistoryListView, OrderHistoryListView,

    # Zones / Tables / Bookings / Warehouse / Purchases / Categories / Menu Items / Ingredients / Orders / Order Items
    ZoneListCreateView, ZoneRetrieveUpdateDestroyView,
    TableListCreateView, TableRetrieveUpdateDestroyView,
    BookingListCreateView, BookingRetrieveUpdateDestroyView,
    WarehouseListCreateView, WarehouseRetrieveUpdateDestroyView,
    PurchaseListCreateView, PurchaseRetrieveUpdateDestroyView,
    CategoryListCreateView, CategoryRetrieveUpdateDestroyView,
    MenuItemListCreateView, MenuItemRetrieveUpdateDestroyView,
    IngredientListCreateView, IngredientRetrieveUpdateDestroyView,
    OrderListCreateView, OrderRetrieveUpdateDestroyView,
    OrderItemListCreateView, OrderItemRetrieveUpdateDestroyView,

    # Kitchen / notifications / analytics
    KitchenTaskListView, KitchenTaskClaimView, KitchenTaskReadyView, KitchenTaskMonitorView,
    KitchenAnalyticsByCookView, KitchenAnalyticsByWaiterView,
    NotificationListView,
)

app_name = "cafe"

urlpatterns = [
    # === Clients ===
    path("clients/", CafeClientListCreateView.as_view(), name="client-list"),
    path("clients/<uuid:pk>/", CafeClientRetrieveUpdateDestroyView.as_view(), name="client-detail"),
    # Вложенные заказы конкретного клиента
    path("clients/<uuid:pk>/orders/", ClientOrderListCreateView.as_view(), name="client-orders"),
    # История заказов конкретного клиента
    path("clients/<uuid:pk>/orders/history/", ClientOrderHistoryListView.as_view(), name="client-order-history"),

    # === Zones ===
    path("zones/", ZoneListCreateView.as_view(), name="zone-list"),
    path("zones/<uuid:pk>/", ZoneRetrieveUpdateDestroyView.as_view(), name="zone-detail"),

    # === Tables ===
    path("tables/", TableListCreateView.as_view(), name="table-list"),
    path("tables/<uuid:pk>/", TableRetrieveUpdateDestroyView.as_view(), name="table-detail"),

    # === Bookings ===
    path("bookings/", BookingListCreateView.as_view(), name="booking-list"),
    path("bookings/<uuid:pk>/", BookingRetrieveUpdateDestroyView.as_view(), name="booking-detail"),

    # === Warehouse ===
    path("warehouse/", WarehouseListCreateView.as_view(), name="warehouse-list"),
    path("warehouse/<uuid:pk>/", WarehouseRetrieveUpdateDestroyView.as_view(), name="warehouse-detail"),

    # === Purchases ===
    path("purchases/", PurchaseListCreateView.as_view(), name="purchase-list"),
    path("purchases/<uuid:pk>/", PurchaseRetrieveUpdateDestroyView.as_view(), name="purchase-detail"),

    # === Categories ===
    path("categories/", CategoryListCreateView.as_view(), name="category-list"),
    path("categories/<uuid:pk>/", CategoryRetrieveUpdateDestroyView.as_view(), name="category-detail"),

    # === Menu items ===
    path("menu-items/", MenuItemListCreateView.as_view(), name="menuitem-list"),
    path("menu-items/<uuid:pk>/", MenuItemRetrieveUpdateDestroyView.as_view(), name="menuitem-detail"),

    # === Ingredients ===
    path("ingredients/", IngredientListCreateView.as_view(), name="ingredient-list"),
    path("ingredients/<uuid:pk>/", IngredientRetrieveUpdateDestroyView.as_view(), name="ingredient-detail"),

    # === Orders ===
    path("orders/", OrderListCreateView.as_view(), name="order-list"),
    path("orders/<uuid:pk>/", OrderRetrieveUpdateDestroyView.as_view(), name="order-detail"),
    # Общая история заказов компании
    path("orders/history/", OrderHistoryListView.as_view(), name="order-history"),

    # === Order items ===
    path("order-items/", OrderItemListCreateView.as_view(), name="orderitem-list"),
    path("order-items/<uuid:pk>/", OrderItemRetrieveUpdateDestroyView.as_view(), name="orderitem-detail"),

    # ==================== Kitchen (повар) ====================
    # Лента задач (pending + in_progress; ?mine=1, ?status=ready и т.п.)
    path("kitchen/tasks/", KitchenTaskListView.as_view(), name="kitchen-task-list"),
    # Взять задачу в работу
    path("kitchen/tasks/<uuid:pk>/claim/", KitchenTaskClaimView.as_view(), name="kitchen-task-claim"),
    # Отметить как готово (уведомляет официанта)
    path("kitchen/tasks/<uuid:pk>/ready/", KitchenTaskReadyView.as_view(), name="kitchen-task-ready"),
    # Мониторинг задач для владельца/админа
    path("kitchen/tasks/monitor/", KitchenTaskMonitorView.as_view(), name="kitchen-task-monitor"),

    # === Analytics ===
    path("kitchen/analytics/cooks/", KitchenAnalyticsByCookView.as_view(), name="kitchen-analytics-cooks"),
    path("kitchen/analytics/waiters/", KitchenAnalyticsByWaiterView.as_view(), name="kitchen-analytics-waiters"),

    # === Notifications (официант) ===
    path("notifications/", NotificationListView.as_view(), name="notifications-list"),
]
