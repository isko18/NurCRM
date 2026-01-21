from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from . import models


class PaymentCategorySerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = models.PaymentCategory
        fields = ("id", "company", "branch", "title")
        read_only_fields = ("id", "company", "branch")
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

    warehouse_name = serializers.CharField(
        source="warehouse.name",
        read_only=True,
        allow_null=True,
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
            "warehouse",
            "warehouse_name",
            "counterparty",
            "counterparty_display_name",
            "payment_category",
            "payment_category_title",
            "amount",
            "comment",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("number", "status", "date", "created_at", "updated_at")
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

