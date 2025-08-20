# shop/api/pos_serializers.py
from rest_framework import serializers
from .models import Product, Cart, CartItem, Sale, MobileScannerToken

# --- CartItem (ранее назывался SaleItemSerializer) ---
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
        if cart and product and hasattr(product, "company_id") and cart.company_id != product.company_id:
            raise serializers.ValidationError({"product": "Товар принадлежит другой компании, чем корзина."})
        return attrs

    def create(self, validated_data):
        cart = validated_data["cart"]
        validated_data.setdefault("unit_price", validated_data["product"].price)
        # проставляем company из cart
        validated_data["company"] = cart.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # company менять нельзя — держим как у cart
        if "company" in validated_data:
            validated_data.pop("company")
        if "cart" in validated_data:
            validated_data.pop("cart")
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
        return (getattr(u, "get_full_name", lambda: "")() or
                getattr(u, "email", None) or
                getattr(u, "username", None))
