from rest_framework import serializers
from . import models


class DocumentItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.DocumentItem
        fields = ("id", "product", "qty", "price", "discount_percent", "line_total")


class DocumentSerializer(serializers.ModelSerializer):
    items = DocumentItemSerializer(many=True)

    class Meta:
        ref_name = "WarehouseDocumentSerializer"
        model = models.Document
        fields = (
            "id",
            "doc_type",
            "status",
            "number",
            "date",
            "warehouse_from",
            "warehouse_to",
            "counterparty",
            "comment",
            "total",
            "items",
        )
        read_only_fields = ("number", "total", "status", "date")

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        doc = super().create(validated_data)
        for it in items:
            models.DocumentItem.objects.create(document=doc, **it)
        return doc

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        instance = super().update(instance, validated_data)
        if items is not None:
            # naive replace
            instance.items.all().delete()
            for it in items:
                models.DocumentItem.objects.create(document=instance, **it)
        return instance


class ProductSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.WarehouseProduct
        fields = ("id", "name", "article", "barcode", "unit")


class WarehouseSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Warehouse
        fields = ("id", "name")


class CounterpartySerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Counterparty
        fields = ("id", "name", "type")
