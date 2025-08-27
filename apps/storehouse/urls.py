from django.urls import path
from .views import (
    WarehouseListCreateAPIView, WarehouseDetailAPIView,
    SupplierListCreateAPIView, SupplierDetailAPIView,
    ProductListCreateAPIView, ProductDetailAPIView,
    StockListAPIView, StockDetailAPIView,
    StockInListCreateAPIView, StockInDetailAPIView,
    StockOutListCreateAPIView, StockOutDetailAPIView,
    StockTransferListCreateAPIView, StockTransferDetailAPIView,
)

urlpatterns = [
    # 📦 Склады
    path("warehouses/", WarehouseListCreateAPIView.as_view(), name="warehouse-list-create"),
    path("warehouses/<uuid:pk>/", WarehouseDetailAPIView.as_view(), name="warehouse-detail"),

    # 🚚 Поставщики
    path("suppliers/", SupplierListCreateAPIView.as_view(), name="supplier-list-create"),
    path("suppliers/<uuid:pk>/", SupplierDetailAPIView.as_view(), name="supplier-detail"),

    # 🛒 Товары
    path("products/", ProductListCreateAPIView.as_view(), name="product-list-create"),
    path("products/<uuid:pk>/", ProductDetailAPIView.as_view(), name="product-detail"),

    # 📊 Остатки
    path("stocks/", StockListAPIView.as_view(), name="stock-list"),
    path("stocks/<uuid:pk>/", StockDetailAPIView.as_view(), name="stock-detail"),

    # 📥 Приход
    path("stock-in/", StockInListCreateAPIView.as_view(), name="stockin-list-create"),
    path("stock-in/<uuid:pk>/", StockInDetailAPIView.as_view(), name="stockin-detail"),

    # 📤 Расход
    path("stock-out/", StockOutListCreateAPIView.as_view(), name="stockout-list-create"),
    path("stock-out/<uuid:pk>/", StockOutDetailAPIView.as_view(), name="stockout-detail"),

    # 🔄 Перемещения
    path("stock-transfer/", StockTransferListCreateAPIView.as_view(), name="stocktransfer-list-create"),
    path("stock-transfer/<uuid:pk>/", StockTransferDetailAPIView.as_view(), name="stocktransfer-detail"),
]
