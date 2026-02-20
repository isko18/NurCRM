from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    WarehouseView, WarehouseDetailView,
    BrandView, BrandDetailView,
    CategoryView, CategoryDetailView,
    ProductGroupView, ProductGroupDetailView,
    ProductView, ProductDetailView,
    ProductScanView,
    ProductImagesView, ProductImageDetailView,
    ProductPackagesView, ProductPackageDetailView,
    AgentRequestCartListCreateAPIView,
    AgentRequestCartRetrieveUpdateDestroyAPIView,
    AgentRequestCartSubmitAPIView,
    AgentRequestCartApproveAPIView,
    AgentRequestCartRejectAPIView,
    AgentRequestItemListCreateAPIView,
    AgentRequestItemDetailAPIView,
    AgentMyProductsListAPIView,
    OwnerAgentsProductsListAPIView,
    CompaniesSearchForAgentsAPIView,
    CompanyWarehouseAgentRequestListCreateAPIView,
    CompanyWarehouseAgentAcceptAPIView,
    CompanyWarehouseAgentRejectAPIView,
    CompanyWarehouseAgentRemoveAPIView,
)
from .views_documents import (
    DocumentListCreateView, DocumentDetailView, DocumentPostView, DocumentUnpostView,
    DocumentCashApproveView, DocumentCashRejectView,
    CashApprovalRequestListView, CashApprovalRequestApproveView, CashApprovalRequestRejectView,
    AgentDocumentListCreateView, AgentDocumentDetailView,
    DocumentTransferCreateAPIView,
    ProductListCreateView, ProductDetailView as ProductDetailViewCRUD,
    WarehouseListCreateView, WarehouseDetailView as WarehouseDetailViewCRUD,
    CounterpartyListCreateView, CounterpartyDetailView,
    DocumentSaleListCreateView, DocumentPurchaseListCreateView,
    DocumentSaleReturnListCreateView, DocumentPurchaseReturnListCreateView,
    DocumentInventoryListCreateView, DocumentReceiptListCreateView,
    DocumentWriteOffListCreateView, DocumentTransferListCreateView,
)
from .views_money import (
    CashRegisterListCreateView,
    CashRegisterDetailView,
    CashRegisterOperationsView,
    PaymentCategoryListCreateView,
    PaymentCategoryDetailView,
    MoneyDocumentListCreateView,
    MoneyDocumentDetailView,
    MoneyDocumentPostView,
    MoneyDocumentUnpostView,
    CounterpartyMoneyOperationsView,
)
from .views_reconciliation import (
    CounterpartyReconciliationClassicAPIView,
    CounterpartyReconciliationJSONAPIView,
)
from .views_analytics import (
    WarehouseAgentMyAnalyticsAPIView,
    WarehouseOwnerAgentAnalyticsAPIView,
    WarehouseOwnerOverallAnalyticsAPIView,
)

urlpatterns = [
    # warehouses
    path("", WarehouseView.as_view(), name="warehouse"),
    path("<uuid:warehouse_uuid>/", WarehouseDetailView.as_view(), name="warehouse-detail"),

    # brands
    path("brands/", BrandView.as_view(), name="warehouse-brand"),
    path("brands/<uuid:brand_uuid>/", BrandDetailView.as_view(), name="warehouse-brand-detail"),

    # categories
    path("category/", CategoryView.as_view(), name="warehouse-category"),
    path("category/<uuid:category_uuid>/", CategoryDetailView.as_view(), name="warehouse-category-detail"),

    # product groups (inside warehouse, like 1C)
    path("<uuid:warehouse_uuid>/groups/", ProductGroupView.as_view(), name="warehouse-product-groups"),
    path("<uuid:warehouse_uuid>/groups/<uuid:group_uuid>/", ProductGroupDetailView.as_view(), name="warehouse-product-group-detail"),

    # products in warehouse
    path("<uuid:warehouse_uuid>/products/", ProductView.as_view(), name="warehouse-products"),
    path("<uuid:warehouse_uuid>/products/scan/", ProductScanView.as_view(), name="warehouse-products-scan"),

    # product detail (global by product uuid)
    path("products/<uuid:product_uuid>/", ProductDetailView.as_view(), name="warehouse-product-detail"),

    # product images
    path("products/<uuid:product_uuid>/images/", ProductImagesView.as_view(), name="product-images"),
    path(
        "products/<uuid:product_uuid>/images/<uuid:image_uuid>/",
        ProductImageDetailView.as_view(),
        name="product-image-detail",
    ),

    # product packages
    path("products/<uuid:product_uuid>/packages/", ProductPackagesView.as_view(), name="product-packages"),
    path(
        "products/<uuid:product_uuid>/packages/<uuid:package_uuid>/",
        ProductPackageDetailView.as_view(),
        name="product-package-detail",
    ),
]

