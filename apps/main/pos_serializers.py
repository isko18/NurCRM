from decimal import Decimal, ROUND_HALF_UP
from rest_framework import serializers

from .models import (
    Product, Cart, CartItem, Sale, SaleItem, MobileScannerToken
)

# --- Позиция в корзине ---
class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    barcode = serializers.CharField(source="product.barcode", read_only=True)

    class Meta:
        model = CartItem
        fields = ("id", "cart", "product", "product_name", "barcode", "quantity", "unit_price")
        read_only_fields = ("id", "product_name", "barcode")

    def validate(self, attrs):
        cart = attrs.get("cart") or getattr(self.instance, "cart", None)
        product = attrs.get("product") or getattr(self.instance, "product", None)
        if cart and product and cart.company_id != getattr(product, "company_id", None):
            raise serializers.ValidationError({"product": "Товар принадлежит другой компании, чем корзина."})
        return attrs

    def create(self, validated_data):
        cart = validated_data["cart"]
        validated_data.setdefault("unit_price", validated_data["product"].price)
        # Если у CartItem есть поле company — проставим из корзины
        if any(f.name == "company" for f in CartItem._meta.fields):
            validated_data["company"] = cart.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # company/cart менять нельзя
        validated_data.pop("company", None)
        validated_data.pop("cart", None)
        return super().update(instance, validated_data)


class SaleCartSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)

    class Meta:
        model = Cart
        fields = ("id", "status", "subtotal", "discount_total", "tax_total", "total", "items")


class ScanRequestSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)


class AddItemSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)


class CheckoutSerializer(serializers.Serializer):
    print_receipt = serializers.BooleanField(default=False)


class MobileScannerTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileScannerToken
        fields = ("token", "expires_at")
        read_only_fields = ("token", "expires_at")


# ============== ПРОДАЖИ ==============

class SaleListSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = (
            "id", "status", "subtotal", "discount_total", "tax_total",
            "total", "created_at", "paid_at", "user_display"
        )

    def get_user_display(self, obj):
        u = obj.user
        if not u:
            return None
        return (getattr(u, "get_full_name", lambda: "")()
                or getattr(u, "email", None)
                or getattr(u, "username", None))


class SaleItemReadSerializer(serializers.ModelSerializer):
    """
    Позиция продажи. Показываем:
    - текущее имя product (если он ещё существует),
    - name_snapshot / barcode_snapshot (снимки на момент продажи),
    - рассчитанный line_total.
    """
    product_name = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = SaleItem
        fields = (
            "id",
            "product",          # может быть null, если товар удалён
            "product_name",     # текущее имя товара (если есть product), иначе из снимка
            "name_snapshot",    # снимок имени
            "barcode_snapshot", # снимок штрих-кода
            "unit_price",
            "quantity",
            "line_total",
        )
        read_only_fields = fields

    def get_product_name(self, obj):
        # если связанный product ещё жив — берём его имя; иначе используем снимок
        return getattr(getattr(obj, "product", None), "name", None) or obj.name_snapshot

    def get_line_total(self, obj):
        # аккуратно считаем Decimal сумму и округляем до 2 знаков
        total = (obj.unit_price or Decimal("0")) * Decimal(obj.quantity or 0)
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class SaleDetailSerializer(serializers.ModelSerializer):
    """
    Детальная продажа с вложенными товарами (read-only).
    """
    user_display = serializers.SerializerMethodField()
    items = SaleItemReadSerializer(many=True, read_only=True)

    class Meta:
        model = Sale
        fields = (
            "id",
            "status",
            "subtotal",
            "discount_total",
            "tax_total",
            "total",
            "created_at",
            "paid_at",
            "user_display",
            "items",
        )
        read_only_fields = fields

    def get_user_display(self, obj):
        u = obj.user
        if not u:
            return None
        return (getattr(u, "get_full_name", lambda: "")()
                or getattr(u, "email", None)
                or getattr(u, "username", None))
