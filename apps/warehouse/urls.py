from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    WarehouseView, WarehouseDetailView,
    BrandView, BrandDetailView,
    CategoryView, CategoryDetailView,
    ProductView, ProductDetailView,
    ProductImagesView, ProductImageDetailView,
    ProductPackagesView, ProductPackageDetailView,
    # legacy views
)
from .views_documents import (
    DocumentListCreateView, DocumentDetailView, DocumentPostView, DocumentUnpostView,
    ProductListCreateView, ProductDetailView as ProductDetailViewCRUD, 
    WarehouseListCreateView, WarehouseDetailView as WarehouseDetailViewCRUD,
    CounterpartyListCreateView, CounterpartyDetailView,
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

    # products in warehouse
    path("<uuid:warehouse_uuid>/products/", ProductView.as_view(), name="warehouse-products"),

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
    # documents
    path("documents/", DocumentListCreateView.as_view(), name="warehouse-documents"),
    path("documents/<uuid:pk>/", DocumentDetailView.as_view(), name="warehouse-document-detail"),
    path("documents/<uuid:pk>/post/", DocumentPostView.as_view(), name="warehouse-document-post"),
    path("documents/<uuid:pk>/unpost/", DocumentUnpostView.as_view(), name="warehouse-document-unpost"),

    # simple CRUD for products/warehouses/counterparties
    path("crud/products/", ProductListCreateView.as_view(), name="warehouse-products-crud"),
    path("crud/products/<uuid:pk>/", ProductDetailViewCRUD.as_view(), name="warehouse-product-detail-crud"),
    
    path("crud/warehouses/", WarehouseListCreateView.as_view(), name="warehouses-crud"),
    path("crud/warehouses/<uuid:pk>/", WarehouseDetailViewCRUD.as_view(), name="warehouses-detail-crud"),

    path("crud/counterparties/", CounterpartyListCreateView.as_view(), name="counterparties-crud"),
    path("crud/counterparties/<uuid:pk>/", CounterpartyDetailView.as_view(), name="counterparties-detail-crud"),
]
