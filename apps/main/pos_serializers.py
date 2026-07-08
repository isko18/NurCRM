from decimal import Decimal
from rest_framework import serializers

from apps.construction.models import Cashbox, CashShift
from .models import (
    Product,
    Cart,
    CartItem,
    CartItemDeletionLog,
    Sale,
    SaleItem,
    MobileScannerToken,
    ProductImage,
)
from .pos_utils import (
    money, qty3, has_field, get_attr, Q2, Q3
)


class MoneyField(serializers.DecimalField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(*args, **kwargs)

    def to_internal_value(self, value):
        val = super().to_internal_value(value)
        return money(val)


class QtyField(serializers.DecimalField):
    """
    Кол-во для POS:
    - для штучного товара будет 1,2,3...
    - для весового будет 0.312, 1.245 ...
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 12)
        kwargs.setdefault("decimal_places", 3)
        kwargs.setdefault("required", False)
        kwargs.setdefault("default", Decimal("1.000"))
        super().__init__(*args, **kwargs)

    def to_internal_value(self, value):
        val = super().to_internal_value(value)
        val = qty3(val)
        if val <= 0:
            raise serializers.ValidationError("Количество должно быть > 0.")
        return val


class StartCartOptionsSerializer(serializers.Serializer):
    """
    Настройки корзины перед продажей:
    - order_discount_total — фиксированная скидка на чек (сумма)
    - order_discount_percent — скидка на чек в процентах (0–100)
    - is_new — создать новую open-корзину (кнопка «Новая»)
    - sale_id — активировать указанную open-корзину (переключение вкладки)

    Используется только ОДИН вариант: либо сумма, либо процент.
    """

    order_discount_total = MoneyField(required=False)
    order_discount_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False
    )
    is_wholesale = serializers.BooleanField(required=False)
    is_new = serializers.BooleanField(required=False, default=False)
    sale_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        total = attrs.get("order_discount_total")
        percent = attrs.get("order_discount_percent")

        if total is not None and total < 0:
            raise serializers.ValidationError({"order_discount_total": "Должна быть ≥ 0."})
        if percent is not None and percent < 0:
            raise serializers.ValidationError({"order_discount_percent": "Должна быть ≥ 0."})
        if percent is not None and percent > 100:
            raise serializers.ValidationError({"order_discount_percent": "Не больше 100%."})
        if total is not None and percent is not None:
            raise serializers.ValidationError(
                "Выберите либо фиксированную скидку (order_discount_total), либо скидку в процентах (order_discount_percent)."
            )

        return attrs


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
    is_weight = serializers.BooleanField(source="product.is_weight", read_only=True)
    # Product.stock в карточке товара — «Акционный товар» (как в ProductSerializer)
    stock = serializers.SerializerMethodField()
    promotion_rules = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    primary_image_url = serializers.SerializerMethodField(read_only=True)
    # Полный массив images[] в POS не нужен: на строке корзины показывается одна
    # миниатюра (primary_image_url). Массив раздувал payload и CPU сериализации.
    # line_discount — поле модели (хранится отдельно от unit_price)

    # ✅ важно: quantity должен быть Decimal(3), а не int
    quantity = QtyField()

    sale_package = serializers.UUIDField(source="sale_package_id", read_only=True, allow_null=True)

    class Meta:
        model = CartItem
        fields = (
            "id", "cart", "product",
            "product_name", "barcode",
            "is_weight",
            "stock", "promotion_rules",
            "quantity", "unit_price", "line_discount", "line_total",
            "sale_package",
            "display_name",
            "primary_image_url",
        )
        read_only_fields = (
            "id", "product_name", "barcode",
            "stock", "promotion_rules", "line_total",
            "display_name", "primary_image_url",
            "sale_package",
        )

    def get_stock(self, obj):
        p = getattr(obj, "product", None)
        return bool(getattr(p, "stock", False))

    def get_display_name(self, obj):
        return get_attr(get_attr(obj, "product", None), "name", None) or (
            get_attr(obj, "custom_name", "") or ""
        )

    def get_line_total(self, obj):
        base = Decimal(str(obj.unit_price or 0)) * Decimal(str(obj.quantity or 0))
        disc = Decimal(str(getattr(obj, "line_discount", None) or 0))
        return money(base - disc)

    def get_promotion_rules(self, obj):
        p = getattr(obj, "product", None)
        if not p or not getattr(p, "stock", False):
            return []
        cache = getattr(p, "_prefetched_objects_cache", {})
        tiers = cache.get("promotion_tiers")
        if tiers is not None:
            rows = list(tiers)
        else:
            rows = list(p.promotion_tiers.all())
        out = []
        for t in rows:
            out.append(
                {
                    "id": str(t.id),
                    "position": t.position,
                    "min_amount": str(t.min_amount),
                    "discount_percent": str(t.discount_percent),
                    "promo_quantity": t.promo_quantity,
                }
            )
        return out

    def _get_product_images(self, product):
        if not product:
            return []
        prefetched = getattr(product, "_prefetched_objects_cache", {})
        if "images" in prefetched:
            return list(prefetched["images"])
        return list(product.images.all())

    def get_primary_image_url(self, obj):
        prod = getattr(obj, "product", None)
        if not prod:
            return None
        images = self._get_product_images(prod)
        im = next((image for image in images if getattr(image, "is_primary", False)), None)
        if im is None and images:
            im = images[0]
        if not (im and im.image):
            return None
        request = self.context.get("request")
        url = im.image.url
        return request.build_absolute_uri(url) if request else url

    def _validate_company_branch(self, cart: Cart, product: Product):
        cart_company_id = get_attr(cart, "company_id")
        product_company_id = get_attr(product, "company_id")
        if cart and product and cart_company_id is not None and product_company_id is not None:
            if cart_company_id != product_company_id:
                raise serializers.ValidationError({"product": "Товар принадлежит другой компании, чем корзина."})

        if has_field(type(cart), "branch") and has_field(type(product), "branch"):
            cart_branch_id = get_attr(cart, "branch_id")
            product_branch_id = get_attr(product, "branch_id")
            if product_branch_id is not None and product_branch_id != cart_branch_id:
                raise serializers.ValidationError({"product": "Товар из другого филиала и не является глобальным."})

    def validate(self, attrs):
        cart = attrs.get("cart") or get_attr(self.instance, "cart", None)
        product = attrs.get("product") or get_attr(self.instance, "product", None)

        if cart and product:
            self._validate_company_branch(cart, product)

        qty = attrs.get("quantity", get_attr(self.instance, "quantity", Decimal("1.000")))
        qty = qty3(Decimal(str(qty)))
        if qty <= 0:
            raise serializers.ValidationError({"quantity": "Количество должно быть > 0."})

        attrs["quantity"] = qty
        return attrs

    def create(self, validated_data):
        cart = validated_data["cart"]

        if validated_data.get("product") and "unit_price" not in validated_data:
            validated_data["unit_price"] = validated_data["product"].price

        if has_field(CartItem, "company"):
            validated_data.setdefault("company", cart.company)

        if has_field(CartItem, "branch"):
            validated_data.setdefault("branch", get_attr(cart, "branch", None))

        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("company", None)
        validated_data.pop("cart", None)
        validated_data.pop("branch", None)
        return super().update(instance, validated_data)



class SaleCartSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    shift = serializers.PrimaryKeyRelatedField(read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = (
            "id",
            "status",
            "is_default",
            "is_wholesale",
            "shift",
            "subtotal",
            "discount_total",
            "order_discount_total",
            "order_discount_percent",
            "tax_total",
            "total",
            "items",
        )

    def get_status(self, obj):
        if obj.status == Cart.Status.ACTIVE:
            return "open"
        return obj.status


class ScanRequestSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64)
    # ✅ было IntegerField → стало Decimal 3 знака
    quantity = QtyField(required=False, default=Decimal("1.000"))
    sale_id = serializers.UUIDField(required=False, allow_null=True)

class AddItemSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    # ✅ было IntegerField → стало Decimal 3 знака
    quantity = QtyField(required=False, default=Decimal("1.000"))
    unit_price = MoneyField(required=False)
    discount_total = MoneyField(required=False)
    # Продажа поштучно из пачки: id упаковки ProductPackage (quantity_in_package = шт в пачке)
    sale_package_id = serializers.UUIDField(required=False, allow_null=True)
    # Разрешить продажу "в минус" (игнорировать проверку остатка).
    # Будет применено только для owner/admin (см. pos_views).
    allow_minus = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        up = attrs.get("unit_price")
        disc = attrs.get("discount_total")
        qty = attrs.get("quantity", Decimal("1.000"))

        if up is not None and up < 0:
            raise serializers.ValidationError({"unit_price": "Должна быть ≥ 0."})
        if disc is not None and disc < 0:
            raise serializers.ValidationError({"discount_total": "Должна быть ≥ 0."})

        qty = qty3(Decimal(str(qty)))
        if qty <= 0:
            raise serializers.ValidationError({"quantity": "Количество должно быть > 0."})

        attrs["quantity"] = qty
        return attrs


class CartItemPatchSerializer(serializers.Serializer):
    """PATCH позиции корзины: количество (0 = удалить), цена или скидка на позицию."""
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, required=False, min_value=Decimal("0"),
    )
    unit_price = MoneyField(required=False)
    discount_total = MoneyField(required=False)

    def validate(self, attrs):
        up = attrs.get("unit_price")
        disc = attrs.get("discount_total")
        if up is not None and up < 0:
            raise serializers.ValidationError({"unit_price": "Должна быть ≥ 0."})
        if disc is not None and disc < 0:
            raise serializers.ValidationError({"discount_total": "Должна быть ≥ 0."})
        return attrs


class OptionalUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        if data in (None, "", "null", "None"):
            return None
        return super().to_internal_value(data)


# ⚠️ ВАЖНО:
# - Эта функция у тебя уже есть в construction serializers.
# - Если в этом файле её нет — импортни или вставь такую же.
def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "owned_company", None):
        return True
    if getattr(user, "is_admin", False):
        return True
    role = getattr(user, "role", None)
    if role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор"):
        return True
    return False


class CheckoutPaymentLineSerializer(serializers.Serializer):
    method = serializers.ChoiceField(
        choices=[c for c in Sale.PaymentMethod.choices if c[0] not in (Sale.PaymentMethod.DEBT, Sale.PaymentMethod.MIXED)],
    )
    amount = MoneyField()


class CheckoutSerializer(serializers.Serializer):
    print_receipt = serializers.BooleanField(default=False)
    client_id = OptionalUUIDField(required=False, allow_null=True)
    department_id = OptionalUUIDField(required=False, allow_null=True)
    # Разрешить списание "в минус" при закрытии чека (только owner/admin).
    allow_minus = serializers.BooleanField(required=False, default=False)

    # если смены нет — можно передать кассу (или автоподбор)
    cashbox_id = OptionalUUIDField(required=False, allow_null=True)

    # ✅ НОВОЕ: можно явно указать смену (нужно, когда 2 смены на 1 кассу)
    shift_id = OptionalUUIDField(required=False, allow_null=True)

    payment_method = serializers.ChoiceField(
        choices=Sale.PaymentMethod.choices,
        required=False,
        allow_null=True,
    )
    cash_received = MoneyField(required=False, allow_null=True)
    payments = CheckoutPaymentLineSerializer(many=True, required=False)

    def _resolve_cashbox(self, cart: Cart, cashbox_id):
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
                return cb
            raise serializers.ValidationError({"cashbox_id": "Нет кассы для этого филиала/компании."})

        cb = Cashbox.objects.filter(id=cashbox_id).first()
        if not cb:
            raise serializers.ValidationError({"cashbox_id": "Касса не найдена."})
        if cb.company_id != cart.company_id:
            raise serializers.ValidationError({"cashbox_id": "Касса другой компании."})
        if (cb.branch_id or None) != (cart.branch_id or None):
            raise serializers.ValidationError({"cashbox_id": "Касса другого филиала."})

        return cb

    def _resolve_shift(self, cart: Cart, cashbox: Cashbox, user, shift_id):
        # 1) если shift_id явно передали — валидируем
        if shift_id:
            sh = CashShift.objects.select_related("cashbox", "cashier").filter(id=shift_id).first()
            if not sh:
                raise serializers.ValidationError({"shift_id": "Смена не найдена."})
            if sh.company_id != cart.company_id:
                raise serializers.ValidationError({"shift_id": "Смена другой компании."})
            if (sh.branch_id or None) != (cart.branch_id or None):
                raise serializers.ValidationError({"shift_id": "Смена другого филиала."})
            if sh.cashbox_id != cashbox.id:
                raise serializers.ValidationError({"shift_id": "Смена относится к другой кассе."})
            if sh.status != CashShift.Status.OPEN:
                raise serializers.ValidationError({"shift_id": "Смена закрыта. Нужна открытая смена."})

            return sh

        # 2) shift_id не передали → берём любую открытую смену компании в этой кассе
        if not user or not getattr(user, "is_authenticated", False):
            raise serializers.ValidationError({"shift_id": "Нужен пользователь для определения смены."})

        sh = (
            CashShift.objects
            .filter(
                company_id=cart.company_id,
                branch_id=cart.branch_id,
                cashbox=cashbox,
                status=CashShift.Status.OPEN,
            )
            .order_by("-opened_at")
            .first()
        )
        if sh:
            return sh

        raise serializers.ValidationError(
            {"shift_id": "У вас нет открытой смены в этой кассе. Откройте смену перед продажей."}
        )

    def validate(self, attrs):
        cart = self.context.get("cart")
        if not cart:
            raise serializers.ValidationError("Serializer context должен содержать cart.")

        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        payments = attrs.get("payments") or []
        cart.recalc()
        sale_total = (cart.total or Decimal("0.00")).quantize(Decimal("0.01"))
        raw = getattr(self, "initial_data", None) or {}
        payment_method_in_request = "payment_method" in raw and raw.get("payment_method") not in (None, "", "null")

        if payments:
            if payment_method_in_request:
                raise serializers.ValidationError(
                    "Передайте либо payments[], либо payment_method, но не оба варианта."
                )
            if len(payments) < 1:
                raise serializers.ValidationError({"payments": "Нужна хотя бы одна строка оплаты."})
            paid_total = sum((p["amount"] for p in payments), Decimal("0.00")).quantize(Decimal("0.01"))
            if paid_total != sale_total:
                raise serializers.ValidationError(
                    {"payments": f"Сумма оплат ({paid_total}) должна равняться сумме чека ({sale_total})."}
                )
            cash_portion = sum(
                (p["amount"] for p in payments if p["method"] == Sale.PaymentMethod.CASH),
                Decimal("0.00"),
            )
            cash_received = attrs.get("cash_received")
            if cash_portion > 0:
                if cash_received is None:
                    attrs["cash_received"] = cash_portion
                elif cash_received < cash_portion:
                    raise serializers.ValidationError(
                        {"cash_received": "Сумма, полученная наличными, меньше наличной части оплаты."}
                    )
            else:
                attrs["cash_received"] = Decimal("0.00")
        else:
            # --- оплата одним способом ---
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
        if getattr(cart, "shift_id", None):
            # уже привязано на старте продажи — не трогаем
            attrs["cashbox_id"] = None
            attrs["shift_id"] = None
            return attrs

        cb = self._resolve_cashbox(cart, attrs.get("cashbox_id"))
        sh = self._resolve_shift(cart, cb, user, attrs.get("shift_id"))

        attrs["cashbox_id"] = cb.id
        attrs["shift_id"] = sh.id
        return attrs


class PayDebtSerializer(serializers.Serializer):
    """
    Оплата ранее оформленной продажи "в долг" (Sale.status=DEBT).
    Делает продажу PAID и тем самым она начинает учитываться в кассе/сменах/аналитике.
    """

    payment_method = serializers.ChoiceField(
        choices=[c for c in Sale.PaymentMethod.choices if c[0] != Sale.PaymentMethod.DEBT],
        required=True,
    )
    cash_received = MoneyField(required=False, allow_null=True)

    def validate(self, attrs):
        sale = self.context.get("sale")
        if sale is None:
            raise serializers.ValidationError("Serializer context должен содержать sale.")

        pm = attrs.get("payment_method")
        cash_received = attrs.get("cash_received")

        if pm == Sale.PaymentMethod.CASH:
            if cash_received is None:
                raise serializers.ValidationError({"cash_received": "Укажите сумму, принятую наличными."})
            if cash_received < 0:
                raise serializers.ValidationError({"cash_received": "Не может быть отрицательной."})
            if cash_received < (sale.total or Decimal("0.00")):
                raise serializers.ValidationError({"cash_received": "Сумма, полученная наличными, меньше суммы долга."})
        else:
            # Для безналичных методов наличных нет
            attrs["cash_received"] = Decimal("0.00")

        return attrs


class MobileScannerTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileScannerToken
        fields = ("token", "expires_at")
        read_only_fields = ("token", "expires_at")


class CartItemDeletionLogSerializer(serializers.ModelSerializer):
    """Журнал удалений позиций из корзины: товар, количество, кто, время."""

    deleted_by_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CartItemDeletionLog
        fields = (
            "id",
            "cart_id",
            "product",
            "product_name",
            "quantity",
            "deleted_by",
            "deleted_by_display",
            "created_at",
        )
        read_only_fields = fields

    def get_deleted_by_display(self, obj):
        u = obj.deleted_by
        if not u:
            return None
        name = getattr(u, "get_full_name", lambda: "")() or ""
        if name.strip():
            return name.strip()
        return getattr(u, "email", None) or str(getattr(u, "pk", ""))


class SaleListSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField()
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    change = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    first_item_name = serializers.SerializerMethodField(read_only=True)

    shift = serializers.PrimaryKeyRelatedField(read_only=True)
    cashbox = serializers.PrimaryKeyRelatedField(read_only=True)
    cashbox_name = serializers.SerializerMethodField(read_only=True)
    debt_amount = serializers.SerializerMethodField(read_only=True)

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
            "debt_amount",
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

    def get_debt_amount(self, obj):
        is_debt = (
            obj.status == Sale.Status.DEBT
            or obj.payment_method == Sale.PaymentMethod.DEBT
        )
        if not is_debt:
            return money(Decimal("0.00"))
        remaining = (obj.total or Decimal("0.00")) - (obj.cash_received or Decimal("0.00"))
        return money(remaining if remaining > Decimal("0.00") else Decimal("0.00"))


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
            "line_discount",
            "quantity",
            "line_total",
        )
        read_only_fields = fields

    def get_product_name(self, obj):
        return get_attr(get_attr(obj, "product", None), "name", None) or obj.name_snapshot

    def get_line_total(self, obj):
        base = (obj.unit_price or Decimal("0")) * Decimal(obj.quantity or 0)
        disc = Decimal(str(getattr(obj, "line_discount", None) or 0))
        return money(base - disc)


class SalePaymentReadSerializer(serializers.Serializer):
    method = serializers.CharField()
    method_display = serializers.CharField()
    amount = serializers.CharField()


class SaleDetailSerializer(serializers.ModelSerializer):
    user_display = serializers.SerializerMethodField(read_only=True)
    items = SaleItemReadSerializer(many=True, read_only=True)
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    change = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    payments = serializers.SerializerMethodField(read_only=True)

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
            "payments",
            "shift",
            "cashbox",
            "cashbox_name",
            "ekassa_fiscal",
        )
        read_only_fields = fields

    def get_payments(self, obj):
        from apps.main.pos_utils import fmt_money

        lines = obj.payment_lines()
        out = []
        for line in lines:
            try:
                method_display = line.get_method_display()
            except Exception:
                method_display = line.method
            out.append(
                {
                    "method": line.method,
                    "method_display": method_display,
                    "amount": fmt_money(line.amount),
                }
            )
        return out

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
    line_discount = serializers.FloatField(required=False, default=0.0)
    line_total = serializers.FloatField(required=False, default=0.0)


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


class AgentCheckoutSerializer(serializers.Serializer):
    print_receipt = serializers.BooleanField(default=False)
    client_id = OptionalUUIDField(required=False, allow_null=True)
    # алиас для клиентов, которые шлют `client` вместо `client_id`
    client = OptionalUUIDField(required=False, allow_null=True, write_only=True)
    # Разрешить "в минус" при оформлении агентской продажи (только owner/admin).
    allow_minus = serializers.BooleanField(required=False, default=False)

    payment_method = serializers.ChoiceField(
        choices=Sale.PaymentMethod.choices,
        default=Sale.PaymentMethod.CASH,
        required=False,
    )
    cash_received = MoneyField(required=False, allow_null=True)

    # если хочешь сохранять кассу в продаже — оставь
    cashbox_id = OptionalUUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs.get("client_id") is None and attrs.get("client") is not None:
            attrs["client_id"] = attrs.pop("client")
        elif "client" in attrs:
            attrs.pop("client", None)

        pm = attrs.get("payment_method") or Sale.PaymentMethod.CASH
        cr = attrs.get("cash_received")

        if pm == Sale.PaymentMethod.CASH:
            if cr is None:
                raise serializers.ValidationError({"cash_received": "Укажите сумму, принятую наличными."})
            if cr < 0:
                raise serializers.ValidationError({"cash_received": "Не может быть отрицательной."})
        else:
            if cr is None:
                attrs["cash_received"] = Decimal("0.00")
            elif cr < 0:
                raise serializers.ValidationError({"cash_received": "Не может быть отрицательной."})

        return attrs
