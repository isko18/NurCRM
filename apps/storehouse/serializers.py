from rest_framework import serializers
from .models import (
    Warehouse, Supplier, Product, Stock,
    StockIn, StockInItem,
    StockOut, StockOutItem,
    StockTransfer, StockTransferItem
)
from apps.main.models import ProductBrand, ProductCategory


class CompanyReadOnlyMixin(serializers.ModelSerializer):
    """
    company → read-only (id),
    при создании автоматически проставляется из request.user.company
    """
    company = serializers.ReadOnlyField(source="company.id")

    def _get_company(self):
        request = self.context.get("request")
        return getattr(getattr(request, "user", None), "company", None)

    def create(self, validated_data):
        company = self._get_company()
        if company is None:
            raise serializers.ValidationError("Невозможно определить компанию пользователя.")
        validated_data["company"] = company
        return super().create(validated_data)


# 📦 Склад
class WarehouseSerializer(CompanyReadOnlyMixin):
    class Meta:
        model = Warehouse
        fields = "__all__"


# 🚚 Поставщик
class SupplierSerializer(CompanyReadOnlyMixin):
    class Meta:
        model = Supplier
        fields = "__all__"


# 🛒 Товар
class ProductSerializer(CompanyReadOnlyMixin):
    brand_name = serializers.CharField(source="brand.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = "__all__"


# 📊 Остатки
class StockSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = Stock
        fields = "__all__"


# 📥 Приход
class StockInItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockInItem
        fields = "__all__"


class StockInSerializer(CompanyReadOnlyMixin):
    items = StockInItemSerializer(many=True)

    class Meta:
        model = StockIn
        fields = "__all__"

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        stock_in = super().create(validated_data)  # company автоматически
        for item_data in items_data:
            StockInItem.objects.create(stock_in=stock_in, **item_data)
        return stock_in


# 📤 Расход
class StockOutItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockOutItem
        fields = "__all__"


class StockOutSerializer(CompanyReadOnlyMixin):
    items = StockOutItemSerializer(many=True)

    class Meta:
        model = StockOut
        fields = "__all__"

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        stock_out = super().create(validated_data)
        for item_data in items_data:
            StockOutItem.objects.create(stock_out=stock_out, **item_data)
        return stock_out


# 🔄 Перемещение
class StockTransferItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockTransferItem
        fields = "__all__"


class StockTransferSerializer(CompanyReadOnlyMixin):
    items = StockTransferItemSerializer(many=True)

    class Meta:
        model = StockTransfer
        fields = "__all__"

    def create(self, validated_data):
        items_data = validated_data.pop("items")
        transfer = super().create(validated_data)
        for item_data in items_data:
            StockTransferItem.objects.create(transfer=transfer, **item_data)
        return transfer
