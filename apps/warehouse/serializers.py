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
    characteristics = WarehouseProductCharacteristicsSerializer(required=False)
    images = WarehouseProductImageSerializer(many=True, read_only=True)
    packages = WarehouseProductPackageSerializer(many=True, read_only=True)

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
            "warehouse",
            "characteristics",
            "images",
            "packages",
        ]
        read_only_fields = ["id", "company", "branch"]

    def validate(self, attrs):
        if "barcode" in attrs:
            attrs["barcode"] = _norm_str(attrs.get("barcode"))
        if "code" in attrs:
            attrs["code"] = _norm_str(attrs.get("code"))
        if "article" in attrs:
            attrs["article"] = _norm_str(attrs.get("article"))
        if "country" in attrs:
            attrs["country"] = _norm_str(attrs.get("country"))

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
    class Meta:
        model = m.AgentRequestItem
        ref_name = "WarehouseAgentRequestItem"
        fields = ("id", "cart", "product", "quantity_requested", "created_date", "updated_date")
        read_only_fields = ("id", "created_date", "updated_date")


class AgentRequestCartSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    items = AgentRequestItemSerializer(many=True, read_only=True)
    agent_display = serializers.SerializerMethodField()
    note = serializers.CharField(required=False, allow_blank=True)

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
            "created_date",
            "updated_date",
            "items",
        )
        read_only_fields = ("id", "status", "submitted_at", "approved_at", "approved_by", "created_date", "updated_date")

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

class AgentRequestCartActionSerializer(serializers.Serializer):
    # placeholder for submit/approve/reject
    pass


class AgentStockBalanceSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_unit = serializers.CharField(source="product.unit", read_only=True)
    agent_display = serializers.SerializerMethodField()

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
            "qty",
        )
        read_only_fields = fields

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
