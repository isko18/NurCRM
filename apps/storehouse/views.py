from rest_framework import generics, permissions
from .models import (
    Warehouse, Supplier, Product, Stock,
    StockIn, StockOut, StockTransfer
)
from .serializers import (
    WarehouseSerializer, SupplierSerializer, ProductSerializer, StockSerializer,
    StockInSerializer, StockOutSerializer, StockTransferSerializer
)


# 📦 Склады
class WarehouseListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Warehouse.objects.filter(company=self.request.user.company)


class WarehouseDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Warehouse.objects.filter(company=self.request.user.company)


# 🚚 Поставщики
class SupplierListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Supplier.objects.filter(company=self.request.user.company)


class SupplierDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Supplier.objects.filter(company=self.request.user.company)


# 🛒 Товары
class ProductListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Product.objects.filter(company=self.request.user.company)


class ProductDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Product.objects.filter(company=self.request.user.company)


# 📊 Остатки
class StockListAPIView(generics.ListAPIView):
    serializer_class = StockSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # остатки по всем складам компании пользователя
        return Stock.objects.filter(warehouse__company=self.request.user.company)


class StockDetailAPIView(generics.RetrieveAPIView):
    serializer_class = StockSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Stock.objects.filter(warehouse__company=self.request.user.company)


# 📥 Приход
class StockInListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = StockInSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockIn.objects.filter(company=self.request.user.company)


class StockInDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockInSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockIn.objects.filter(company=self.request.user.company)


# 📤 Расход
class StockOutListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = StockOutSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockOut.objects.filter(company=self.request.user.company)


class StockOutDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockOutSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockOut.objects.filter(company=self.request.user.company)


# 🔄 Перемещения
class StockTransferListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockTransfer.objects.filter(company=self.request.user.company)


class StockTransferDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockTransferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return StockTransfer.objects.filter(company=self.request.user.company)
