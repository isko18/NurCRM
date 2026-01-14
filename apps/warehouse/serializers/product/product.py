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
    - WarehouseProductCharasteristics (ошибка в слове)
    Тут подстраховываемся.
    """
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

    def validate(self, attrs):
        # нормализуем строки, чтобы не ловить "дубли" из-за пробелов
        if "barcode" in attrs:
            attrs["barcode"] = _norm_str(attrs.get("barcode"))
        if "code" in attrs:
            attrs["code"] = _norm_str(attrs.get("code"))
        if "article" in attrs:
            attrs["article"] = (attrs.get("article") or "").strip()

        # quantity в Decimal (на всякий)
        if "quantity" in attrs:
            attrs["quantity"] = _to_decimal(attrs.get("quantity"), default="0")

        return attrs

    def _upsert_characteristics(self, product, characteristics_data):
        if not characteristics_data:
            return

        CharModel = _get_characteristics_model()
        if not CharModel:
            # если модели характеристик нет/не найдена — молча пропускаем
            return

        # предполагаем OneToOne/ForeignKey на product (часто поле product)
        # если у тебя поле называется иначе — поменяй здесь.
        obj = CharModel.objects.filter(product=product).first()
        if obj:
            for k, v in characteristics_data.items():
                setattr(obj, k, v)
            obj.save()
        else:
            CharModel.objects.create(product=product, **characteristics_data)

    def create(self, validated_data):
        """
        UP SERT логика:
        - если barcode задан и уже есть товар в этом складе (company+warehouse+barcode),
          вместо create -> увеличиваем quantity и обновляем поля.
        - если barcode пустой -> создаём новую запись.
        """
        characteristics_data = validated_data.pop("characteristics", None)

        barcode = validated_data.get("barcode")
        company = validated_data.get("company")
        warehouse = validated_data.get("warehouse")

        qty_in = _to_decimal(validated_data.get("quantity"), default="0")

        # barcode пустой -> обычный create
        if not barcode:
            product = WarehouseProduct.objects.create(**validated_data)
            self._upsert_characteristics(product, characteristics_data)
            return product

        # barcode есть -> upsert в рамках company+warehouse+barcode
        with transaction.atomic():
            existing = (
                WarehouseProduct.objects
                .select_for_update()
                .filter(company=company, warehouse=warehouse, barcode=barcode)
                .first()
            )

            if existing:
                # 1) увеличиваем остаток (можешь заменить на "перезаписать", если надо)
                existing.quantity = _to_decimal(existing.quantity, "0") + qty_in

                # 2) опционально обновляем поля (чтобы скан всегда актуализировал данные)
                # если НЕ надо — просто убери этот блок
                for f in (
                    "name",
                    "article",
                    "description",
                    "unit",
                    "is_weight",
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
                    "code",
                ):
                    if f in validated_data:
                        setattr(existing, f, validated_data[f])

                existing.save()
                self._upsert_characteristics(existing, characteristics_data)
                return existing

            # если не нашли -> создаём
            try:
                product = WarehouseProduct.objects.create(**validated_data)
            except IntegrityError:
                # защита от гонки: пока создавали, кто-то успел создать
                product = (
                    WarehouseProduct.objects
                    .select_for_update()
                    .get(company=company, warehouse=warehouse, barcode=barcode)
                )
                product.quantity = _to_decimal(product.quantity, "0") + qty_in
                product.save()

            self._upsert_characteristics(product, characteristics_data)
            return product

    def update(self, instance, validated_data):
        characteristics_data = validated_data.pop("characteristics", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        self._upsert_characteristics(instance, characteristics_data)
        return instance