# register viewsets for documents and simple CRUD
urlpatterns += [
    # agent carts
    path("agent-carts/", AgentRequestCartListCreateAPIView.as_view(), name="warehouse-agent-carts"),
    path("agent-carts/<uuid:pk>/", AgentRequestCartRetrieveUpdateDestroyAPIView.as_view(), name="warehouse-agent-cart-detail"),
    path("agent-carts/<uuid:pk>/submit/", AgentRequestCartSubmitAPIView.as_view(), name="warehouse-agent-cart-submit"),
    path("agent-carts/<uuid:pk>/approve/", AgentRequestCartApproveAPIView.as_view(), name="warehouse-agent-cart-approve"),
    path("agent-carts/<uuid:pk>/reject/", AgentRequestCartRejectAPIView.as_view(), name="warehouse-agent-cart-reject"),

    # agent cart items
    path("agent-cart-items/", AgentRequestItemListCreateAPIView.as_view(), name="warehouse-agent-cart-items"),
    path("agent-cart-items/<uuid:pk>/", AgentRequestItemDetailAPIView.as_view(), name="warehouse-agent-cart-item-detail"),

    # agent stock
    path("agents/me/products/", AgentMyProductsListAPIView.as_view(), name="warehouse-agent-my-products"),
    path("owner/agents/products/", OwnerAgentsProductsListAPIView.as_view(), name="warehouse-owner-agents-products"),

    # агенты: поиск компаний и заявки в компанию
    path("agents/companies/search/", CompaniesSearchForAgentsAPIView.as_view(), name="warehouse-agents-companies-search"),
    path("agents/company-requests/", CompanyWarehouseAgentRequestListCreateAPIView.as_view(), name="warehouse-agents-company-requests"),
    path("agents/company-requests/<uuid:pk>/accept/", CompanyWarehouseAgentAcceptAPIView.as_view(), name="warehouse-agents-company-request-accept"),
    path("agents/company-requests/<uuid:pk>/reject/", CompanyWarehouseAgentRejectAPIView.as_view(), name="warehouse-agents-company-request-reject"),
    path("agents/company-requests/<uuid:pk>/remove/", CompanyWarehouseAgentRemoveAPIView.as_view(), name="warehouse-agents-company-request-remove"),
]

urlpatterns += [
    # agent documents
    path("agent/documents/", AgentDocumentListCreateView.as_view(), name="warehouse-agent-documents"),
    path("agent/documents/<uuid:pk>/", AgentDocumentDetailView.as_view(), name="warehouse-agent-document-detail"),
]

urlpatterns += [
    # analytics
    path("agents/me/analytics/", WarehouseAgentMyAnalyticsAPIView.as_view(), name="warehouse-agent-my-analytics"),
    path("owner/agents/<uuid:agent_id>/analytics/", WarehouseOwnerAgentAnalyticsAPIView.as_view(), name="warehouse-owner-agent-analytics"),
    path("owner/analytics/", WarehouseOwnerOverallAnalyticsAPIView.as_view(), name="warehouse-owner-analytics"),
]

