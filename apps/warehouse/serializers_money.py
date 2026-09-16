from decimal import Decimal

from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from . import models


class CashRegisterSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = models.CashRegister
        fields = ("id", "company", "branch", "name", "location")
        read_only_fields = ("id", "company", "branch")


class CashRegisterDetailSerializer(CashRegisterSerializer):
    """Касса с балансом, приходами и расходами."""

    balance = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    receipts = serializers.ListField(read_only=True)
    expenses = serializers.ListField(read_only=True)

    class Meta(CashRegisterSerializer.Meta):
        fields = CashRegisterSerializer.Meta.fields + ("balance", "receipts", "expenses")


class PaymentCategorySerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = models.PaymentCategory
        fields = ("id", "company", "branch", "title", "system_code")
        read_only_fields = ("id", "company", "branch", "system_code")
        ref_name = "WarehousePaymentCategorySerializer"


class MoneyDocumentSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    counterparty_display_name = serializers.CharField(
        source="counterparty.name",
        read_only=True,
        allow_null=True,
    )

    payment_category_title = serializers.CharField(
        source="payment_category.title",
        read_only=True,
        allow_null=True,
    )

    cash_register_name = serializers.CharField(
        source="cash_register.name",
        read_only=True,
        allow_null=True,
    )
    warehouse_name = serializers.CharField(
        source="warehouse.name",
        read_only=True,
        allow_null=True,
    )

    # Операционная дата: принимаем YYYY-MM-DD (модалка) или ISO-datetime.
    # Не передана при создании → используется текущий момент (default модели).
    date = serializers.DateTimeField(
        required=False,
        input_formats=["iso-8601", "%Y-%m-%d"],
    )

    class Meta:
        model = models.MoneyDocument
        fields = (
            "id",
            "company",
            "branch",
            "doc_type",
            "status",
            "number",
            "date",
            "cash_register",
            "cash_register_name",
            "warehouse",
            "warehouse_name",
            "counterparty",
            "counterparty_display_name",
            "payment_category",
            "payment_category_title",
            "payment_method",
            "source_document",
            "amount",
            "comment",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("number", "status", "created_at", "updated_at", "source_document")
        ref_name = "WarehouseMoneyDocumentSerializer"

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        if instance and instance.status == instance.Status.POSTED:
            raise serializers.ValidationError({"status": "Нельзя изменять проведенный документ. Сначала отмените проведение."})

        # Run model validation with merged attrs
        obj = instance or models.MoneyDocument()
        for k, v in attrs.items():
            setattr(obj, k, v)
        try:
            obj.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
        return attrs

    def create(self, validated_data):
        cash_register = validated_data.get("cash_register")
        if cash_register:
            validated_data.setdefault("company", cash_register.company)
            validated_data.setdefault("branch", cash_register.branch)
        return super().create(validated_data)


class PartnerCashIncassationCreateSerializer(serializers.Serializer):
    cash_register_from = serializers.PrimaryKeyRelatedField(queryset=models.CashRegister.objects.all())
    cash_register_to = serializers.PrimaryKeyRelatedField(queryset=models.CashRegister.objects.all())
    amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=512)


class CompanyCashIncassationSerializer(serializers.ModelSerializer):
    from_company_name = serializers.CharField(source="from_company.name", read_only=True)
    to_company_name = serializers.CharField(source="to_company.name", read_only=True)
    cash_register_from_name = serializers.CharField(source="cash_register_from.name", read_only=True)
    cash_register_to_name = serializers.CharField(source="cash_register_to.name", read_only=True)
    expense_document_number = serializers.CharField(source="expense_document.number", read_only=True)
    receipt_document_number = serializers.CharField(source="receipt_document.number", read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, allow_null=True)

    class Meta:
        model = models.CompanyCashIncassation
        fields = (
            "id",
            "from_company",
            "from_company_name",
            "to_company",
            "to_company_name",
            "cash_register_from",
            "cash_register_from_name",
            "cash_register_to",
            "cash_register_to_name",
            "expense_document",
            "expense_document_number",
            "receipt_document",
            "receipt_document_number",
            "amount",
            "comment",
            "created_by",
            "created_by_email",
            "created_at",
        )
        read_only_fields = (
            "id",
            "from_company",
            "from_company_name",
            "to_company",
            "to_company_name",
            "cash_register_from",
            "cash_register_from_name",
            "cash_register_to",
            "cash_register_to_name",
            "expense_document",
            "expense_document_number",
            "receipt_document",
            "receipt_document_number",
            "amount",
            "comment",
            "created_by",
            "created_by_email",
            "created_at",
        )


class WarehouseCashConfirmationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.WarehouseCashConfirmationSettings
        fields = ("enabled",)


