from decimal import Decimal
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from . import models


class DocumentItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
    product_article = serializers.CharField(source="product.article", read_only=True, allow_null=True)

    class Meta:
        model = models.DocumentItem
        fields = (
            "id",
            "product",
            "product_name",
            "product_article",
            "qty",
            "price",
            "discount_percent",
            "discount_amount",
            "line_total",
        )


class DocumentSerializer(serializers.ModelSerializer):
    items = DocumentItemSerializer(many=True)
    counterparty_display_name = serializers.CharField(
        source="counterparty.name", read_only=True, allow_null=True
    )
    warehouse_from_name = serializers.CharField(
        source="warehouse_from.name", read_only=True, allow_null=True
    )
    warehouse_to_name = serializers.CharField(
        source="warehouse_to.name", read_only=True, allow_null=True
    )
    agent = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        ref_name = "WarehouseDocumentSerializer"
        model = models.Document
        fields = (
            "id",
            "doc_type",
            "status",
            "number",
            "date",
            "payment_kind",
            "warehouse_from",
            "warehouse_to",
            "warehouse_from_name",
            "warehouse_to_name",
            "counterparty",
            "agent",
            "counterparty_display_name",
            "comment",
            "discount_percent",
            "discount_amount",
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


class TransferItemInputSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=models.WarehouseProduct.objects.all())
    qty = serializers.DecimalField(max_digits=18, decimal_places=3)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0.00"))
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0.00"))
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, default=Decimal("0.00"))


class TransferCreateSerializer(serializers.Serializer):
    warehouse_from = serializers.PrimaryKeyRelatedField(queryset=models.Warehouse.objects.all())
    warehouse_to = serializers.PrimaryKeyRelatedField(queryset=models.Warehouse.objects.all())
    comment = serializers.CharField(required=False, allow_blank=True)
    items = TransferItemInputSerializer(many=True)

    def validate(self, attrs):
        items = attrs.get("items") or []
        if not items:
            raise serializers.ValidationError({"items": "Нельзя проводить пустое перемещение."})
        if attrs["warehouse_from"] == attrs["warehouse_to"]:
            raise serializers.ValidationError({"warehouse_to": "Склад-источник и склад-приемник должны быть разными."})
        return attrs


class ProductSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.WarehouseProduct
        fields = ("id", "name", "article", "barcode", "unit", "quantity")


class WarehouseSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Warehouse
        fields = ("id", "name")


class CounterpartySerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    agent = serializers.ReadOnlyField(source="agent.id")

    class Meta:
        model = models.Counterparty
        fields = ("id", "name", "type", "company", "branch", "agent")
