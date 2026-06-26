"""
Сериализаторы раздела «Сводка» (Сводки продаж).

Контракт: JSON для Web и Mobile. PDF строится на фронтенде, бэкенд отдаёт только JSON.
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model

from . import models
from .services_summaries import _full_name

User = get_user_model()


def _agent_code(user) -> str:
    """Код агента для отображения. Отдельного поля нет — используем код/username, иначе пусто."""
    if not user:
        return ""
    return getattr(user, "code", None) or getattr(user, "username", None) or ""


class SummaryAgentSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.SerializerMethodField()
    code = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        return _full_name(obj)

    def get_code(self, obj):
        return _agent_code(obj)


class SummaryCreatedBySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        return _full_name(obj)


class SummaryWarehouseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class SummaryDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.WarehouseSalesSummaryDocument
        fields = (
            "id", "number", "date", "agent", "client", "address",
            "quantity", "weight", "amount",
        )


class SummaryProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.WarehouseSalesSummaryProduct
        fields = (
            "name", "unit", "packages", "per_package",
            "quantity", "price", "amount", "weight",
        )


class SummaryDetailSerializer(serializers.ModelSerializer):
    """Полный объект сводки (GET by id, POST/PATCH/regenerate ответы)."""

    warehouse = SummaryWarehouseSerializer(read_only=True)
    created_by = SummaryCreatedBySerializer(read_only=True)
    agents = SummaryAgentSerializer(many=True, read_only=True)
    documents = SummaryDocumentSerializer(many=True, read_only=True)
    products = SummaryProductSerializer(many=True, read_only=True)
    totals = serializers.SerializerMethodField()

    class Meta:
        model = models.WarehouseSalesSummary
        fields = (
            "id", "number", "name", "comment", "date", "type",
            "warehouse", "created_by", "created_at", "updated_at",
            "agents", "documents", "products", "totals",
        )

    def get_totals(self, obj):
        return {
            "documents_count": obj.documents_count,
            "products_count": obj.products_count,
            "total_quantity": obj.total_quantity,
            "total_weight": obj.total_weight,
            "total_amount": obj.total_amount,
        }


class SummaryListSerializer(serializers.ModelSerializer):
    """Облегчённая карточка для списка (без documents/products)."""

    created_by = SummaryCreatedBySerializer(read_only=True)
    agents_count = serializers.SerializerMethodField()

    class Meta:
        model = models.WarehouseSalesSummary
        fields = (
            "id", "number", "name", "type", "date",
            "created_by", "agents_count", "documents_count",
            "total_amount", "created_at",
        )

    def get_agents_count(self, obj):
        return obj.agents.count()


class SummaryWriteSerializer(serializers.ModelSerializer):
    """Создание/обновление сводки. Снапшот собирается во вьюхе."""

    warehouse = serializers.PrimaryKeyRelatedField(queryset=models.Warehouse.objects.all())
    agents = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, required=False,
    )

    class Meta:
        model = models.WarehouseSalesSummary
        fields = ("name", "comment", "type", "date", "warehouse", "agents")
        extra_kwargs = {
            "comment": {"required": False},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # warehouse/date нельзя менять при обновлении (снапшот привязан к ним).
        if self.instance is not None:
            self.fields["warehouse"].required = False
            self.fields["date"].required = False

    def validate(self, attrs):
        summary_type = attrs.get("type") or getattr(self.instance, "type", None)
        if summary_type == models.WarehouseSalesSummary.Type.GENERAL:
            # agents игнорируется для общей сводки
            attrs["agents"] = []
        return attrs
