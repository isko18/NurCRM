from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    WarehouseView, WarehouseDetailView,
    BrandView, BrandDetailView,
    CategoryView, CategoryDetailView,
    ProductGroupView, ProductGroupDetailView,
    ProductView, ProductDetailView,
    WarehouseProductCatalogListView,
    ProductScanView,
    WarehouseBarcodeCheckAPIView,
    WarehouseMassIncomingAPIView,
    ProductImagesView, ProductImageDetailView,
    ProductPackagesView, ProductPackageDetailView,
    AgentRequestCartListCreateAPIView,
    AgentRequestCartRetrieveUpdateDestroyAPIView,
    AgentRequestCartSubmitAPIView,
    AgentRequestCartApproveAPIView,
    AgentRequestCartRejectAPIView,
    AgentRequestCartDispatchAPIView,
    AgentRequestCartCreateSaleAPIView,
    AgentRequestItemListCreateAPIView,
    AgentRequestItemDetailAPIView,
    AgentReturnCartListCreateAPIView,
    AgentReturnCartRetrieveUpdateDestroyAPIView,
    AgentReturnCartSubmitAPIView,
    AgentReturnCartApproveAPIView,
    AgentReturnCartRejectAPIView,
    AgentReturnCartReceiveAPIView,
    AgentReturnItemListCreateAPIView,
    AgentReturnItemDetailAPIView,
    AgentMyProductsListAPIView,
    OwnerAgentsProductsListAPIView,
    OwnerAgentProductsListAPIView,
    CompaniesSearchForAgentsAPIView,
    CompanyWarehouseAgentRequestListCreateAPIView,
    CompanyWarehouseAgentAcceptAPIView,
    CompanyWarehouseAgentRejectAPIView,
    CompanyWarehouseAgentRemoveAPIView,
    CompanyWarehouseAgentCommonAccessUpdateAPIView,
    CompanyWarehouseAgentAdminAssignAPIView,
)
from .views_documents import (
    DocumentListCreateView, DocumentDetailView, DocumentPostView, DocumentUnpostView,
    DocumentCashApproveView, DocumentCashRejectView,
    CashApprovalRequestListView, CashApprovalRequestApproveView, CashApprovalRequestRejectView,
    AgentDocumentListCreateView, AgentDocumentDetailView,
    DocumentScanView,
    DocumentTransferCreateAPIView,
    ProductListCreateView, ProductDetailView as ProductDetailViewCRUD,
    WarehouseListCreateView, WarehouseDetailView as WarehouseDetailViewCRUD,
    CounterpartyListCreateView, CounterpartyDetailView,
    CounterpartyBalanceSummaryView,
    DocumentSaleListCreateView, DocumentPurchaseListCreateView,
    DocumentSaleReturnListCreateView, DocumentPurchaseReturnListCreateView,
    DocumentInventoryListCreateView, DocumentReceiptListCreateView,
    DocumentWriteOffListCreateView, DocumentCommercialOfferListCreateView, DocumentTransferListCreateView,
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
    MoneyDocumentRejectView,
    CounterpartyMoneyOperationsView,
    WarehouseCashConfirmationSettingsView,
)
from .views_reconciliation import (
    CounterpartyReconciliationClassicAPIView,
    CounterpartyReconciliationJSONAPIView,
)
from .views_partnership import (
    CompanyStockPartnershipRequestListCreateAPIView,
    CompanyStockPartnershipRequestAcceptAPIView,
    CompanyStockPartnershipRequestRejectAPIView,
    CompanyStockPartnershipRequestCancelAPIView,
    PartnerCompaniesListAPIView,
    PartnerCompanyCatalogAPIView,
    DocumentPartnerTransferCreateAPIView,
    PartnerCashIncassationListCreateAPIView,
)
from .views_analytics import (
    WarehouseAgentMyAnalyticsAPIView,
    WarehouseOwnerAgentAnalyticsAPIView,
    WarehouseOwnerAgentsSalesAnalyticsAPIView,
    WarehouseOwnerOverallAnalyticsAPIView,
    WarehouseOwnerPartnersAnalyticsAPIView,
    WarehouseOwnerPartnerAnalyticsAPIView,
)
from .views_summaries import WarehouseSalesSummaryViewSet

