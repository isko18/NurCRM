from rest_framework import serializers
from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin
from apps.warehouse.models import WarehouseProduct

from .charasteristics import WarehouseProductCharacteristicsSerializer

from .image import WarehouseProductImageSerializer

from .package import WarehouseProductPackageSerializer




# Сериализатор для list|create

class WarehouseProductSerializer(CompanyBranchReadOnlyMixin,serializers.ModelSerializer):
    
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
        read_only_fields = ["id"]

    def create(self, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)
        product = WarehouseProduct.objects.create(**validated_data)

        if characteristics_data:
            WarehouseProductCharasteristics.objects.create(
                product=product,
                **characteristics_data
            )

        return product

    def update(self, instance, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)

        # Обновляем поля продукта
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Обновляем характеристики
        if characteristics_data:
            if hasattr(instance, "characteristics"):
                for attr, value in characteristics_data.items():
                    setattr(instance.characteristics, attr, value)
                instance.characteristics.save()
            else:
                WarehouseProductCharasteristics.objects.create(
                    product=instance,
                    **characteristics_data
                )

        return instance





