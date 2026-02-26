from django.urls import path

from .views import (
    ResidentialComplexListCreateView,
    ResidentialComplexDetailView,
    ResidentialComplexDrawingListCreateView,
    ResidentialComplexDrawingDetailView,
    ResidentialComplexWarehouseListCreateView,
    ResidentialComplexWarehouseDetailView,
    BuildingProductListCreateView,
    BuildingProductDetailView,
    BuildingProcurementListCreateView,
    BuildingProcurementDetailView,
    BuildingProcurementItemListCreateView,
    BuildingProcurementItemDetailView,
    BuildingProcurementSubmitToCashView,
    BuildingCashPendingProcurementListView,
    BuildingCashApproveProcurementView,
    BuildingCashRejectProcurementView,
    BuildingTransferCreateView,
    BuildingTransferListView,
    BuildingTransferDetailView,
    BuildingTransferAcceptView,
    BuildingTransferRejectView,
    BuildingWorkflowEventListView,
    BuildingWarehouseStockItemListView,
    BuildingWarehouseStockMoveListView,
    BuildingPurchaseDocumentListCreateView,
    BuildingPurchaseDocumentDetailView,
    BuildingPurchaseDocumentCashApproveView,
    BuildingPurchaseDocumentCashRejectView,
)

app_name = "building"

urlpatterns = [
    path("objects/", ResidentialComplexListCreateView.as_view(), name="residential-complex-list-create"),
    path("objects/<uuid:pk>/", ResidentialComplexDetailView.as_view(), name="residential-complex-detail"),
    path("drawings/", ResidentialComplexDrawingListCreateView.as_view(), name="residential-complex-drawing-list-create"),
    path("drawings/<uuid:pk>/", ResidentialComplexDrawingDetailView.as_view(), name="residential-complex-drawing-detail"),
    path("warehouses/", ResidentialComplexWarehouseListCreateView.as_view(), name="residential-complex-warehouse-list-create"),
    path("warehouses/<uuid:pk>/", ResidentialComplexWarehouseDetailView.as_view(), name="residential-complex-warehouse-detail"),
    path("products/", BuildingProductListCreateView.as_view(), name="building-product-list-create"),
    path("products/<uuid:pk>/", BuildingProductDetailView.as_view(), name="building-product-detail"),

    path("procurements/", BuildingProcurementListCreateView.as_view(), name="building-procurement-list-create"),
    path("procurements/<uuid:pk>/", BuildingProcurementDetailView.as_view(), name="building-procurement-detail"),
    path("procurement-items/", BuildingProcurementItemListCreateView.as_view(), name="building-procurement-item-list-create"),
    path("procurement-items/<uuid:pk>/", BuildingProcurementItemDetailView.as_view(), name="building-procurement-item-detail"),
    path("procurements/<uuid:pk>/submit-to-cash/", BuildingProcurementSubmitToCashView.as_view(), name="building-procurement-submit-to-cash"),

    path("cash/procurements/pending/", BuildingCashPendingProcurementListView.as_view(), name="building-cash-procurements-pending"),
    path("cash/procurements/<uuid:pk>/approve/", BuildingCashApproveProcurementView.as_view(), name="building-cash-procurement-approve"),
    path("cash/procurements/<uuid:pk>/reject/", BuildingCashRejectProcurementView.as_view(), name="building-cash-procurement-reject"),

    path("procurements/<uuid:pk>/transfers/create/", BuildingTransferCreateView.as_view(), name="building-transfer-create"),
    path("warehouse-transfers/", BuildingTransferListView.as_view(), name="building-transfer-list"),
    path("warehouse-transfers/<uuid:pk>/", BuildingTransferDetailView.as_view(), name="building-transfer-detail"),
    path("warehouse-transfers/<uuid:pk>/accept/", BuildingTransferAcceptView.as_view(), name="building-transfer-accept"),
    path("warehouse-transfers/<uuid:pk>/reject/", BuildingTransferRejectView.as_view(), name="building-transfer-reject"),

    path("workflow-events/", BuildingWorkflowEventListView.as_view(), name="building-workflow-event-list"),
    path("warehouse-stock/items/", BuildingWarehouseStockItemListView.as_view(), name="building-warehouse-stock-item-list"),
    path("warehouse-stock/moves/", BuildingWarehouseStockMoveListView.as_view(), name="building-warehouse-stock-move-list"),

    # purchase documents (warehouse-like contract for procurement department)
    path("documents/purchase/", BuildingPurchaseDocumentListCreateView.as_view(), name="building-documents-purchase"),
    path("documents/purchase/<uuid:pk>/", BuildingPurchaseDocumentDetailView.as_view(), name="building-document-purchase-detail"),
    path("documents/purchase/<uuid:pk>/cash/approve/", BuildingPurchaseDocumentCashApproveView.as_view(), name="building-document-purchase-cash-approve"),
    path("documents/purchase/<uuid:pk>/cash/reject/", BuildingPurchaseDocumentCashRejectView.as_view(), name="building-document-purchase-cash-reject"),
]
