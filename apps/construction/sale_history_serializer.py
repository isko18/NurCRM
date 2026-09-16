# apps/construction/serializers/sale_history.py
from decimal import Decimal

from rest_framework import serializers

from apps.main.models import Sale, SaleItem


class SaleItemHistorySerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()
    qty = serializers.DecimalField(source="quantity", max_digits=12, decimal_places=3, read_only=True)
    price = serializers.DecimalField(source="unit_price", max_digits=12, decimal_places=2, read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = SaleItem
        fields = [
            "id",
            "product",
            "product_name",
            "name_snapshot",
            "barcode_snapshot",
            "unit_price",
            "quantity",
            "qty",
            "price",
            "line_total",
        ]
        read_only_fields = fields

    def get_product_name(self, obj):
        return getattr(getattr(obj, "product", None), "name", None) or obj.name_snapshot

    def get_line_total(self, obj):
        quantity = obj.quantity or Decimal("0")
        price = obj.unit_price or Decimal("0")
        discount = getattr(obj, "line_discount", None) or Decimal("0")
        return (price * quantity - discount).quantize(Decimal("0.01"))


class SaleHistorySerializer(serializers.ModelSerializer):
    change = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    client_name = serializers.SerializerMethodField()
    cashier_display = serializers.SerializerMethodField()
    items = SaleItemHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id",
            "company",
            "branch",
            "shift",
            "cashbox",

            "status",
            "doc_number",

            "payment_method",
            "cash_received",
            "change",

            "subtotal",
            "discount_total",
            "tax_total",
            "total",

            "created_at",
            "paid_at",

            "client",
            "client_name",
            "user",
            "cashier_display",

            "items",
        ]
        read_only_fields = fields

    def get_client_name(self, obj):
        c = getattr(obj, "client", None)
        if not c:
            return None
        return getattr(c, "name", None) or str(c)

    def get_cashier_display(self, obj):
        u = getattr(obj, "user", None)
        if not u:
            return None
        fn = getattr(u, "get_full_name", None)
        return (fn() if callable(fn) else "") or getattr(u, "email", None) or getattr(u, "username", None)
