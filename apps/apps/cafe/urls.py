# cafe/urls.py
from django.urls import path, re_path

from .views import (
    # Receipt printer settings
    ReceiptPrinterSettingsView,
    # Clients + nested client orders
    CafeClientListCreateView, CafeClientRetrieveUpdateDestroyView, ClientOrderListCreateView,
    ClientOrderHistoryListView, OrderHistoryListView, OrderHistoryRetrieveUpdateView,

    # Zones / Tables / Bookings / Warehouse / Purchases / Categories / Menu Items / Ingredients / Orders / Order Items
    ZoneListCreateView, ZoneRetrieveUpdateDestroyView,
    TableListCreateView, TableRetrieveUpdateDestroyView,
    BookingListCreateView, BookingRetrieveUpdateDestroyView,
    WarehouseListCreateView, WarehouseRetrieveUpdateDestroyView, WarehouseStockAdjustView,
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
    EquipmentListCreateView, EquipmentRetrieveUpdateDestroyView, EquipmentReceiveView,
    EquipmentInventorySessionListCreateView, EquipmentInventorySessionRetrieveView, EquipmentInventorySessionConfirmView,

    KitchenListCreateView, KitchenRetrieveUpdateDestroyView, OrderClosedListView, OrderPayView, OrderPayDebtView,
    OrderRefundView, OrderItemRefundView,
    CafeExpenseListCreateView, CafeExpenseRetrieveUpdateDestroyView,
    CafeWaiterPayProfileListCreateView, CafeWaiterPayProfileRetrieveUpdateDestroyView,

    PreparationListCreateView, PreparationRetrieveUpdateDestroyView, PreparationReceiveView,
    PreparationTechCardView, PreparationIngredientListCreateView, PreparationIngredientRetrieveUpdateDestroyView,
    ProcessingTypeListCreateView, ProcessingTypeRetrieveUpdateDestroyView,
    DishIngredientCreateForDishView, DishIngredientRetrieveUpdateDestroyView,
    DishIngredientProcessingCreateView, DishIngredientProcessingDeleteView,
    DishCostView, DishCalculatePreviewView, TechCardsExportView,
)

from apps.cafe.analytics import (
    KitchenAnalyticsByCookView, KitchenAnalyticsByWaiterView,
    SalesSummaryView, SalesDynamicsView, SalesByMenuItemView, SalesByCategoryView,
    SalesByKitchenView, RevenueInflowView, RejectionsAnalyticsView,
    CancelledOrdersAnalyticsView,
    CafeExpensesSummaryView, CafeFinanceAnalyticsView, CafeDebtAnalyticsView,
    CafeShiftReportView, CafeDailyCloseReportView, CafeWaiterSalaryReportView,
    CafeUnifiedAnalyticsView, CafeWaiterSalesView,
    PurchasesSummaryView, PurchasesBySupplierView,
    WarehouseLowStockView, CafeAnalyticsExportView,
    MenuAnalyticsAllView,
)

from apps.cafe.showcase.views_public import PublicCafeInfoAPIView, PublicCafeMenuAPIView, PublicCafeMenuItemsAPIView

from apps.cafe.offline_views import CafeOfflineSnapshotView, CafeOfflineSyncView

from apps.cafe.fiscal_views import (
    CafeFiscalSettingsView,
    CafeFiscalShiftStateView,
    CafeFiscalShiftListView,
    CafeFiscalShiftOpenView,
    CafeFiscalShiftCloseView,
    CafeFiscalCashDepositView,
    CafeFiscalCashWithdrawView,
    CafeFiscalOrderReceiptPayloadView,
    CafeFiscalOrderReceiptRecordView,
    CafeFiscalReceiptListView,
)

