from decimal import Decimal, ROUND_HALF_UP
from rest_framework import serializers

from apps.construction.models import Cashbox  # ✅ нужно для checkout без смены
from .models import (
    Product, Cart, CartItem, Sale, SaleItem, MobileScannerToken, ProductImage
)

Q2 = Decimal("0.01")


def money(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(Q2, rounding=ROUND_HALF_UP)


def _has_field(model, name: str) -> bool:
    try:
        return any(f.name == name for f in model._meta.get_fields())
    except Exception:
        return False


def _get_attr(obj, name, default=None):
    return getattr(obj, name, default) if obj is not None else default


class MoneyField(serializers.DecimalField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(*args, **kwargs)

    def to_internal_value(self, value):
        val = super().to_internal_value(value)
        return money(val)


class StartCartOptionsSerializer(serializers.Serializer):
    order_discount_total = MoneyField(required=False)

    def validate_order_discount_total(self, v):
        if v is not None and v < 0:
            raise serializers.ValidationError("Должна быть ≥ 0.")
        return v


class CustomCartItemCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    price = MoneyField()
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)


class ProductImageReadSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ("id", "image_url", "alt", "is_primary")

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url


# ⚠️ это serializer для CartItem (название у тебя старое, оставляю чтобы не ломать импорты)
class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    barcode = serializers.CharField(source="product.barcode", read_only=True)
    display_name = serializers.SerializerMethodField()
    primary_image_url = serializers.SerializerMethodField(read_only=True)
    images = ProductImageReadSerializer(many=True, read_only=True, source="product.images")

    class Meta:
        model = CartItem
        fields = (
            "id", "cart", "product",
            "product_name", "barcode",
            "quantity", "unit_price",
            "display_name",
            "primary_image_url",
            "images",
        )
        read_only_fields = (
            "id", "product_name", "barcode",
            "display_name", "primary_image_url", "images",
        )

    def get_display_name(self, obj):
        return _get_attr(_get_attr(obj, "product", None), "name", None) or (
            _get_attr(obj, "custom_name", "") or ""
        )

    def get_primary_image_url(self, obj):
        prod = getattr(obj, "product", None)
        if not prod:
            return None
        im = prod.images.filter(is_primary=True).first() or prod.images.first()
        if not (im and im.image):
            return None
        request = self.context.get("request")
        url = im.image.url
        return request.build_absolute_uri(url) if request else url

    def _validate_company_branch(self, cart: Cart, product: Product):
        cart_company_id = _get_attr(cart, "company_id")
        product_company_id = _get_attr(product, "company_id")
        if cart and product and cart_company_id is not None and product_company_id is not None:
            if cart_company_id != product_company_id:
                raise serializers.ValidationError(
                    {"product": "Товар принадлежит другой компании, чем корзина."}
                )

        if _has_field(type(cart), "branch") and _has_field(type(product), "branch"):
            cart_branch_id = _get_attr(cart, "branch_id")
            product_branch_id = _get_attr(product, "branch_id")
            # product.branch=None → глобальный, разрешаем
            if product_branch_id is not None and product_branch_id != cart_branch_id:
                raise serializers.ValidationError(
                    {"product": "Товар из другого филиала и не является глобальным."}
                )

    def validate(self, attrs):
        cart = attrs.get("cart") or _get_attr(self.instance, "cart", None)
        product = attrs.get("product") or _get_attr(self.instance, "product", None)

        if cart and product:
            self._validate_company_branch(cart, product)

        qty = attrs.get("quantity", _get_attr(self.instance, "quantity", 1))
        if qty is not None and qty < 1:
            raise serializers.ValidationError({"quantity": "Минимум 1."})

        return attrs

    def create(self, validated_data):
        cart = validated_data["cart"]

        if validated_data.get("product") and "unit_price" not in validated_data:
            validated_data["unit_price"] = validated_data["product"].price

        if _has_field(CartItem, "company"):
            validated_data.setdefault("company", cart.company)

        if _has_field(CartItem, "branch"):
            validated_data.setdefault("branch", _get_attr(cart, "branch", None))

        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("company", None)
        validated_data.pop("cart", None)
        validated_data.pop("branch", None)
        return super().update(instance, validated_data)


class SaleCartSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    shift = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Cart
        fields = (
            "id",
            "status",
            "shift",
            "subtotal",
            "discount_total",
            "order_discount_total",
            "tax_total",
            "total",
            "items",
        )


class ScanRequestSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)


class AddItemSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, required=False, default=1)
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
    def to_internal_value(self, data):
        if data in (None, "", "null", "None"):
            return None
        return super().to_internal_value(data)