summary_router = DefaultRouter()
summary_router.register(r"summaries", WarehouseSalesSummaryViewSet, basename="warehouse-summary")

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
    path("<uuid:warehouse_uuid>/barcode-check/<str:code>/", WarehouseBarcodeCheckAPIView.as_view(), name="warehouse-barcode-check-code"),
    path("<uuid:warehouse_uuid>/barcode-check/", WarehouseBarcodeCheckAPIView.as_view(), name="warehouse-barcode-check"),
    path("<uuid:warehouse_uuid>/mass-incoming/", WarehouseMassIncomingAPIView.as_view(), name="warehouse-mass-incoming"),

    # global product catalog across all company warehouses (list only)
    path("products/", WarehouseProductCatalogListView.as_view(), name="warehouse-products-catalog"),

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
    path("agent-carts/<uuid:pk>/dispatch/", AgentRequestCartDispatchAPIView.as_view(), name="warehouse-agent-cart-dispatch"),
    path("agent-carts/<uuid:pk>/create-sale/", AgentRequestCartCreateSaleAPIView.as_view(), name="warehouse-agent-cart-create-sale"),

    # agent cart items
    path("agent-cart-items/", AgentRequestItemListCreateAPIView.as_view(), name="warehouse-agent-cart-items"),
    path("agent-cart-items/<uuid:pk>/", AgentRequestItemDetailAPIView.as_view(), name="warehouse-agent-cart-item-detail"),

    # agent return carts
    path("agent-return-carts/", AgentReturnCartListCreateAPIView.as_view(), name="warehouse-agent-return-carts"),
    path("agent-return-carts/<uuid:pk>/", AgentReturnCartRetrieveUpdateDestroyAPIView.as_view(), name="warehouse-agent-return-cart-detail"),
    path("agent-return-carts/<uuid:pk>/submit/", AgentReturnCartSubmitAPIView.as_view(), name="warehouse-agent-return-cart-submit"),
    path("agent-return-carts/<uuid:pk>/approve/", AgentReturnCartApproveAPIView.as_view(), name="warehouse-agent-return-cart-approve"),
    path("agent-return-carts/<uuid:pk>/reject/", AgentReturnCartRejectAPIView.as_view(), name="warehouse-agent-return-cart-reject"),
    path("agent-return-carts/<uuid:pk>/receive/", AgentReturnCartReceiveAPIView.as_view(), name="warehouse-agent-return-cart-receive"),
    path("agent-return-cart-items/", AgentReturnItemListCreateAPIView.as_view(), name="warehouse-agent-return-cart-items"),
    path("agent-return-cart-items/<uuid:pk>/", AgentReturnItemDetailAPIView.as_view(), name="warehouse-agent-return-cart-item-detail"),

    # agent stock
    path("agents/me/products/", AgentMyProductsListAPIView.as_view(), name="warehouse-agent-my-products"),
    path("owner/agents/products/", OwnerAgentsProductsListAPIView.as_view(), name="warehouse-owner-agents-products"),
    path("owner/agents/<uuid:agent_id>/products/", OwnerAgentProductsListAPIView.as_view(), name="warehouse-owner-agent-products"),

    # агенты: поиск компаний и заявки в компанию
    path("agents/companies/search/", CompaniesSearchForAgentsAPIView.as_view(), name="warehouse-agents-companies-search"),
    path("agents/company-requests/", CompanyWarehouseAgentRequestListCreateAPIView.as_view(), name="warehouse-agents-company-requests"),
    path("agents/company-requests/<uuid:pk>/accept/", CompanyWarehouseAgentAcceptAPIView.as_view(), name="warehouse-agents-company-request-accept"),
    path("agents/company-requests/<uuid:pk>/reject/", CompanyWarehouseAgentRejectAPIView.as_view(), name="warehouse-agents-company-request-reject"),
    path("agents/company-requests/<uuid:pk>/remove/", CompanyWarehouseAgentRemoveAPIView.as_view(), name="warehouse-agents-company-request-remove"),
    path("agents/company-requests/<uuid:pk>/common-access/", CompanyWarehouseAgentCommonAccessUpdateAPIView.as_view(), name="warehouse-agents-company-request-common-access"),
    path("agents/company-memberships/", CompanyWarehouseAgentAdminAssignAPIView.as_view(), name="warehouse-agents-company-memberships"),
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
    path("owner/agents/analytics/", WarehouseOwnerAgentsSalesAnalyticsAPIView.as_view(), name="warehouse-owner-agents-sales-analytics"),
    path("owner/analytics/", WarehouseOwnerOverallAnalyticsAPIView.as_view(), name="warehouse-owner-analytics"),
    path("owner/partners/analytics/", WarehouseOwnerPartnersAnalyticsAPIView.as_view(), name="warehouse-owner-partners-analytics"),
    path(
        "owner/partners/<uuid:partner_company_id>/analytics/",
        WarehouseOwnerPartnerAnalyticsAPIView.as_view(),
        name="warehouse-owner-partner-analytics",
    ),
]

