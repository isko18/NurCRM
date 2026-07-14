# apps/main/production_salary_serializers.py
"""Сериализаторы зарплаты в производстве (/main/production/salary/)."""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.main.models import (
    ProductionEmployeeRate,
    ProductionPieceRate,
    ProductionSalaryAccrual,
    ProductionSalaryPayout,
    ProductionWorkSession,
    _user_display_name,
)


class ProductionEmployeeRateSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = ProductionEmployeeRate
        fields = ["employee", "employee_name", "hourly_rate", "updated_at"]

    def get_employee_name(self, obj) -> str:
        return _user_display_name(obj.employee)


class ProductionEmployeeRateUpdateSerializer(serializers.Serializer):
    hourly_rate = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"),
    )


class ProductionPieceRateSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = ProductionPieceRate
        fields = ["product", "product_name", "amount_per_unit", "updated_at"]


class ProductionPieceRateUpdateSerializer(serializers.Serializer):
    amount_per_unit = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0"),
    )


class ProductionWorkSessionSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = ProductionWorkSession
        fields = [
            "id", "employee", "employee_name", "date", "hours", "comment",
            "created_by", "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]

    def get_employee_name(self, obj) -> str:
        return _user_display_name(obj.employee)


class ProductionWorkSessionCreateSerializer(serializers.Serializer):
    employee = serializers.UUIDField()
    date = serializers.DateField()
    hours = serializers.DecimalField(
        max_digits=6, decimal_places=2,
        min_value=Decimal("0.01"), max_value=Decimal("24"),
    )
    comment = serializers.CharField(required=False, allow_blank=True, max_length=255)


class ProductionSalaryAccrualSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    product_name = serializers.CharField(source="product.name", read_only=True, default=None)

    class Meta:
        model = ProductionSalaryAccrual
        fields = [
            "id", "employee", "employee_name", "kind", "status", "amount",
            "work_session", "hours", "rate",
            "production_record", "product", "product_name", "quantity", "amount_per_unit",
            "payout", "created_at",
        ]

    def get_employee_name(self, obj) -> str:
        return _user_display_name(obj.employee)


class ProductionSalaryPayoutSerializer(serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = ProductionSalaryPayout
        fields = [
            "id", "employee", "employee_name", "amount", "cashbox", "comment",
            "created_by", "created_at",
        ]

    def get_employee_name(self, obj) -> str:
        return _user_display_name(obj.employee)


class ProductionSalaryPayoutCreateSerializer(serializers.Serializer):
    employee = serializers.UUIDField()
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01"),
    )
    cashbox = serializers.UUIDField()
    comment = serializers.CharField(required=False, allow_blank=True, max_length=255)
