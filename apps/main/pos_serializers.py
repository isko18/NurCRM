from decimal import Decimal, ROUND_HALF_UP
from rest_framework import serializers

from .models import (
    Product, Cart, CartItem, Sale, SaleItem, MobileScannerToken
)

# ---------- Helpers ----------

Q2 = Decimal("0.01")

def money(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(Q2, rounding=ROUND_HALF_UP)


class MoneyField(serializers.DecimalField):
    """
    Денежное поле: 2 знака, округление ROUND_HALF_UP.
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(*args, **kwargs)

    def to_internal_value(self, value):
        val = super().to_internal_value(value)
        return money(val)


# --- Позиция в корзине ---
class StartCartOptionsSerializer(serializers.Serializer):
    """
    Опциональные настройки при старте корзины.
    Скидка на весь заказ указывается суммой (MoneyField).
    """
    order_discount_total = MoneyField(required=False)

    def validate_order_discount_total(self, v):
        if v is not None and v < 0:
            raise serializers.ValidationError("Должна быть ≥ 0.")
        return v

class CustomCartItemCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    price = MoneyField()
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    barcode = serializers.CharField(source="product.barcode", read_only=True)
    display_name = serializers.SerializerMethodField()  # ← будем отдавать имя товара или custom_name

    class Meta:
        model = CartItem
        fields = (
            "id", "cart", "product",
            "product_name", "barcode",
            "quantity", "unit_price",
            "display_name",              # ← добавить в выдачу
        )
        read_only_fields = ("id", "product_name", "barcode", "display_name")

    def get_display_name(self, obj):
        # если product есть — берём его имя, иначе кастомное
        return getattr(getattr(obj, "product", None), "name", None) or (getattr(obj, "custom_name", "") or "")

    def validate(self, attrs):
        cart = attrs.get("cart") or getattr(self.instance, "cart", None)
        product = attrs.get("product") or getattr(self.instance, "product", None)
        if cart and product and cart.company_id != getattr(product, "company_id", None):
            raise serializers.ValidationError({"product": "Товар принадлежит другой компании, чем корзина."})
        return attrs

    def create(self, validated_data):
        cart = validated_data["cart"]
        # для «обычных» товаров по умолчанию берём цену продукта;
        # кастомные позиции создаются отдельным эндпоинтом через CustomCartItemCreateSerializer
        if validated_data.get("product") and "unit_price" not in validated_data:
            validated_data["unit_price"] = validated_data["product"].price

        # Если у CartItem есть поле company — проставим из корзины
        if any(f.name == "company" for f in CartItem._meta.fields):
            validated_data.setdefault("company", cart.company)
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
        fields = ("id", "status", "subtotal", "discount_total", "order_discount_total", "tax_total", "total", "items")


class ScanRequestSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)


class AddItemSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)
    # Можно задать либо цену позиции (после скидки), либо скидку на всю строку:
    unit_price = MoneyField(required=False)
    discount_total = MoneyField(required=False)

    def validate(self, attrs):
        up = attrs.get("unit_price")
        disc = attrs.get("discount_total")
        qty = attrs.get("quantity", 1)

        if up is not None and up < 0:
            raise serializers.ValidationError({"unit_price": "Должна быть ≥ 0."})
        if disc is not None and disc < 0:
            raise serializers.ValidationError({"discount_total": "Должна быть ≥ 0."})
        if up is not None and disc is not None:
            raise serializers.ValidationError("Передавайте либо unit_price, либо discount_total.")
        if qty < 1:
            raise serializers.ValidationError({"quantity": "Минимум 1."})
        return attrs


class OptionalUUIDField(serializers.UUIDField):
    """
    UUID-поле, которое принимает None/""/"null" как отсутствие значения.
    Удобно для фронтов, которые шлют пустую строку.
    """
    def to_internal_value(self, data):
        if data in (None, "", "null", "None"):
            return None
        return super().to_internal_value(data)


class CheckoutSerializer(serializers.Serializer):
    print_receipt = serializers.BooleanField(default=False)
    client_id = OptionalUUIDField(required=False, allow_null=True)
    # (опц.) чтобы не читать department_id напрямую из request.data во вьюхе:
    department_id = OptionalUUIDField(required=False, allow_null=True)


class MobileScannerTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileScannerToken
        fields = ("token", "expires_at")
        read_only_fields = ("token", "expires_at")


# ============== ПРОДАЖИ ==============

class SaleListSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    client_name = serializers.CharField(source="client.full_name", read_only=True)

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
            "client",        # id клиента
            "client_name",   # имя клиента
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
        return money(total)


class SaleDetailSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField(read_only=True)
    items = SaleItemReadSerializer(many=True, read_only=True)
    client_name = serializers.CharField(source="client.full_name", read_only=True)

    class Meta:
        model = Sale
        fields = (
            "id",
            "status",          # <-- допускаем запись
            "subtotal",
            "discount_total",
            "tax_total",
            "total",
            "created_at",
            "paid_at",
            "user_display",
            "client",          # id клиента
            "client_name",     # имя клиента
            "items",
        )
        # всё read-only, КРОМЕ status
        read_only_fields = (
            "id",
            "subtotal",
            "discount_total",
            "tax_total",
            "total",
            "created_at",
            "paid_at",
            "user_display",
            "client",
            "client_name",
            "items",
        )

    def get_user_display(self, obj):
        u = obj.user
        if not u:
            return None
        return (
            getattr(u, "get_full_name", lambda: "")()
            or getattr(u, "email", None)
            or getattr(u, "username", None)
        )

class SaleStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sale
        fields = ("status",)