urlpatterns += [
    # documents
    path("documents/", DocumentListCreateView.as_view(), name="warehouse-documents"),
    path("documents/scan/", DocumentScanView.as_view(), name="warehouse-documents-scan"),
    path("transfer/", DocumentTransferCreateAPIView.as_view(), name="warehouse-transfer"),
    path("stock-partnership-requests/", CompanyStockPartnershipRequestListCreateAPIView.as_view(), name="warehouse-stock-partnership-requests"),
    path("stock-partnership-requests/<uuid:pk>/accept/", CompanyStockPartnershipRequestAcceptAPIView.as_view(), name="warehouse-stock-partnership-request-accept"),
    path("stock-partnership-requests/<uuid:pk>/reject/", CompanyStockPartnershipRequestRejectAPIView.as_view(), name="warehouse-stock-partnership-request-reject"),
    path("stock-partnership-requests/<uuid:pk>/cancel/", CompanyStockPartnershipRequestCancelAPIView.as_view(), name="warehouse-stock-partnership-request-cancel"),
    path("stock-partnerships/active/", PartnerCompaniesListAPIView.as_view(), name="warehouse-stock-partnerships-active"),
    path("stock-partnerships/companies/<uuid:company_id>/catalog/", PartnerCompanyCatalogAPIView.as_view(), name="warehouse-stock-partnership-catalog"),
    path("stock-partnerships/transfer/", DocumentPartnerTransferCreateAPIView.as_view(), name="warehouse-stock-partnership-transfer"),
    path("stock-partnerships/cash-incassations/", PartnerCashIncassationListCreateAPIView.as_view(), name="warehouse-stock-partnership-cash-incassations"),
    path("documents/sale/", DocumentSaleListCreateView.as_view(), name="warehouse-documents-sale"),
    path("documents/purchase/", DocumentPurchaseListCreateView.as_view(), name="warehouse-documents-purchase"),
    path("documents/sale-return/", DocumentSaleReturnListCreateView.as_view(), name="warehouse-documents-sale-return"),
    path("documents/purchase-return/", DocumentPurchaseReturnListCreateView.as_view(), name="warehouse-documents-purchase-return"),
    path("documents/inventory/", DocumentInventoryListCreateView.as_view(), name="warehouse-documents-inventory"),
    path("documents/receipt/", DocumentReceiptListCreateView.as_view(), name="warehouse-documents-receipt"),
    path("documents/write-off/", DocumentWriteOffListCreateView.as_view(), name="warehouse-documents-write-off"),
    path("documents/commercial-offer/", DocumentCommercialOfferListCreateView.as_view(), name="warehouse-documents-commercial-offer"),
    path("documents/transfer/", DocumentTransferListCreateView.as_view(), name="warehouse-documents-transfer"),
    path("documents/<uuid:pk>/", DocumentDetailView.as_view(), name="warehouse-document-detail"),
    path("documents/<uuid:pk>/post/", DocumentPostView.as_view(), name="warehouse-document-post"),
    path("documents/<uuid:pk>/unpost/", DocumentUnpostView.as_view(), name="warehouse-document-unpost"),
    path("documents/<uuid:pk>/cash/approve/", DocumentCashApproveView.as_view(), name="warehouse-document-cash-approve"),
    path("documents/<uuid:pk>/cash/reject/", DocumentCashRejectView.as_view(), name="warehouse-document-cash-reject"),
    path("cash/requests/", CashApprovalRequestListView.as_view(), name="warehouse-cash-requests"),
    path("cash/requests/<uuid:pk>/approve/", CashApprovalRequestApproveView.as_view(), name="warehouse-cash-request-approve"),
    path("cash/requests/<uuid:pk>/reject/", CashApprovalRequestRejectView.as_view(), name="warehouse-cash-request-reject"),
    path("cash/confirmation-settings/", WarehouseCashConfirmationSettingsView.as_view(), name="warehouse-cash-confirmation-settings"),

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
    path("money/documents/<uuid:pk>/reject/", MoneyDocumentRejectView.as_view(), name="money-document-reject"),

    # money operations by counterparty
    path(
        "money/counterparties/<uuid:counterparty_id>/operations/",
        CounterpartyMoneyOperationsView.as_view(),
        name="money-operations-by-counterparty",
    ),
]

urlpatterns += [
    # сводка по контрагентам за период (сальдо/оборот)
    path(
        "counterparties/balance-summary/",
        CounterpartyBalanceSummaryView.as_view(),
        name="counterparties-balance-summary",
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

# Сводки продаж (раздел «Сводка»)
urlpatterns += summary_router.urls

# Зарплата агентов (раздел «Зарплата»)
from .salary_views import (
    SalaryRateListAPIView,
    SalaryRateUpdateAPIView,
    SalaryAccrualListAPIView,
    SalarySummaryAPIView,
    SalaryPayoutListCreateAPIView,
)

urlpatterns += [
    path("salary/rates/", SalaryRateListAPIView.as_view(), name="warehouse-salary-rates"),
    path("salary/rates/<uuid:warehouse_id>/", SalaryRateUpdateAPIView.as_view(), name="warehouse-salary-rate-update"),
    path("salary/accruals/", SalaryAccrualListAPIView.as_view(), name="warehouse-salary-accruals"),
    path("salary/summary/", SalarySummaryAPIView.as_view(), name="warehouse-salary-summary"),
    path("salary/payouts/", SalaryPayoutListCreateAPIView.as_view(), name="warehouse-salary-payouts"),
]