urlpatterns += [
    # documents
    path("documents/", DocumentListCreateView.as_view(), name="warehouse-documents"),
    path("transfer/", DocumentTransferCreateAPIView.as_view(), name="warehouse-transfer"),
    path("documents/sale/", DocumentSaleListCreateView.as_view(), name="warehouse-documents-sale"),
    path("documents/purchase/", DocumentPurchaseListCreateView.as_view(), name="warehouse-documents-purchase"),
    path("documents/sale-return/", DocumentSaleReturnListCreateView.as_view(), name="warehouse-documents-sale-return"),
    path("documents/purchase-return/", DocumentPurchaseReturnListCreateView.as_view(), name="warehouse-documents-purchase-return"),
    path("documents/inventory/", DocumentInventoryListCreateView.as_view(), name="warehouse-documents-inventory"),
    path("documents/receipt/", DocumentReceiptListCreateView.as_view(), name="warehouse-documents-receipt"),
    path("documents/write-off/", DocumentWriteOffListCreateView.as_view(), name="warehouse-documents-write-off"),
    path("documents/transfer/", DocumentTransferListCreateView.as_view(), name="warehouse-documents-transfer"),
    path("documents/<uuid:pk>/", DocumentDetailView.as_view(), name="warehouse-document-detail"),
    path("documents/<uuid:pk>/post/", DocumentPostView.as_view(), name="warehouse-document-post"),
    path("documents/<uuid:pk>/unpost/", DocumentUnpostView.as_view(), name="warehouse-document-unpost"),
    path("documents/<uuid:pk>/cash/approve/", DocumentCashApproveView.as_view(), name="warehouse-document-cash-approve"),
    path("documents/<uuid:pk>/cash/reject/", DocumentCashRejectView.as_view(), name="warehouse-document-cash-reject"),
    path("cash/requests/", CashApprovalRequestListView.as_view(), name="warehouse-cash-requests"),
    path("cash/requests/<uuid:pk>/approve/", CashApprovalRequestApproveView.as_view(), name="warehouse-cash-request-approve"),
    path("cash/requests/<uuid:pk>/reject/", CashApprovalRequestRejectView.as_view(), name="warehouse-cash-request-reject"),

    # simple CRUD for products/warehouses/counterparties
    path("crud/products/", ProductListCreateView.as_view(), name="warehouse-products-crud"),
    path("crud/products/<uuid:pk>/", ProductDetailViewCRUD.as_view(), name="warehouse-product-detail-crud"),
    path("crud/warehouses/", WarehouseListCreateView.as_view(), name="warehouses-crud"),
    path("crud/warehouses/<uuid:pk>/", WarehouseDetailViewCRUD.as_view(), name="warehouses-detail-crud"),

    path("crud/counterparties/", CounterpartyListCreateView.as_view(), name="counterparties-crud"),
    path("crud/counterparties/<uuid:pk>/", CounterpartyDetailView.as_view(), name="counterparties-detail-crud"),
]

urlpatterns += [
    # cash registers (касса)
    path("cash-registers/", CashRegisterListCreateView.as_view(), name="cash-registers"),
    path("cash-registers/<uuid:pk>/", CashRegisterDetailView.as_view(), name="cash-register-detail"),
    path("cash-registers/<uuid:pk>/operations/", CashRegisterOperationsView.as_view(), name="cash-register-operations"),

    # money categories
    path("money/categories/", PaymentCategoryListCreateView.as_view(), name="money-categories"),
    path("money/categories/<uuid:pk>/", PaymentCategoryDetailView.as_view(), name="money-category-detail"),

    # money documents
    path("money/documents/", MoneyDocumentListCreateView.as_view(), name="money-documents"),
    path("money/documents/<uuid:pk>/", MoneyDocumentDetailView.as_view(), name="money-document-detail"),
    path("money/documents/<uuid:pk>/post/", MoneyDocumentPostView.as_view(), name="money-document-post"),
    path("money/documents/<uuid:pk>/unpost/", MoneyDocumentUnpostView.as_view(), name="money-document-unpost"),

    # money operations by counterparty
    path(
        "money/counterparties/<uuid:counterparty_id>/operations/",
        CounterpartyMoneyOperationsView.as_view(),
        name="money-operations-by-counterparty",
    ),
]

urlpatterns += [
    # reconciliation
    path(
        "counterparties/<uuid:counterparty_id>/reconciliation/",
        CounterpartyReconciliationClassicAPIView.as_view(),
        name="counterparty-reconciliation",
    ),
    path(
        "counterparties/<uuid:counterparty_id>/reconciliation/json/",
        CounterpartyReconciliationJSONAPIView.as_view(),
        name="counterparty-reconciliation-json",
    ),
]
