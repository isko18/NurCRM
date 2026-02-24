from decimal import Decimal
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth import get_user_model
from . import models

User = get_user_model()


class StockMoveSerializer(serializers.ModelSerializer):
    """Сериализатор движения товара с видом: приход или расход."""

    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)

    class Meta:
        model = models.StockMove
        fields = (
            "id",
            "document",
            "warehouse",
            "warehouse_name",
            "product",
            "product_name",
            "product_article",
            "qty_delta",
            "move_kind",
            "created_at",
        )


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
    moves = StockMoveSerializer(many=True, read_only=True)
    receipts = serializers.SerializerMethodField()
    expenses = serializers.SerializerMethodField()

    money_document_id = serializers.UUIDField(source="money_document.id", read_only=True, allow_null=True)
    money_document_number = serializers.CharField(source="money_document.number", read_only=True, allow_null=True)
    money_document_status = serializers.CharField(source="money_document.status", read_only=True, allow_null=True)
    money_document_amount = serializers.DecimalField(
        source="money_document.amount",
        max_digits=18,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )

    cash_register_name = serializers.CharField(source="cash_register.name", read_only=True, allow_null=True)
    payment_category_title = serializers.CharField(source="payment_category.title", read_only=True, allow_null=True)
    cash_request_status = serializers.SerializerMethodField()

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
    agent_display = serializers.SerializerMethodField()

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
            "prepayment_amount",
            "warehouse_from",
            "warehouse_to",
            "warehouse_from_name",
            "warehouse_to_name",
            "counterparty",
            "cash_register",
            "cash_register_name",
            "payment_category",
            "payment_category_title",
            "cash_request_status",
            "money_document_id",
            "money_document_number",
            "money_document_status",
            "money_document_amount",
            "agent",
            "agent_display",
            "counterparty_display_name",
            "comment",
            "discount_percent",
            "discount_amount",
            "total",
            "items",
            "moves",
            "receipts",
            "expenses",
        )
        read_only_fields = ("number", "total", "status", "date", "cash_request_status")

    def get_agent_display(self, obj):
        u = getattr(obj, "agent", None)
        if not u:
            return None
        if hasattr(u, "get_full_name") and u.get_full_name():
            return u.get_full_name()
        return (
            f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
            or getattr(u, "username", None)
            or getattr(u, "email", None)
            or str(getattr(u, "id", ""))
        )

    def get_cash_request_status(self, obj):
        req = getattr(obj, "cash_request", None)
        return getattr(req, "status", None)

    def get_receipts(self, obj):
        """Приходы — движения с move_kind=RECEIPT."""
        moves = getattr(obj, "_receipts_moves", None)
        if moves is None and hasattr(obj, "moves"):
            moves = [m for m in obj.moves.all() if m.move_kind == models.StockMove.MoveKind.RECEIPT]
        if moves is None:
            return []
        return StockMoveSerializer(moves, many=True).data

    def get_expenses(self, obj):
        """Расходы — движения с move_kind=EXPENSE."""
        moves = getattr(obj, "_expenses_moves", None)
        if moves is None and hasattr(obj, "moves"):
            moves = [m for m in obj.moves.all() if m.move_kind == models.StockMove.MoveKind.EXPENSE]
        if moves is None:
            return []
        return StockMoveSerializer(moves, many=True).data

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
        if instance.status in (instance.Status.POSTED, instance.Status.CASH_PENDING):
            raise serializers.ValidationError(
                {"status": "Нельзя изменять проведенный/ожидающий кассу документ. Сначала отмените проведение."}
            )
        
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


class CashRequestDocumentMiniSerializer(serializers.ModelSerializer):
    warehouse_from_name = serializers.CharField(source="warehouse_from.name", read_only=True, allow_null=True)
    counterparty_display_name = serializers.CharField(source="counterparty.name", read_only=True, allow_null=True)

    class Meta:
        model = models.Document
        fields = (
            "id",
            "number",
            "doc_type",
            "status",
            "payment_kind",
            "date",
            "total",
            "warehouse_from",
            "warehouse_from_name",
            "counterparty",
            "counterparty_display_name",
            "cash_register",
            "payment_category",
        )


class CashApprovalRequestSerializer(serializers.ModelSerializer):
    document = CashRequestDocumentMiniSerializer(read_only=True)
    money_document_id = serializers.UUIDField(source="money_document.id", read_only=True, allow_null=True)
    decided_by_id = serializers.UUIDField(source="decided_by.id", read_only=True, allow_null=True)

    class Meta:
        model = models.CashApprovalRequest
        fields = (
            "id",
            "status",
            "requires_money",
            "money_doc_type",
            "amount",
            "decision_note",
            "requested_at",
            "decided_at",
            "decided_by_id",
            "money_document_id",
            "document",
        )


class CashApprovalDecisionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)


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
    agent_display = serializers.SerializerMethodField()
    agent = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        allow_null=True,
        required=False,
        help_text="Назначить контрагента агенту (только владелец/админ). Агент видит только контрагентов, назначенных ему.",
    )

    class Meta:
        model = models.Counterparty
        fields = ("id", "name", "type", "company", "branch", "agent", "agent_display")
        read_only_fields = ("id", "company", "branch")

    def get_agent_display(self, obj):
        u = getattr(obj, "agent", None)
        if not u:
            return None
        if hasattr(u, "get_full_name") and u.get_full_name():
            return u.get_full_name()
        return f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() or getattr(u, "email", "") or str(u.id)
