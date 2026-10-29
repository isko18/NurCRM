# apps/construction/serializers/sale_history.py
from rest_framework import serializers

from apps.main.models import Sale, SaleItem


class SaleItemHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = [
            "id",
            "product",
            "name_snapshot",
            "barcode_snapshot",
            "unit_price",
            "quantity",
        ]
        read_only_fields = fields


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