from apps.cafe.household_views import (
    CafeExpenseCategoryListCreateView,
    CafeExpenseCategoryRetrieveUpdateDestroyView,
    WarehouseReceiveView,
    CafeHouseholdItemListCreateView,
    CafeHouseholdItemRetrieveUpdateDestroyView,
    CafeHouseholdMovementListView,
    CafeHouseholdReceiveView,
    CafeHouseholdWriteOffView,
    CafeHouseholdInventorySessionListCreateView,
    CafeHouseholdInventorySessionRetrieveUpdateView,
    CafeHouseholdInventorySessionConfirmView,
)

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
    path("warehouse/<uuid:pk>/adjust/", WarehouseStockAdjustView.as_view(), name="warehouse-stock-adjust"),
    path("warehouse/<uuid:pk>/receive/", WarehouseReceiveView.as_view(), name="warehouse-receive"),

    path("expense-categories/", CafeExpenseCategoryListCreateView.as_view(), name="cafe-expense-category-list"),
    path(
        "expense-categories/<uuid:pk>/",
        CafeExpenseCategoryRetrieveUpdateDestroyView.as_view(),
        name="cafe-expense-category-detail",
    ),

    path("household-items/", CafeHouseholdItemListCreateView.as_view(), name="cafe-household-item-list"),
    path(
        "household-items/<uuid:pk>/",
        CafeHouseholdItemRetrieveUpdateDestroyView.as_view(),
        name="cafe-household-item-detail",
    ),
    path(
        "household-items/<uuid:pk>/movements/",
        CafeHouseholdMovementListView.as_view(),
        name="cafe-household-item-movements",
    ),
    path(
        "household-items/<uuid:pk>/receive/",
        CafeHouseholdReceiveView.as_view(),
        name="cafe-household-item-receive",
    ),
    path(
        "household-items/<uuid:pk>/write-off/",
        CafeHouseholdWriteOffView.as_view(),
        name="cafe-household-item-write-off",
    ),

    path(
        "household-inventory/sessions/",
        CafeHouseholdInventorySessionListCreateView.as_view(),
        name="cafe-household-inventory-session-list",
    ),
    path(
        "household-inventory/sessions/<uuid:pk>/",
        CafeHouseholdInventorySessionRetrieveUpdateView.as_view(),
        name="cafe-household-inventory-session-detail",
    ),
    path(
        "household-inventory/sessions/<uuid:pk>/confirm/",
        CafeHouseholdInventorySessionConfirmView.as_view(),
        name="cafe-household-inventory-session-confirm",
    ),

    # === Purchases ===
    path("purchases/", PurchaseListCreateView.as_view(), name="purchase-list"),
    path("purchases/<uuid:pk>/", PurchaseRetrieveUpdateDestroyView.as_view(), name="purchase-detail"),

    path("expenses/", CafeExpenseListCreateView.as_view(), name="cafe-expense-list"),
    path("expenses/<uuid:pk>/", CafeExpenseRetrieveUpdateDestroyView.as_view(), name="cafe-expense-detail"),

    path("waiter-pay-profiles/", CafeWaiterPayProfileListCreateView.as_view(), name="cafe-waiter-pay-list"),
    path(
        "waiter-pay-profiles/<uuid:pk>/",
        CafeWaiterPayProfileRetrieveUpdateDestroyView.as_view(),
        name="cafe-waiter-pay-detail",
    ),

    # === Categories ===
    path("categories/", CategoryListCreateView.as_view(), name="category-list"),
    path("categories/<uuid:pk>/", CategoryRetrieveUpdateDestroyView.as_view(), name="category-detail"),

    # === Menu items ===
    path("menu-items/", MenuItemListCreateView.as_view(), name="menuitem-list"),
    path("menu-items/<uuid:pk>/", MenuItemRetrieveUpdateDestroyView.as_view(), name="menuitem-detail"),

    # === Ingredients ===
    path("ingredients/", IngredientListCreateView.as_view(), name="ingredient-list"),
    path("ingredients/<uuid:pk>/", IngredientRetrieveUpdateDestroyView.as_view(), name="ingredient-detail"),

    # === Costing (new) ===
    path("preparations/", PreparationListCreateView.as_view(), name="preparation-list"),
    path("preparations/<uuid:pk>/", PreparationRetrieveUpdateDestroyView.as_view(), name="preparation-detail"),
    path("preparations/<uuid:pk>/tech-card/", PreparationTechCardView.as_view(), name="preparation-tech-card"),
    path("preparations/<uuid:pk>/receive/", PreparationReceiveView.as_view(), name="preparation-receive"),
    path(
        "preparations/<uuid:preparation_id>/ingredients/",
        PreparationIngredientListCreateView.as_view(),
        name="preparation-ingredient-list",
    ),
    path(
        "preparation-ingredients/<uuid:pk>/",
        PreparationIngredientRetrieveUpdateDestroyView.as_view(),
        name="preparation-ingredient-detail",
    ),

    path("processing-types/", ProcessingTypeListCreateView.as_view(), name="processing-type-list"),
    path("processing-types/<uuid:pk>/", ProcessingTypeRetrieveUpdateDestroyView.as_view(), name="processing-type-detail"),

    path("dishes/<uuid:pk>/ingredients/", DishIngredientCreateForDishView.as_view(), name="dish-ingredient-create"),
    path("dish-ingredients/<uuid:pk>/", DishIngredientRetrieveUpdateDestroyView.as_view(), name="dish-ingredient-detail"),
    path("dish-ingredient-processings/", DishIngredientProcessingCreateView.as_view(), name="dish-ingredient-processing-create"),
    path("dish-ingredient-processings/<uuid:pk>/", DishIngredientProcessingDeleteView.as_view(), name="dish-ingredient-processing-delete"),

    path("dishes/<uuid:pk>/cost/", DishCostView.as_view(), name="dish-cost"),
    path("dishes/calculate-preview/", DishCalculatePreviewView.as_view(), name="dish-calc-preview"),
    path("tech-cards/export/", TechCardsExportView.as_view(), name="tech-cards-export"),

    # === Офлайн-режим (снапшот + синхронизация очереди действий) ===
    path("offline-snapshot/", CafeOfflineSnapshotView.as_view(), name="cafe-offline-snapshot"),
    path("offline-sync/", CafeOfflineSyncView.as_view(), name="cafe-offline-sync"),

    # === Orders ===
    path("orders/", OrderListCreateView.as_view(), name="order-list"),
    path("orders/<uuid:pk>/", OrderRetrieveUpdateDestroyView.as_view(), name="order-detail"),

    path("orders/<uuid:pk>/pay/", OrderPayView.as_view(), name="cafe_order_pay"),
    path("orders/<uuid:pk>/pay-debt/", OrderPayDebtView.as_view(), name="cafe_order_pay_debt"),
    path("orders/<uuid:pk>/refund/", OrderRefundView.as_view(), name="cafe_order_refund"),
    path("orders/<uuid:pk>/refund-item/", OrderItemRefundView.as_view(), name="cafe_order_refund_item"),
    path("orders/closed/", OrderClosedListView.as_view(), name="cafe_orders_closed"),
    # Общая история заказов компании
    path("orders/history/", OrderHistoryListView.as_view(), name="order-history"),
    path("orders/history/<uuid:pk>/", OrderHistoryRetrieveUpdateView.as_view(), name="order-history-detail"),

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
    path("equipment/<uuid:pk>/receive/", EquipmentReceiveView.as_view(), name="equipment-receive"),
    path("equipment/inventory/sessions/", EquipmentInventorySessionListCreateView.as_view(), name="equipment-inventory-session-list"),
    path("equipment/inventory/sessions/<uuid:pk>/", EquipmentInventorySessionRetrieveView.as_view(), name="equipment-inventory-session-detail"),
    path("equipment/inventory/sessions/<uuid:pk>/confirm/", EquipmentInventorySessionConfirmView.as_view(), name="equipment-inventory-session-confirm"),

    path("kitchens/", KitchenListCreateView.as_view(), name="cafe-kitchen-list"),
    path("kitchens/<uuid:pk>/", KitchenRetrieveUpdateDestroyView.as_view(), name="cafe-kitchen-detail"),

    path("kitchen/analytics/cooks/", KitchenAnalyticsByCookView.as_view()),
    path("kitchen/analytics/waiters/", KitchenAnalyticsByWaiterView.as_view()),

    path("analytics/sales/summary/", SalesSummaryView.as_view()),
    path("analytics/sales/dynamics/", SalesDynamicsView.as_view(), name="cafe-analytics-sales-dynamics"),
    path("analytics/sales/items/", SalesByMenuItemView.as_view()),
    path("analytics/menu/all/", MenuAnalyticsAllView.as_view(), name="cafe-analytics-menu-all"),
    path("analytics/sales/categories/", SalesByCategoryView.as_view(), name="cafe-analytics-sales-categories"),
    path("analytics/sales/kitchens/", SalesByKitchenView.as_view(), name="cafe-analytics-sales-kitchens"),
    path("analytics/revenue-inflow/", RevenueInflowView.as_view(), name="cafe-analytics-revenue-inflow"),
    path("analytics/rejections/", RejectionsAnalyticsView.as_view(), name="cafe-analytics-rejections"),
    path("analytics/orders/cancelled/", CancelledOrdersAnalyticsView.as_view(), name="cafe-analytics-orders-cancelled"),
    path("analytics/expenses/summary/", CafeExpensesSummaryView.as_view(), name="cafe-analytics-expenses-summary"),
    path("analytics/finance/", CafeFinanceAnalyticsView.as_view(), name="cafe-analytics-finance"),
    path("analytics/debts/", CafeDebtAnalyticsView.as_view(), name="cafe-analytics-debts"),
    path("analytics/shift-report/", CafeShiftReportView.as_view(), name="cafe-analytics-shift-report"),
    path("analytics/daily-close/", CafeDailyCloseReportView.as_view(), name="cafe-analytics-daily-close"),
    path("analytics/waiter-sales/", CafeWaiterSalesView.as_view(), name="cafe-analytics-waiter-sales"),
    path("analytics/waiter-salary/", CafeWaiterSalaryReportView.as_view(), name="cafe-analytics-waiter-salary"),
    path("analytics/unified/", CafeUnifiedAnalyticsView.as_view(), name="cafe-analytics-unified"),

    path("analytics/purchases/summary/", PurchasesSummaryView.as_view()),
    path("analytics/purchases/suppliers/", PurchasesBySupplierView.as_view()),

    path("analytics/warehouse/low-stock/", WarehouseLowStockView.as_view()),
    re_path(r"^analytics/export/?$", CafeAnalyticsExportView.as_view(), name="cafe-analytics-export"),

    # === Фискальная интеграция (налоговая ГНС КР) ===
    path("fiscal/settings/", CafeFiscalSettingsView.as_view(), name="cafe-fiscal-settings"),
    path("fiscal/shift/state/", CafeFiscalShiftStateView.as_view(), name="cafe-fiscal-shift-state"),
    path("fiscal/shift/open/", CafeFiscalShiftOpenView.as_view(), name="cafe-fiscal-shift-open"),
    path("fiscal/shift/close/", CafeFiscalShiftCloseView.as_view(), name="cafe-fiscal-shift-close"),
    path("fiscal/shifts/", CafeFiscalShiftListView.as_view(), name="cafe-fiscal-shifts"),
    path("fiscal/cash/deposit/", CafeFiscalCashDepositView.as_view(), name="cafe-fiscal-cash-deposit"),
    path("fiscal/cash/withdraw/", CafeFiscalCashWithdrawView.as_view(), name="cafe-fiscal-cash-withdraw"),
    path(
        "fiscal/orders/<uuid:pk>/receipt-payload/",
        CafeFiscalOrderReceiptPayloadView.as_view(),
        name="cafe-fiscal-order-receipt-payload",
    ),
    path(
        "fiscal/orders/<uuid:pk>/receipt/",
        CafeFiscalOrderReceiptRecordView.as_view(),
        name="cafe-fiscal-order-receipt",
    ),
    path("fiscal/receipts/", CafeFiscalReceiptListView.as_view(), name="cafe-fiscal-receipts"),

    path("public/cafe/<slug:company_slug>/", PublicCafeInfoAPIView.as_view(), name="public_cafe_info"),
    path("public/cafe/<slug:company_slug>/menu/", PublicCafeMenuAPIView.as_view(), name="public_cafe_menu"),
    path("public/cafe/<slug:company_slug>/menu-items/", PublicCafeMenuItemsAPIView.as_view(), name="public_cafe_menu_items"),
]