class CheckoutSerializer(serializers.Serializer):
    print_receipt = serializers.BooleanField(default=False)
    client_id = OptionalUUIDField(required=False, allow_null=True)
    department_id = OptionalUUIDField(required=False, allow_null=True)

    # если смены нет — можно передать кассу (или автоподбор)
    cashbox_id = OptionalUUIDField(required=False, allow_null=True)

    payment_method = serializers.ChoiceField(
        choices=Sale.PaymentMethod.choices,
        default=Sale.PaymentMethod.CASH,
        required=False,
    )
    cash_received = MoneyField(required=False, allow_null=True)

    def validate(self, attrs):
        cart = self.context.get("cart")
        if not cart:
            raise serializers.ValidationError("Serializer context должен содержать cart.")

        shift_id = getattr(cart, "shift_id", None)

        # --- оплата ---
        payment_method = attrs.get("payment_method") or Sale.PaymentMethod.CASH
        cash_received = attrs.get("cash_received")

        if payment_method == Sale.PaymentMethod.CASH:
            if cash_received is None:
                raise serializers.ValidationError({"cash_received": "Укажите сумму, принятую наличными."})
            if cash_received < 0:
                raise serializers.ValidationError({"cash_received": "Не может быть отрицательной."})
        else:
            if cash_received is None:
                attrs["cash_received"] = Decimal("0.00")
            elif cash_received < 0:
                raise serializers.ValidationError({"cash_received": "Не может быть отрицательной."})

        # --- касса/смена ---
        if shift_id:
            # есть смена → касса берётся из смены, cashbox_id игнорируем
            attrs["cashbox_id"] = None
            return attrs

        cashbox_id = attrs.get("cashbox_id")

        # смены нет → автоподбор кассы (ПОСЛЕДНЯЯ)
        if not cashbox_id:
            cb = (
                Cashbox.objects
                .filter(company_id=cart.company_id, branch_id=cart.branch_id)
                .order_by("-created_at")
                .first()
                or Cashbox.objects
                .filter(company_id=cart.company_id, branch__isnull=True)
                .order_by("-created_at")
                .first()
            )
            if cb:
                attrs["cashbox_id"] = cb.id
                return attrs

            raise serializers.ValidationError({"cashbox_id": "Нет кассы для этого филиала/компании."})

        # валидация кассы
        cb = Cashbox.objects.filter(id=cashbox_id).first()
        if not cb:
            raise serializers.ValidationError({"cashbox_id": "Касса не найдена."})
        if cb.company_id != cart.company_id:
            raise serializers.ValidationError({"cashbox_id": "Касса другой компании."})
        if (cb.branch_id or None) != (cart.branch_id or None):
            raise serializers.ValidationError({"cashbox_id": "Касса другого филиала."})

        return attrs

class MobileScannerTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileScannerToken
        fields = ("token", "expires_at")
        read_only_fields = ("token", "expires_at")


class SaleListSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    change = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    first_item_name = serializers.SerializerMethodField(read_only=True)

    shift = serializers.PrimaryKeyRelatedField(read_only=True)
    cashbox = serializers.PrimaryKeyRelatedField(read_only=True)
    cashbox_name = serializers.SerializerMethodField(read_only=True)

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
            "client",
            "client_name",
            "payment_method",
            "cash_received",
            "change",
            "shift",
            "cashbox",
            "cashbox_name",
            "first_item_name",
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

    def get_first_item_name(self, obj):
        item = obj.items.first()
        if not item:
            return None
        return (item.name_snapshot or "").strip() or getattr(getattr(item, "product", None), "name", None)

    def get_cashbox_name(self, obj):
        cb = getattr(obj, "cashbox", None)
        if not cb:
            return None
        if getattr(cb, "branch", None):
            return f"Касса филиала {cb.branch.name}"
        return cb.name or "Касса компании"


class SaleItemReadSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = SaleItem
        fields = (
            "id",
            "product",
            "product_name",
            "name_snapshot",
            "barcode_snapshot",
            "unit_price",
            "quantity",
            "line_total",
        )
        read_only_fields = fields

    def get_product_name(self, obj):
        return _get_attr(_get_attr(obj, "product", None), "name", None) or obj.name_snapshot

    def get_line_total(self, obj):
        total = (obj.unit_price or Decimal("0")) * Decimal(obj.quantity or 0)
        return money(total)


class SaleDetailSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField(read_only=True)
    items = SaleItemReadSerializer(many=True, read_only=True)
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    change = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    shift = serializers.PrimaryKeyRelatedField(read_only=True)
    cashbox = serializers.PrimaryKeyRelatedField(read_only=True)
    cashbox_name = serializers.SerializerMethodField(read_only=True)

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
            "client",
            "client_name",
            "items",
            "payment_method",
            "cash_received",
            "change",
            "shift",
            "cashbox",
            "cashbox_name",
        )
        read_only_fields = fields

    def get_user_display(self, obj):
        u = obj.user
        if not u:
            return None
        return (
            getattr(u, "get_full_name", lambda: "")()
            or getattr(u, "email", None)
            or getattr(u, "username", None)
        )

    def get_cashbox_name(self, obj):
        cb = getattr(obj, "cashbox", None)
        if not cb:
            return None
        if getattr(cb, "branch", None):
            return f"Касса филиала {cb.branch.name}"
        return cb.name or "Касса компании"


class SaleStatusUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sale
        fields = ("status",)


class ReceiptItemSerializer(serializers.Serializer):
    name = serializers.CharField()
    qty = serializers.FloatField()
    price = serializers.FloatField()


class ReceiptSerializer(serializers.Serializer):
    doc_no = serializers.CharField()
    company = serializers.CharField(allow_blank=True, required=False)
    created_at = serializers.CharField(allow_null=True, required=False)
    cashier_name = serializers.CharField(allow_null=True, required=False)
    items = ReceiptItemSerializer(many=True)
    discount = serializers.FloatField(required=False, default=0.0)
    tax = serializers.FloatField(required=False, default=0.0)
    paid_cash = serializers.FloatField(required=False, default=0.0)
    paid_card = serializers.FloatField(required=False, default=0.0)
    change = serializers.FloatField(required=False, default=0.0)
