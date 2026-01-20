from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from . import models


class DocumentItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.DocumentItem
        fields = ("id", "product", "qty", "price", "discount_percent", "line_total")


class DocumentSerializer(serializers.ModelSerializer):
    items = DocumentItemSerializer(many=True)
    counterparty_display_name = serializers.CharField(
        source='counterparty.name',
        read_only=True,
        allow_null=True
    )

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
            "counterparty_display_name",
            "comment",
            "total",
            "items",
        )
        read_only_fields = ("number", "total", "status", "date")

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        
        # Валидация документа перед созданием
        doc = models.Document(**validated_data)
        try:
            doc.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
        
        doc = super().create(validated_data)
        
        # Валидация и создание items
        for it in items:
            item = models.DocumentItem(document=doc, **it)
            try:
                item.clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
            item.save()
        
        return doc

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        
        # Проверяем, что документ не проведен
        if instance.status == instance.Status.POSTED:
            raise serializers.ValidationError({"status": "Нельзя изменять проведенный документ. Сначала отмените проведение."})
        
        # Валидация документа перед обновлением
        for key, value in validated_data.items():
            setattr(instance, key, value)
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
        
        instance = super().update(instance, validated_data)
        
        if items is not None:
            # Удаляем старые items
            instance.items.all().delete()
            
            # Валидация и создание новых items
            for it in items:
                item = models.DocumentItem(document=instance, **it)
                try:
                    item.clean()
                except DjangoValidationError as e:
                    raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
                item.save()
        
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
