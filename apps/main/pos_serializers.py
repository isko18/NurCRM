# shop/api/pos_serializers.py
from rest_framework import serializers
from .models import Product
from .models import Cart, CartItem, Sale, MobileScannerToken

class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    barcode = serializers.CharField(source="product.barcode", read_only=True)
    class Meta:
        model = CartItem
        fields = ("id","product","product_name","barcode","quantity","unit_price")

class SaleCartSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    class Meta:
        model = Cart
        fields = ("id","status","subtotal","discount_total","tax_total","total","items")

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
        fields = ("token","expires_at")


class SaleListSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = ("id", "status", "subtotal", "discount_total", "tax_total",
                  "total", "created_at", "paid_at", "user_display")

    def get_user_display(self, obj):
        u = obj.user
        if not u:
            return None
        # подстрой под свои поля пользователя
        return getattr(u, "get_full_name", lambda: "")() or getattr(u, "email", None) or u.username