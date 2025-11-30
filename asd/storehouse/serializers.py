from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    Warehouse, Supplier, Product, Stock,
    StockIn, StockInItem,
    StockOut, StockOutItem,
    StockTransfer, StockTransferItem
)
from apps.main.models import ProductBrand, ProductCategory


# ===========================
# Общий миксин: company/branch из контекста (read-only наружу)
# ===========================
class CompanyBranchReadOnlyMixin(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    _cached_branch = None

    def _auto_branch(self):
        if self._cached_branch is not None:
            return self._cached_branch

        request = self.context.get("request")
        if not request:
            self._cached_branch = None
            return None

        user = getattr(request, "user", None)
        user_company_id = getattr(getattr(user, "company", None), "id", None)

        branch_candidate = None
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                branch_candidate = primary() or None
            except Exception:
                branch_candidate = None
        elif primary:
            branch_candidate = primary

        if branch_candidate is None and hasattr(request, "branch"):
            branch_candidate = getattr(request, "branch")

        # консистентность company ↔ branch.company
        if branch_candidate and user_company_id and getattr(branch_candidate, "company_id", None) != user_company_id:
            branch_candidate = None

        self._cached_branch = branch_candidate
        return self._cached_branch

    def _inject_company_branch(self, validated_data):
        request = self.context.get("request")
        if request:
            user = getattr(request, "user", None)
            company = getattr(user, "company", None)
            if company:
                validated_data["company"] = company
            # многие модели в складе имеют branch — если есть поле, проставим
            model = getattr(getattr(self, "Meta", None), "model", None)
            if model and any(f.name == "branch" for f in model._meta.get_fields()):
                validated_data["branch"] = self._auto_branch()
        return validated_data

    def create(self, validated_data):
        self._inject_company_branch(validated_data)
        obj = super().create(validated_data)
        # дернём model.clean() ещё раз, чтобы ловить ошибки как ValidationError DRF
        try:
            obj.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
        return obj

    def update(self, instance, validated_data):
        self._inject_company_branch(validated_data)
        obj = super().update(instance, validated_data)
        try:
            obj.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
        return obj


# 📦 Склад
class WarehouseSerializer(CompanyBranchReadOnlyMixin):
    class Meta:
        ref_name = "WarehouseProduct"
        model = Warehouse
        fields = [
            "id", "company", "branch", "name", "address",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at"]


# 🚚 Поставщик
class SupplierSerializer(CompanyBranchReadOnlyMixin):
    class Meta:
        model = Supplier
        fields = [
            "id", "company", "branch", "name", "contact_name", "phone", "address", "email",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at"]


# 🛒 Товар
class ProductSerializer(CompanyBranchReadOnlyMixin):
    brand_name = serializers.CharField(source="brand.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        ref_name = "StorehouseProduct"
        model = Product
        fields = [
            "id", "company", "branch",
            "name", "barcode", "brand", "brand_name", "category", "category_name",
            "unit", "purchase_price", "selling_price", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at"]


# 📊 Остатки
class StockSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    company = serializers.ReadOnlyField(source="warehouse.company_id")  # удобно видеть компанию в ответе

    class Meta:
        model = Stock
        fields = ["id", "company", "warehouse", "warehouse_name", "product", "product_name", "quantity"]

    def validate(self, attrs):
        warehouse = attrs.get("warehouse") or getattr(self.instance, "warehouse", None)
        product = attrs.get("product") or getattr(self.instance, "product", None)

        if warehouse and product:
            if product.company_id != warehouse.company_id:
                raise serializers.ValidationError({"product": "Товар и склад принадлежат разным компаниям."})
            # если склад филиальный — товар глобальный или того же филиала
            if warehouse.branch_id and product.branch_id not in (None, warehouse.branch_id):
                raise serializers.ValidationError({"product": "Товар другого филиала, чем склад."})

        return attrs


# ===== Вложенные айтемы =====
class StockInItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockInItem
        fields = ["id", "stock_in", "product", "product_name", "quantity", "price"]
        read_only_fields = ["id", "stock_in", "product_name"]


class StockOutItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockOutItem
        fields = ["id", "stock_out", "product", "product_name", "quantity"]
        read_only_fields = ["id", "stock_out", "product_name"]


class StockTransferItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockTransferItem
        fields = ["id", "transfer", "product", "product_name", "quantity"]
        read_only_fields = ["id", "transfer", "product_name"]


# ===== Документы =====
class StockInSerializer(CompanyBranchReadOnlyMixin):
    items = StockInItemSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = StockIn
        fields = [
            "id", "company", "branch",
            "document_number", "date",
            "supplier", "supplier_name",
            "warehouse", "warehouse_name",
            "items", "created_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "supplier_name", "warehouse_name"]

    def validate(self, attrs):
        request_branch = self._auto_branch()
        company = getattr(getattr(self.context.get("request"), "user", None), "company", None)

        instance = self.instance
        supplier = attrs.get("supplier", getattr(instance, "supplier", None))
        warehouse = attrs.get("warehouse", getattr(instance, "warehouse", None))
        # branch согласованность документа со связями
        if company and supplier and supplier.company_id != company.id:
            raise serializers.ValidationError({"supplier": "Поставщик другой компании."})
        if company and warehouse and warehouse.company_id != company.id:
            raise serializers.ValidationError({"warehouse": "Склад другой компании."})

        if request_branch is not None:
            tbid = request_branch.id
            if supplier and supplier.branch_id not in (None, tbid):
                raise serializers.ValidationError({"supplier": "Поставщик другого филиала."})
            if warehouse and warehouse.branch_id not in (None, tbid):
                raise serializers.ValidationError({"warehouse": "Склад другого филиала."})

        return attrs

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        self._inject_company_branch(validated_data)

        with transaction.atomic():
            stock_in = StockIn(**validated_data)
            # «теневая» проверка
            try:
                stock_in.clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
            stock_in.save()

            # позиции
            for item in items_data:
                sii = StockInItem(stock_in=stock_in, **item)
                try:
                    sii.clean()
                except DjangoValidationError as e:
                    raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
                sii.save()
        return stock_in


class StockOutSerializer(CompanyBranchReadOnlyMixin):
    items = StockOutItemSerializer(many=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = StockOut
        fields = [
            "id", "company", "branch",
            "document_number", "date",
            "warehouse", "warehouse_name",
            "type", "recipient", "destination_address",
            "items", "created_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "warehouse_name"]

    def validate(self, attrs):
        request_branch = self._auto_branch()
        company = getattr(getattr(self.context.get("request"), "user", None), "company", None)

        instance = self.instance
        warehouse = attrs.get("warehouse", getattr(instance, "warehouse", None))

        if company and warehouse and warehouse.company_id != company.id:
            raise serializers.ValidationError({"warehouse": "Склад другой компании."})

        if request_branch is not None and warehouse and warehouse.branch_id not in (None, request_branch.id):
            raise serializers.ValidationError({"warehouse": "Склад другого филиала."})

        return attrs

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        self._inject_company_branch(validated_data)

        with transaction.atomic():
            stock_out = StockOut(**validated_data)
            try:
                stock_out.clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
            stock_out.save()

            for item in items_data:
                soi = StockOutItem(stock_out=stock_out, **item)
                try:
                    soi.clean()
                except DjangoValidationError as e:
                    raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
                soi.save()
        return stock_out


class StockTransferSerializer(CompanyBranchReadOnlyMixin):
    items = StockTransferItemSerializer(many=True)
    source_name = serializers.CharField(source="source_warehouse.name", read_only=True)
    destination_name = serializers.CharField(source="destination_warehouse.name", read_only=True)

    class Meta:
        model = StockTransfer
        fields = [
            "id", "company", "branch",
            "document_number", "date",
            "source_warehouse", "source_name",
            "destination_warehouse", "destination_name",
            "items", "created_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "source_name", "destination_name"]

    def validate(self, attrs):
        company = getattr(getattr(self.context.get("request"), "user", None), "company", None)
        request_branch = self._auto_branch()

        instance = self.instance
        src = attrs.get("source_warehouse", getattr(instance, "source_warehouse", None))
        dst = attrs.get("destination_warehouse", getattr(instance, "destination_warehouse", None))

        if company:
            if src and src.company_id != company.id:
                raise serializers.ValidationError({"source_warehouse": "Склад-источник другой компании."})
            if dst and dst.company_id != company.id:
                raise serializers.ValidationError({"destination_warehouse": "Склад-приёмник другой компании."})

        if request_branch is not None:
            tbid = request_branch.id
            if src and src.branch_id not in (None, tbid):
                raise serializers.ValidationError({"source_warehouse": "Источник другого филиала."})
            if dst and dst.branch_id not in (None, tbid):
                raise serializers.ValidationError({"destination_warehouse": "Приёмник другого филиала."})

        return attrs

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        self._inject_company_branch(validated_data)

        with transaction.atomic():
            transfer = StockTransfer(**validated_data)
            try:
                transfer.clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
            transfer.save()

            for item in items_data:
                sti = StockTransferItem(transfer=transfer, **item)
                try:
                    sti.clean()
                except DjangoValidationError as e:
                    raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
                sti.save()
        return transfer
