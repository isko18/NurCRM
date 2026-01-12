from django.urls import path 

from .views.warehouse import (
    WarehouseView,WarehouseDetailView
)

from .views.brands import (
    BrandView,BrandDetailView
)

from .views.category import (
    CategoryView,CategoryDetailView
)

from .views.product import (
    ProductView,ProductDetailView,
    ProductImagesView,ProductImageDetailView,
    ProductPackagesView,ProductPackageDetailView
)


# / -> create | list 
# /<uuid> -> update | delete | retrieve


# /<uuid>/products -> list | create  
# /products/<uuid> -> update | delete | retrieve

# products/<uuid>/images -> 
# product-images/<uuid> ->

# products/<uuid>/packages -> 
# products-packages/<uuid> ->


# warehouses-brands/ -> create | list 
# warehouses-category/ -> create | list

# warehouses-brands/<uuid> -> update | retireve
# warehouses-category/<uuid> -> update | retrieve


urlpatterns = [

    path("",WarehouseView.as_view() ,name="warehouse"),
    path("<uuid:pk>/",WarehouseDetailView.as_view() ,name="warehouse-detail"),

    path("brands/",BrandView.as_view(),name="warehouse-brand"),
    path("brands/<uuid:pk>",BrandDetailView.as_view(),name="warehouse-brand-detail"),

    path("category/",CategoryView.as_view(),name="warehouse-category"),
    path("category/<uuid:pk>",CategoryDetailView.as_view(),name="warehouse-category-detail"),

    path("<uuid:warehouse_uuid>/products/",ProductView.as_view(),name="warehouse-product"),
    path("products/<uuid:pk>/",ProductDetailView.as_view(),name="warehouse-detail-product"),

    path("products/<uuid:product_uuid>/images/", ProductImagesView.as_view(), name="product-images-list-create"),
    path("product-images/<uuid:pk>/", ProductImageDetailView.as_view(), name="product-image-detail"),

    path("products/<uuid:product_uuid>/packages/", ProductPackagesView.as_view(), name="product-packages-list-create"),
    path("products-packages/<uuid:pk>/", ProductPackageDetailView.as_view(), name="product-package-detail"),
]
