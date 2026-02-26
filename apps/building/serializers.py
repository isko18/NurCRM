from rest_framework import serializers
from django.db import transaction

from .models import (
    ResidentialComplex,
    ResidentialComplexDrawing,
    ResidentialComplexWarehouse,
    BuildingProduct,
    BuildingProcurementRequest,
    BuildingProcurementItem,
    BuildingProcurementCashDecision,
    BuildingTransferRequest,
    BuildingTransferItem,
    BuildingWarehouseStockItem,
    BuildingWarehouseStockMove,
    BuildingWorkflowEvent,
)


class ResidentialComplexSerializer(serializers.ModelSerializer):
    """Сериализатор для ЖК: список и детали."""

    class Meta:
        model = ResidentialComplex
        fields = [
            "id",
            "company",
            "name",
            "address",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "created_at", "updated_at"]


class ResidentialComplexCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания ЖК. company подставляется из request.user."""

    class Meta:
        model = ResidentialComplex
        fields = [
            "id",
            "name",
            "address",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        company = self.context["request"].user.company
        if not company:
            raise serializers.ValidationError({"company": "У пользователя не указана компания."})
        validated_data["company_id"] = company.id
        return super().create(validated_data)


class ResidentialComplexDrawingSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ResidentialComplexDrawing
        fields = [
            "id",
            "residential_complex",
            "title",
            "file",
            "file_url",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "file_url", "created_at", "updated_at"]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if not getattr(obj, "file", None):
            return None
        url = obj.file.url
        return request.build_absolute_uri(url) if request else url

    def validate_residential_complex(self, value):
        user = self.context["request"].user
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            user_company_id = getattr(user, "company_id", None)
            if not user_company_id:
                raise serializers.ValidationError("У пользователя не указана компания.")
            if value.company_id != user_company_id:
                raise serializers.ValidationError("ЖК принадлежит другой компании.")
        return value


class ResidentialComplexWarehouseSerializer(serializers.ModelSerializer):
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)

    class Meta:
        model = ResidentialComplexWarehouse
        fields = [
            "id",
            "residential_complex",
            "residential_complex_name",
            "name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class BuildingProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingProduct
        fields = [
            "id",
            "company",
            "name",
            "article",
            "barcode",
            "unit",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "created_at", "updated_at"]


class BuildingProcurementItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)

    class Meta:
        model = BuildingProcurementItem
        fields = [
            "id",
            "procurement",
            "product",
            "product_name",
            "product_article",
            "name",
            "unit",
            "quantity",
            "price",
            "line_total",
            "order",
            "note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "line_total", "created_at", "updated_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        procurement = attrs.get("procurement") or getattr(self.instance, "procurement", None)
        product = attrs.get("product")
        if product and procurement and product.company_id != procurement.residential_complex.company_id:
            raise serializers.ValidationError({"product": "Товар принадлежит другой компании."})
        if product:
            attrs.setdefault("name", product.name)
            attrs.setdefault("unit", product.unit)
        return attrs


class BuildingProcurementCashDecisionSerializer(serializers.ModelSerializer):
    decided_by_display = serializers.SerializerMethodField()

    class Meta:
        model = BuildingProcurementCashDecision
        fields = ["id", "procurement", "decision", "reason", "decided_by", "decided_by_display", "decided_at"]
        read_only_fields = ["id", "decided_at", "decided_by_display"]

    def get_decided_by_display(self, obj):
        user = getattr(obj, "decided_by", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))


class BuildingTransferItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingTransferItem
        fields = [
            "id",
            "transfer",
            "procurement_item",
            "name",
            "unit",
            "quantity",
            "price",
            "line_total",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "line_total", "created_at", "updated_at"]


class BuildingTransferSerializer(serializers.ModelSerializer):
    items = BuildingTransferItemSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    procurement_title = serializers.CharField(source="procurement.title", read_only=True)

    class Meta:
        model = BuildingTransferRequest
        fields = [
            "id",
            "procurement",
            "procurement_title",
            "warehouse",
            "warehouse_name",
            "created_by",
            "decided_by",
            "status",
            "note",
            "rejection_reason",
            "total_amount",
            "accepted_at",
            "rejected_at",
            "created_at",
            "updated_at",
            "items",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "decided_by",
            "status",
            "rejection_reason",
            "total_amount",
            "accepted_at",
            "rejected_at",
            "created_at",
            "updated_at",
            "items",
        ]


class BuildingProcurementSerializer(serializers.ModelSerializer):
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)
    initiator_display = serializers.SerializerMethodField()
    cash_decision = BuildingProcurementCashDecisionSerializer(read_only=True)
    transfers = BuildingTransferSerializer(many=True, read_only=True)
    items = BuildingProcurementItemSerializer(many=True, read_only=True)

    class Meta:
        model = BuildingProcurementRequest
        fields = [
            "id",
            "residential_complex",
            "residential_complex_name",
            "initiator",
            "initiator_display",
            "title",
            "comment",
            "status",
            "total_amount",
            "submitted_to_cash_at",
            "cash_decided_at",
            "cash_decided_by",
            "created_at",
            "updated_at",
            "cash_decision",
            "items",
            "transfers",
        ]
        read_only_fields = [
            "id",
            "initiator",
            "status",
            "total_amount",
            "submitted_to_cash_at",
            "cash_decided_at",
            "cash_decided_by",
            "created_at",
            "updated_at",
            "cash_decision",
            "items",
            "transfers",
        ]

    def get_initiator_display(self, obj):
        user = getattr(obj, "initiator", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))


class BuildingWorkflowEventSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = BuildingWorkflowEvent
        fields = [
            "id",
            "procurement",
            "procurement_item",
            "transfer",
            "transfer_item",
            "warehouse",
            "stock_item",
            "actor",
            "actor_display",
            "action",
            "from_status",
            "to_status",
            "message",
            "payload",
            "created_at",
        ]
        read_only_fields = fields

    def get_actor_display(self, obj):
        user = getattr(obj, "actor", None)
        if not user:
            return None
        full_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return full_name or getattr(user, "email", None) or str(getattr(user, "id", ""))


class BuildingWarehouseStockItemSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = BuildingWarehouseStockItem
        fields = ["id", "warehouse", "warehouse_name", "name", "unit", "quantity", "last_price", "created_at", "updated_at"]
        read_only_fields = fields


class BuildingWarehouseStockMoveSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    stock_item_name = serializers.CharField(source="stock_item.name", read_only=True)

    class Meta:
        model = BuildingWarehouseStockMove
        fields = [
            "id",
            "warehouse",
            "warehouse_name",
            "stock_item",
            "stock_item_name",
            "transfer",
            "move_type",
            "quantity_delta",
            "price",
            "created_by",
            "created_at",
        ]
        read_only_fields = fields


class BuildingReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)


class BuildingTransferCreateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)


class BuildingTransferAcceptSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)


class BuildingPurchaseDocumentItemSerializer(serializers.ModelSerializer):
    qty = serializers.DecimalField(source="quantity", max_digits=16, decimal_places=3)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)

    class Meta:
        model = BuildingProcurementItem
        fields = ["id", "product", "product_name", "product_article", "name", "unit", "qty", "price", "line_total", "order", "note"]
        read_only_fields = ["id", "line_total"]


class BuildingPurchaseDocumentSerializer(serializers.ModelSerializer):
    doc_type = serializers.CharField(read_only=True, default="PURCHASE")
    date = serializers.DateTimeField(source="created_at", read_only=True)
    total = serializers.DecimalField(source="total_amount", max_digits=16, decimal_places=2, read_only=True)
    number = serializers.CharField(read_only=True, allow_blank=True, default="")
    residential_complex_name = serializers.CharField(source="residential_complex.name", read_only=True)
    items = BuildingPurchaseDocumentItemSerializer(many=True)

    class Meta:
        model = BuildingProcurementRequest
        fields = [
            "id",
            "doc_type",
            "status",
            "number",
            "date",
            "residential_complex",
            "residential_complex_name",
            "comment",
            "total",
            "items",
        ]
        read_only_fields = ["id", "doc_type", "status", "number", "date", "total", "residential_complex_name"]

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        if not items_data:
            raise serializers.ValidationError({"items": "Нельзя создать закупку без позиций."})

        request = self.context.get("request")
        initiator = getattr(request, "user", None) if request else None
        with transaction.atomic():
            procurement = BuildingProcurementRequest.objects.create(initiator=initiator, **validated_data)
            for idx, item in enumerate(items_data, start=1):
                quantity = item.pop("quantity")
                product = item.get("product")
                if product and product.company_id != procurement.residential_complex.company_id:
                    raise serializers.ValidationError({"items": "Один из товаров принадлежит другой компании."})
                if product:
                    item.setdefault("name", product.name)
                    item.setdefault("unit", product.unit)
                BuildingProcurementItem.objects.create(
                    procurement=procurement,
                    quantity=quantity,
                    order=item.get("order", idx),
                    **item,
                )
            procurement.recalculate_totals()
        return procurement

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        if instance.status != BuildingProcurementRequest.Status.DRAFT:
            raise serializers.ValidationError({"status": "Можно изменять только закупку в статусе draft."})

        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            if items_data is not None:
                if not items_data:
                    raise serializers.ValidationError({"items": "Нельзя оставить закупку без позиций."})
                instance.items.all().delete()
                for idx, item in enumerate(items_data, start=1):
                    quantity = item.pop("quantity")
                    product = item.get("product")
                    if product and product.company_id != instance.residential_complex.company_id:
                        raise serializers.ValidationError({"items": "Один из товаров принадлежит другой компании."})
                    if product:
                        item.setdefault("name", product.name)
                        item.setdefault("unit", product.unit)
                    BuildingProcurementItem.objects.create(
                        procurement=instance,
                        quantity=quantity,
                        order=item.get("order", idx),
                        **item,
                    )
                instance.recalculate_totals()
        return instance
