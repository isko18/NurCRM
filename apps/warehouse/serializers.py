import uuid
from io import BytesIO
from decimal import Decimal
from PIL import Image
from django.db import transaction

from django.apps import apps
from django.core.files.base import ContentFile
from rest_framework import serializers

from apps.warehouse import models as m
from apps.warehouse.utils import _active_branch, _restrict_pk_queryset_strict


class CompanyBranchReadOnlyMixin:
    def _user(self):
        req = self.context.get("request")
        return getattr(req, "user", None) if req else None

    def _user_company(self):
        u = self._user()
        return getattr(u, "company", None) or getattr(u, "owned_company", None)

    def _auto_branch(self):
        return _active_branch(self)

    def create(self, validated_data):
        company = self._user_company()
        if company:
            validated_data.setdefault("company", company)
        if "branch" in getattr(self.Meta, "fields", []):
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        company = self._user_company()
        if company:
            validated_data["company"] = company
        if "branch" in getattr(self.Meta, "fields", []):
            validated_data["branch"] = self._auto_branch()
        return super().update(instance, validated_data)


# ----------------
# Simple serializers
# ----------------


class WarehouseSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')
    products_count = serializers.IntegerField(read_only=True)
    products_qty_total = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)

    class Meta:
        model = m.Warehouse
        fields = ("id", "name", "location", "status", "company", "branch", "products_count", "products_qty_total")
        ref_name = "WarehouseWarehouseSerializer"


class BrandSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=m.WarehouseProductBrand.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = m.WarehouseProductBrand
        fields = ['id', 'company', 'branch', 'name', 'parent']
        read_only_fields = ['id', 'company', 'branch']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("parent"), m.WarehouseProductBrand.objects.all(), comp, br)

    def validate(self, attrs):
        """
        Валидируем уникальность имени до INSERT, чтобы не ловить 500 IntegrityError.
        Логика соответствует constraints в модели:
          - если branch != NULL -> уникальность (branch, name)
          - если branch == NULL -> уникальность (company, name) среди branch IS NULL
        """
        attrs = super().validate(attrs)

        name = attrs.get("name")
        if name is None:
            return attrs

        name = name.strip()
        attrs["name"] = name

        company = self._user_company()
        branch = self._auto_branch()

        qs = m.WarehouseProductBrand.objects.all()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)

        if branch is not None:
            qs = qs.filter(branch=branch, name=name)
        else:
            if company is not None:
                qs = qs.filter(company=company, branch__isnull=True, name=name)
            else:
                qs = qs.filter(branch__isnull=True, name=name)

        if qs.exists():
            raise serializers.ValidationError({"name": "Бренд с таким названием уже существует."})

        return attrs


class CategorySerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=m.WarehouseProductCategory.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = m.WarehouseProductCategory
        fields = ['id', 'company', 'branch', 'name', 'parent']
        read_only_fields = ['id', 'company', 'branch']
        ref_name = "WarehouseCategory"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("parent"), m.WarehouseProductCategory.objects.all(), comp, br)

    def validate(self, attrs):
        """
        Валидируем уникальность имени до INSERT, чтобы не ловить 500 IntegrityError.
        Логика соответствует constraints в модели:
          - если branch != NULL -> уникальность (branch, name)
          - если branch == NULL -> уникальность (company, name) среди branch IS NULL
        """
        attrs = super().validate(attrs)

        name = attrs.get("name")
        if name is None:
            return attrs

        name = name.strip()
        attrs["name"] = name

        company = self._user_company()
        branch = self._auto_branch()

        qs = m.WarehouseProductCategory.objects.all()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)

        if branch is not None:
            qs = qs.filter(branch=branch, name=name)
        else:
            if company is not None:
                qs = qs.filter(company=company, branch__isnull=True, name=name)
            else:
                qs = qs.filter(branch__isnull=True, name=name)

        if qs.exists():
            raise serializers.ValidationError({"name": "Категория с таким названием уже существует."})

        return attrs


class WarehouseProductGroupSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    """Группа товаров внутри склада (иерархия как в 1С)."""
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    warehouse = serializers.PrimaryKeyRelatedField(queryset=m.Warehouse.objects.all(), required=False)
    parent = serializers.PrimaryKeyRelatedField(
        queryset=m.WarehouseProductGroup.objects.all(),
        allow_null=True,
        required=False,
    )
    products_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = m.WarehouseProductGroup
        fields = ["id", "company", "branch", "warehouse", "name", "parent", "products_count"]
        read_only_fields = ["id", "company", "branch"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warehouse = self.context.get("warehouse")
        if warehouse:
            self.fields["warehouse"].queryset = m.Warehouse.objects.filter(pk=warehouse.pk)
            qs = m.WarehouseProductGroup.objects.filter(warehouse=warehouse)
            self.fields["parent"].queryset = qs

    def create(self, validated_data):
        warehouse = self.context.get("warehouse")
        if warehouse:
            validated_data["warehouse"] = warehouse
        return super().create(validated_data)


# ----------------
# Product related
# ----------------


class WarehouseProductCharacteristicsSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    class Meta:
        model = m.WarehouseProductCharasteristics
        fields = (
            "height_cm",
            "width_cm",
            "depth_cm",
            "factual_weight_kg",
            "description",
        )


def _to_webp(uploaded_file, *, quality: int = 82) -> ContentFile:
    uploaded_file.seek(0)
    img = Image.open(uploaded_file)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    buf.seek(0)
    base_name = (getattr(uploaded_file, "name", "image") or "image").rsplit(".", 1)[0]
    return ContentFile(buf.read(), name=f"{base_name}.webp")


class WarehouseProductImageSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True, required=True)
    image_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = m.WarehouseProductImage
        fields = ("id", "product", "image", "image_url", "is_primary", "alt", "created_at")
        read_only_fields = ("id", "product", "created_at")

    def get_image_url(self, obj):
        request = self.context.get("request")
        if not getattr(obj, "image", None):
            return None
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url

    def validate_image(self, value):
        ct = getattr(value, "content_type", "") or ""
        allowed = {"image/jpeg", "image/png", "image/webp"}
        if ct and ct not in allowed:
            raise serializers.ValidationError("Разрешены только JPG, PNG, WEBP.")
        return value

    def create(self, validated_data):
        uploaded = validated_data.pop("image", None)
        if uploaded:
            validated_data["image"] = _to_webp(uploaded)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        uploaded = validated_data.pop("image", None)
        if uploaded:
            validated_data["image"] = _to_webp(uploaded)
        return super().update(instance, validated_data)


class WarehouseProductPackageSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    class Meta:
        model = m.WarehouseProductPackage
        fields = ("id", "product", "name", "quantity_in_package", "unit", "created_at")
        read_only_fields = ("id", "product", "created_at")


def _norm_str(v):
    s = (v or "").strip()
    return s or None


def _to_decimal(v, default="0"):
    try:
        return Decimal(str(v)) if v is not None else Decimal(default)
    except Exception:
        return Decimal(default)


def _warehouse_product_unit_price_after_discount(product: m.WarehouseProduct) -> Decimal:
    """Цена за единицу с учётом скидки с карточки товара (для списка агента / корзины)."""
    price = _to_decimal(getattr(product, "price", None), "0")
    pct = _to_decimal(getattr(product, "discount_percent", None), "0")
    if pct <= 0:
        return price.quantize(Decimal("0.001"))
    return (price * (Decimal("1") - pct / Decimal("100"))).quantize(Decimal("0.001"))


def _get_characteristics_model():
    for label in (
        "warehouse.WarehouseProductCharacteristics",
        "warehouse.WarehouseProductCharasteristics",
    ):
        try:
            return apps.get_model(label)
        except Exception:
            continue
    return None


class WarehouseProductSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    characteristics = WarehouseProductCharacteristicsSerializer(required=False, allow_null=True)
    images = WarehouseProductImageSerializer(many=True, read_only=True)
    packages = WarehouseProductPackageSerializer(many=True, read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warehouse = self.context.get("warehouse")
        if warehouse and "product_group" in self.fields:
            self.fields["product_group"].queryset = m.WarehouseProductGroup.objects.filter(warehouse=warehouse)

    class Meta:
        ref_name = "WarehouseProductSerializer"
        model = m.WarehouseProduct
        fields = [
            "id",
            "name",
            "article",
            "description",
            "barcode",
            "code",
            "unit",
            "is_weight",
            "quantity",
            "purchase_price",
            "markup_percent",
            "price",
            "discount_percent",
            "plu",
            "country",
            "status",
            "stock",
            "expiration_date",
            "brand",
            "category",
            "product_group",
            "warehouse",
            "characteristics",
            "images",
            "packages",
        ]
        read_only_fields = ["id", "company", "branch"]
        extra_kwargs = {
            "category": {"required": False, "allow_null": True},
            "article": {"required": False, "allow_null": True},
            "country": {"required": False, "allow_null": True},
            "characteristics": {"required": False, "allow_null": True},
        }

    def validate(self, attrs):
        if "barcode" in attrs:
            attrs["barcode"] = _norm_str(attrs.get("barcode"))
        if "code" in attrs:
            attrs["code"] = _norm_str(attrs.get("code"))
        if "article" in attrs:
            attrs["article"] = (attrs.get("article") or "").strip()
        if "country" in attrs:
            attrs["country"] = (attrs.get("country") or "").strip()

        for f in ("quantity", "purchase_price", "markup_percent", "price", "discount_percent"):
            if f in attrs:
                attrs[f] = _to_decimal(attrs.get(f), "0")

        return attrs

    def _upsert_characteristics(self, product: m.WarehouseProduct, characteristics_data):
        if not characteristics_data:
            return

        Model = _get_characteristics_model()
        if Model is None:
            return

        fk_field = "product"
        if "product" not in {f.name for f in Model._meta.get_fields()}:
            fk_field = "warehouse_product" if "warehouse_product" in {f.name for f in Model._meta.get_fields()} else "product"

        defaults = dict(characteristics_data)
        Model.objects.update_or_create(**{fk_field: product}, defaults=defaults)

    def create(self, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)

        barcode = _norm_str(validated_data.get("barcode"))
        company = validated_data.get("company")
        warehouse = validated_data.get("warehouse")

        with transaction.atomic():
            if barcode and company and warehouse:
                existing = (
                    m.WarehouseProduct.objects
                    .select_for_update()
                    .filter(company=company, warehouse=warehouse, barcode=barcode)
                    .first()
                )
                if existing:
                    validated_data.pop("company", None)
                    validated_data.pop("branch", None)
                    validated_data.pop("warehouse", None)

                    for k, v in validated_data.items():
                        setattr(existing, k, v)

                    existing.barcode = barcode
                    existing.save()

                    self._upsert_characteristics(existing, characteristics_data)
                    return existing

            product = m.WarehouseProduct.objects.create(**validated_data)
            self._upsert_characteristics(product, characteristics_data)
            return product

    def update(self, instance, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)
        validated_data.pop("warehouse", None)
        validated_data.pop("company", None)
        validated_data.pop("branch", None)

        if "barcode" in validated_data:
            validated_data["barcode"] = _norm_str(validated_data.get("barcode"))

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        self._upsert_characteristics(instance, characteristics_data)
        return instance


# ----------------
# Agent flows
# ----------------


class AgentRequestItemSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True, allow_blank=True)
    product_unit = serializers.CharField(source="product.unit", read_only=True, default="шт.")
    qty = serializers.DecimalField(
        source="quantity_requested",
        max_digits=18,
        decimal_places=3,
        read_only=True,
    )
    price = serializers.DecimalField(
        source="product.price",
        max_digits=11,
        decimal_places=3,
        read_only=True,
    )
    discount_percent = serializers.DecimalField(
        source="product.discount_percent",
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    discount_amount = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = m.AgentRequestItem
        ref_name = "WarehouseAgentRequestItem"
        fields = (
            "id",
            "cart",
            "product",
            "product_name",
            "product_article",
            "product_unit",
            "quantity_requested",
            "qty",
            "price",
            "discount_percent",
            "discount_amount",
            "line_total",
            "created_date",
            "updated_date",
        )
        read_only_fields = ("id", "created_date", "updated_date")

    def get_discount_amount(self, obj):
        price = getattr(obj.product, "price", None) or Decimal("0")
        qty = getattr(obj, "quantity_requested", None) or Decimal("0")
        pct = getattr(obj.product, "discount_percent", None) or Decimal("0")
        subtotal = price * qty
        return (subtotal * pct / Decimal("100")).quantize(Decimal("0.01"))

    def get_line_total(self, obj):
        price = getattr(obj.product, "price", None) or Decimal("0")
        qty = getattr(obj, "quantity_requested", None) or Decimal("0")
        pct = getattr(obj.product, "discount_percent", None) or Decimal("0")
        subtotal = price * qty
        discount = subtotal * pct / Decimal("100")
        return (subtotal - discount).quantize(Decimal("0.01"))


class AgentRequestCartSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    items = AgentRequestItemSerializer(many=True, read_only=True)
    agent_display = serializers.SerializerMethodField()
    note = serializers.CharField(required=False, allow_blank=True)
    sale_document_number = serializers.CharField(source="sale_document.number", read_only=True, allow_null=True)

    class Meta:
        model = m.AgentRequestCart
        ref_name = "WarehouseAgentRequestCart"
        fields = (
            "id",
            "agent",
            "agent_display",
            "warehouse",
            "status",
            "note",
            "submitted_at",
            "approved_at",
            "approved_by",
            "sale_document",
            "sale_document_number",
            "created_date",
            "updated_date",
            "items",
        )
        read_only_fields = (
            "id",
            "status",
            "submitted_at",
            "approved_at",
            "approved_by",
            "sale_document",
            "sale_document_number",
            "created_date",
            "updated_date",
        )
        extra_kwargs = {
            "agent": {"required": False},
        }

    def get_agent_display(self, obj):
        agent = getattr(obj, "agent", None)
        if not agent:
            return None
        full_name = ""
        if hasattr(agent, "get_full_name"):
            try:
                full_name = agent.get_full_name() or ""
            except Exception:
                full_name = ""
        if not full_name:
            first = getattr(agent, "first_name", "") or ""
            last = getattr(agent, "last_name", "") or ""
            full_name = f"{first} {last}".strip()
        return full_name or getattr(agent, "username", None) or getattr(agent, "email", None) or str(getattr(agent, "id", ""))


# ----------------
# Company / Agent membership (заявки агентов в компании)
# ----------------


class CompanyWarehouseAgentSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    user_display = serializers.SerializerMethodField()
    decided_by_display = serializers.SerializerMethodField()
    common_access_enabled = serializers.BooleanField(read_only=True)
    common_warehouse = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = m.CompanyWarehouseAgent
        fields = (
            "id",
            "company",
            "company_name",
            "user",
            "user_display",
            "status",
            "note",
            "common_access_enabled",
            "common_warehouse",
            "created_at",
            "updated_at",
            "decided_at",
            "decided_by",
            "decided_by_display",
        )
        read_only_fields = (
            "id",
            "status",
            "common_access_enabled",
            "common_warehouse",
            "created_at",
            "updated_at",
            "decided_at",
            "decided_by",
        )
        extra_kwargs = {"company": {"required": True}, "user": {"required": False}, "note": {"required": False, "allow_blank": True}}

    def get_user_display(self, obj):
        u = getattr(obj, "user", None)
        if not u:
            return None
        if hasattr(u, "get_full_name") and u.get_full_name():
            return u.get_full_name()
        return (
            f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
            or getattr(u, "email", "")
            or str(u.id)
        )

    def get_decided_by_display(self, obj):
        u = getattr(obj, "decided_by", None)
        if not u:
            return None
        return getattr(u, "email", None) or str(u.id)


class CompanyWarehouseAgentCommonAccessUpdateSerializer(serializers.ModelSerializer):
    common_access_enabled = serializers.BooleanField(required=True)
    common_warehouse = serializers.PrimaryKeyRelatedField(
        queryset=m.Warehouse.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = m.CompanyWarehouseAgent
        fields = ("common_access_enabled", "common_warehouse")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        enabled = attrs.get("common_access_enabled")
        warehouse = attrs.get("common_warehouse")

        if enabled and warehouse is None:
            raise serializers.ValidationError({"common_warehouse": "Укажите склад, если включен общий доступ."})
        if not enabled:
            attrs["common_warehouse"] = None

        inst = getattr(self, "instance", None)
        company = getattr(inst, "company", None)
        if enabled and warehouse is not None and company is not None:
            if getattr(warehouse, "company_id", None) != getattr(company, "id", None):
                raise serializers.ValidationError({"common_warehouse": "Склад принадлежит другой компании."})

        return attrs


class AgentRequestCartActionSerializer(serializers.Serializer):
    # placeholder for submit/approve/reject
    pass


class AgentRequestCartCreateSaleSerializer(serializers.Serializer):
    """
    Создание документа SALE по позициям заявки агента (AgentRequestCart).
    Делает документ, привязанный к agent, чтобы аналитика считала продажи агента.
    """

    counterparty = serializers.PrimaryKeyRelatedField(queryset=m.Counterparty.objects.all())
    post = serializers.BooleanField(required=False, default=False)
    payment_kind = serializers.ChoiceField(choices=m.Document.PaymentKind.choices, required=False)
    prepayment_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0.00"))
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0.00"))
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0.00"))
    comment = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class AgentStockBalanceSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_unit = serializers.CharField(source="product.unit", read_only=True)
    product_price = serializers.DecimalField(source="product.price", max_digits=18, decimal_places=3, read_only=True)
    product_discount_percent = serializers.DecimalField(
        source="product.discount_percent", max_digits=12, decimal_places=2, read_only=True
    )
    product_price_after_discount = serializers.SerializerMethodField()
    product_group = serializers.SerializerMethodField()
    product_group_name = serializers.SerializerMethodField()
    product_category = serializers.SerializerMethodField()
    product_category_name = serializers.SerializerMethodField()
    agent_display = serializers.SerializerMethodField()
    last_movement_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = m.AgentStockBalance
        fields = (
            "id",
            "agent",
            "agent_display",
            "warehouse",
            "product",
            "product_name",
            "product_article",
            "product_unit",
            "product_price",
            "product_discount_percent",
            "product_price_after_discount",
            "product_group",
            "product_group_name",
            "product_category",
            "product_category_name",
            "qty",
            "last_movement_at",
        )
        read_only_fields = fields

    def get_product_price_after_discount(self, obj):
        p = getattr(obj, "product", None)
        if not p:
            return Decimal("0.000")
        return _warehouse_product_unit_price_after_discount(p)

    def _get_product_group(self, product):
        if not product:
            return None, None
        group = getattr(product, "product_group", None)
        if group is None:
            return None, None
        return str(group.id), getattr(group, "name", "") or ""

    def _get_product_category(self, product):
        if not product:
            return None, None
        cat = getattr(product, "category", None)
        if cat is None:
            return None, None
        return str(cat.id), getattr(cat, "name", "") or ""

    def get_product_group(self, obj):
        return self._get_product_group(getattr(obj, "product", None))[0]

    def get_product_group_name(self, obj):
        return self._get_product_group(getattr(obj, "product", None))[1]

    def get_product_category(self, obj):
        return self._get_product_category(getattr(obj, "product", None))[0]

    def get_product_category_name(self, obj):
        return self._get_product_category(getattr(obj, "product", None))[1]

    def get_agent_display(self, obj):
        agent = getattr(obj, "agent", None)
        if not agent:
            return None
        full_name = ""
        if hasattr(agent, "get_full_name"):
            try:
                full_name = agent.get_full_name() or ""
            except Exception:
                full_name = ""
        if not full_name:
            first = getattr(agent, "first_name", "") or ""
            last = getattr(agent, "last_name", "") or ""
            full_name = f"{first} {last}".strip()
        return full_name or getattr(agent, "username", None) or getattr(agent, "email", None) or str(getattr(agent, "id", ""))


class CommonWarehouseBalanceSerializer(serializers.Serializer):
    """
    Ответ совместим с форматом /agents/me/products, но qty берётся из общего остатка склада
    (WarehouseProduct.quantity), а id генерируется детерминированно.
    """

    id = serializers.UUIDField()
    agent = serializers.UUIDField()
    warehouse = serializers.UUIDField()
    product = serializers.UUIDField()
    product_name = serializers.CharField()
    product_article = serializers.CharField(allow_null=True, required=False)
    product_unit = serializers.CharField()
    product_price = serializers.DecimalField(max_digits=18, decimal_places=3)
    product_discount_percent = serializers.DecimalField(max_digits=12, decimal_places=2)
    product_price_after_discount = serializers.DecimalField(max_digits=18, decimal_places=3)
    product_group = serializers.UUIDField(allow_null=True, required=False)
    product_group_name = serializers.CharField(allow_null=True, required=False)
    product_category = serializers.UUIDField(allow_null=True, required=False)
    product_category_name = serializers.CharField(allow_null=True, required=False)
    qty = serializers.DecimalField(max_digits=18, decimal_places=3)
    created_date = serializers.DateTimeField(allow_null=True, required=False)
    updated_date = serializers.DateTimeField(allow_null=True, required=False)

    @staticmethod
    def make_row(*, agent_id, warehouse_id, product: m.WarehouseProduct):
        sid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"nurcrm:common_stock:{warehouse_id}:{product.id}",
        )
        product_group = getattr(product, "product_group", None)
        category = getattr(product, "category", None)
        return {
            "id": sid,
            "agent": agent_id,
            "warehouse": warehouse_id,
            "product": product.id,
            "product_name": product.name,
            "product_article": getattr(product, "article", None),
            "product_unit": getattr(product, "unit", "") or "",
            "product_price": getattr(product, "price", None) or Decimal("0.000"),
            "product_discount_percent": getattr(product, "discount_percent", None) or Decimal("0.00"),
            "product_price_after_discount": _warehouse_product_unit_price_after_discount(product),
            "product_group": product_group.id if product_group else None,
            "product_group_name": (getattr(product_group, "name", "") or "") if product_group else None,
            "product_category": category.id if category else None,
            "product_category_name": (getattr(category, "name", "") or "") if category else None,
            "qty": getattr(product, "quantity", None) or Decimal("0.000"),
            "created_date": getattr(product, "created_date", None),
            "updated_date": getattr(product, "updated_date", None),
        }
