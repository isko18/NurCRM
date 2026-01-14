from decimal import Decimal

from django.apps import apps
from django.db import transaction, IntegrityError
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
    В проекте встречаются разные имена:
    - WarehouseProductCharacteristics
    - WarehouseProductCharacteristicsSerializer (ошибка в слове)
    Тут подстраховываемся.
    """
    for label in (
        "warehouse.WarehouseProductCharacteristics",
        "warehouse.WarehouseProductCharacteristicsSerializer",
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

    def create(self, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)

        barcode = (validated_data.get("barcode") or "").strip()
        company = validated_data.get("company")
        warehouse = validated_data.get("warehouse")

        with transaction.atomic():
            # Если barcode есть — делаем мягкий upsert в рамках company+warehouse+barcode
            if barcode and company and warehouse:
                existing = (
                    WarehouseProduct.objects
                    .filter(company=company, warehouse=warehouse, barcode=barcode)
                    .select_for_update()
                    .first()
                )
                if existing:
                    # Обновляем нужные поля (ты сам реши список)
                    for k, v in validated_data.items():
                        setattr(existing, k, v)
                    existing.save()

                    if characteristics_data:
                        WarehouseProductCharacteristicsSerializer.objects.update_or_create(
                            product=existing,
                            defaults=characteristics_data,
                        )
                    return existing

            # Иначе обычное создание
            product = WarehouseProduct.objects.create(**validated_data)

            if characteristics_data:
                WarehouseProductCharacteristicsSerializer.objects.create(
                    product=product,
                    **characteristics_data
                )

            return product

    def update(self, instance, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)

        # запрещаем менять склад/компанию/филиал через update
        validated_data.pop("warehouse", None)
        validated_data.pop("company", None)
        validated_data.pop("branch", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if characteristics_data:
            WarehouseProductCharacteristicsSerializer.objects.update_or_create(
                product=instance,
                defaults=characteristics_data,
            )

        return instance
