from decimal import Decimal
from django.apps import apps
from django.db import transaction
from rest_framework import serializers

from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin
from apps.warehouse.models import WarehouseProduct

from .charasteristics import WarehouseProductCharacteristicsSerializer
from .image import WarehouseProductImageSerializer
from .package import WarehouseProductPackageSerializer


def _norm_str(v):
    s = (v or "").strip()
    return s or None


def _to_decimal(v, default="0"):
    try:
        return Decimal(str(v)) if v is not None else Decimal(default)
    except Exception:
        return Decimal(default)


def _get_characteristics_model():
    """
    Подстраховка: в проектах бывает разное имя модели характеристик.
    Укажи реальные варианты имён моделей (без Serializer).
    """
    for label in (
        "warehouse.WarehouseProductCharacteristics",
        "warehouse.WarehouseProductCharasteristics",  # если у тебя опечатка в названии модели
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
        model = WarehouseProduct
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
        # нормализация
        if "barcode" in attrs:
            attrs["barcode"] = _norm_str(attrs.get("barcode"))
        if "code" in attrs:
            attrs["code"] = _norm_str(attrs.get("code"))
        if "article" in attrs:
            attrs["article"] = _norm_str(attrs.get("article"))
        if "country" in attrs:
            attrs["country"] = _norm_str(attrs.get("country"))

        # decimal нормализация (если приходит строкой)
        for f in ("quantity", "purchase_price", "markup_percent", "price", "discount_percent"):
            if f in attrs:
                attrs[f] = _to_decimal(attrs.get(f), "0")

        return attrs

    def _upsert_characteristics(self, product: WarehouseProduct, characteristics_data):
        if not characteristics_data:
            return

        Model = _get_characteristics_model()
        if Model is None:
            # Модель не найдена — не падаем, но и не создаём мусор
            return

        # Если в Model FK называется не product, а product_id — подстройка:
        fk_field = "product"
        if "product" not in {f.name for f in Model._meta.get_fields()}:
            # попробуем самое частое альтернативное
            fk_field = "warehouse_product" if "warehouse_product" in {f.name for f in Model._meta.get_fields()} else "product"

        defaults = dict(characteristics_data)
        Model.objects.update_or_create(**{fk_field: product}, defaults=defaults)

    def create(self, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)

        barcode = _norm_str(validated_data.get("barcode"))
        company = validated_data.get("company")
        warehouse = validated_data.get("warehouse")

        # ВАЖНО: company/warehouse должны быть проставлены во View через serializer.save(...)
        # иначе company будет None и всё снова упадёт на NOT NULL
        with transaction.atomic():
            if barcode and company and warehouse:
                existing = (
                    WarehouseProduct.objects
                    .select_for_update()
                    .filter(company=company, warehouse=warehouse, barcode=barcode)
                    .first()
                )
                if existing:
                    # запрещаем менять связки
                    validated_data.pop("company", None)
                    validated_data.pop("branch", None)
                    validated_data.pop("warehouse", None)

                    for k, v in validated_data.items():
                        setattr(existing, k, v)

                    # гарантируем, что barcode хранится нормализованным
                    existing.barcode = barcode
                    existing.save()

                    self._upsert_characteristics(existing, characteristics_data)
                    return existing

            product = WarehouseProduct.objects.create(**validated_data)
            self._upsert_characteristics(product, characteristics_data)
            return product

    def update(self, instance, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)

        # запрещаем менять склад/компанию/филиал через update
        validated_data.pop("warehouse", None)
        validated_data.pop("company", None)
        validated_data.pop("branch", None)

        # нормализуем barcode если прилетает
        if "barcode" in validated_data:
            validated_data["barcode"] = _norm_str(validated_data.get("barcode"))

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        self._upsert_characteristics(instance, characteristics_data)
        return instance
