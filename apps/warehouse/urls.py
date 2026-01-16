from django.urls import path

from .views import (
    WarehouseView, WarehouseDetailView,
    BrandView, BrandDetailView,
    CategoryView, CategoryDetailView,
    ProductView, ProductDetailView,
    ProductImagesView, ProductImageDetailView,
    ProductPackagesView, ProductPackageDetailView,
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
