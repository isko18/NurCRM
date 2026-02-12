# cafe/urls.py
from django.urls import path

from .views import (
    # Receipt printer settings
    ReceiptPrinterSettingsView,
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
    KitchenTaskListView, KitchenTaskClaimView, KitchenTaskClaimBulkView, KitchenTaskReadyView, KitchenTaskReadyBulkView, KitchenTaskRetrieveUpdateDestroyView, KitchenTaskMonitorView,
    KitchenAnalyticsByCookView, KitchenAnalyticsByWaiterView,
    NotificationListView, InventorySessionListCreateView, InventorySessionRetrieveView, InventorySessionConfirmView,
    EquipmentListCreateView, EquipmentRetrieveUpdateDestroyView,
    EquipmentInventorySessionListCreateView, EquipmentInventorySessionRetrieveView, EquipmentInventorySessionConfirmView,

    KitchenListCreateView, KitchenRetrieveUpdateDestroyView, OrderClosedListView, OrderPayView
)

from apps.cafe.analytics import (
    KitchenAnalyticsByCookView, KitchenAnalyticsByWaiterView,
    SalesSummaryView, SalesByMenuItemView,
    PurchasesSummaryView, PurchasesBySupplierView,
    WarehouseLowStockView,
)

from apps.cafe.showcase.views_public import PublicCafeInfoAPIView, PublicCafeMenuAPIView, PublicCafeMenuItemsAPIView

app_name = "cafe"

urlpatterns = [
    # === Настройки принтера кассы (чековый принтер) ===
    path("receipt-printer/", ReceiptPrinterSettingsView.as_view(), name="receipt-printer-settings"),

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

    path("orders/<uuid:pk>/pay/", OrderPayView.as_view(), name="cafe_order_pay"),
    path("orders/closed/", OrderClosedListView.as_view(), name="cafe_orders_closed"),
    # Общая история заказов компании
    path("orders/history/", OrderHistoryListView.as_view(), name="order-history"),

    # === Order items ===
    path("order-items/", OrderItemListCreateView.as_view(), name="orderitem-list"),
    path("order-items/<uuid:pk>/", OrderItemRetrieveUpdateDestroyView.as_view(), name="orderitem-detail"),

    # ==================== Kitchen (повар) ====================
    # Лента задач (pending + in_progress; ?mine=1, ?status=ready или ?status=pending,in_progress)
    path("kitchen/tasks/", KitchenTaskListView.as_view(), name="kitchen-task-list"),
    # Bulk: взять несколько задач в работу (POST body: {"task_ids": ["uuid", ...]})
    path("kitchen/tasks/claim/", KitchenTaskClaimBulkView.as_view(), name="kitchen-task-claim-bulk"),
    # Bulk: отметить несколько задач как готовые (POST body: {"task_ids": ["uuid", ...]})
    path("kitchen/tasks/ready/", KitchenTaskReadyBulkView.as_view(), name="kitchen-task-ready-bulk"),
    # Получить/обновить/удалить задачу (PATCH для изменения статуса и других полей)
    path("kitchen/tasks/<uuid:pk>/", KitchenTaskRetrieveUpdateDestroyView.as_view(), name="kitchen-task-detail"),
    # Взять одну задачу в работу
    path("kitchen/tasks/<uuid:pk>/claim/", KitchenTaskClaimView.as_view(), name="kitchen-task-claim"),
    # Отметить одну задачу как готово (уведомляет официанта)
    path("kitchen/tasks/<uuid:pk>/ready/", KitchenTaskReadyView.as_view(), name="kitchen-task-ready"),
    # Мониторинг задач для владельца/админа
    path("kitchen/tasks/monitor/", KitchenTaskMonitorView.as_view(), name="kitchen-task-monitor"),

    # # === Analytics ===
    # path("kitchen/analytics/cooks/", KitchenAnalyticsByCookView.as_view(), name="kitchen-analytics-cooks"),
    # path("kitchen/analytics/waiters/", KitchenAnalyticsByWaiterView.as_view(), name="kitchen-analytics-waiters"),

    # === Notifications (официант) ===
    path("notifications/", NotificationListView.as_view(), name="notifications-list"),
    
    
    path("inventory/sessions/", InventorySessionListCreateView.as_view(), name="inventory-session-list"),
    path("inventory/sessions/<uuid:pk>/", InventorySessionRetrieveView.as_view(), name="inventory-session-detail"),
    path("inventory/sessions/<uuid:pk>/confirm/", InventorySessionConfirmView.as_view(), name="inventory-session-confirm"),

    # ==================== INVENTORY: оборудование ====================
    path("equipment/", EquipmentListCreateView.as_view(), name="equipment-list"),
    path("equipment/<uuid:pk>/", EquipmentRetrieveUpdateDestroyView.as_view(), name="equipment-detail"),
    path("equipment/inventory/sessions/", EquipmentInventorySessionListCreateView.as_view(), name="equipment-inventory-session-list"),
    path("equipment/inventory/sessions/<uuid:pk>/", EquipmentInventorySessionRetrieveView.as_view(), name="equipment-inventory-session-detail"),
    path("equipment/inventory/sessions/<uuid:pk>/confirm/", EquipmentInventorySessionConfirmView.as_view(), name="equipment-inventory-session-confirm"),

    path("kitchens/", KitchenListCreateView.as_view(), name="cafe-kitchen-list"),
    path("kitchens/<uuid:pk>/", KitchenRetrieveUpdateDestroyView.as_view(), name="cafe-kitchen-detail"),

    path("kitchen/analytics/cooks/", KitchenAnalyticsByCookView.as_view()),
    path("kitchen/analytics/waiters/", KitchenAnalyticsByWaiterView.as_view()),

    path("analytics/sales/summary/", SalesSummaryView.as_view()),
    path("analytics/sales/items/", SalesByMenuItemView.as_view()),

    path("analytics/purchases/summary/", PurchasesSummaryView.as_view()),
    path("analytics/purchases/suppliers/", PurchasesBySupplierView.as_view()),

    path("analytics/warehouse/low-stock/", WarehouseLowStockView.as_view()),

    path("public/cafe/<slug:company_slug>/", PublicCafeInfoAPIView.as_view(), name="public_cafe_info"),
    path("public/cafe/<slug:company_slug>/menu/", PublicCafeMenuAPIView.as_view(), name="public_cafe_menu"),
    path("public/cafe/<slug:company_slug>/menu-items/", PublicCafeMenuItemsAPIView.as_view(), name="public_cafe_menu_items"),
]
