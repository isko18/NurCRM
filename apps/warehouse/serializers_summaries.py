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


class SummaryDocumentItemSerializer(serializers.ModelSerializer):
    """Позиция конкретной накладной (детализация для PDF)."""

    class Meta:
        model = models.WarehouseSalesSummaryDocumentItem
        fields = (
            "name", "unit", "quantity", "price",
            "discount_percent", "discount_amount", "amount", "weight",
        )


class SummaryDocumentSerializer(serializers.ModelSerializer):
    items = SummaryDocumentItemSerializer(many=True, read_only=True)

    class Meta:
        model = models.WarehouseSalesSummaryDocument
        fields = (
            "id", "number", "date", "agent", "client", "address",
            "quantity", "weight", "amount", "items",
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
    warehouses = SummaryWarehouseSerializer(many=True, read_only=True)
    created_by = SummaryCreatedBySerializer(read_only=True)
    agents = SummaryAgentSerializer(many=True, read_only=True)
    documents = SummaryDocumentSerializer(many=True, read_only=True)
    products = SummaryProductSerializer(many=True, read_only=True)
    totals = serializers.SerializerMethodField()

    class Meta:
        model = models.WarehouseSalesSummary
        fields = (
            "id", "number", "name", "comment", "date", "type",
            "warehouse", "warehouses", "all_warehouses",
            "created_by", "created_at", "updated_at",
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

    # Legacy: один склад. Оставлен для совместимости со старым фронтом.
    warehouse = serializers.PrimaryKeyRelatedField(
        queryset=models.Warehouse.objects.all(), required=False, allow_null=True,
    )
    # Новое: набор складов (когда all_warehouses=false).
    warehouses = serializers.PrimaryKeyRelatedField(
        queryset=models.Warehouse.objects.all(), many=True, required=False,
    )
    all_warehouses = serializers.BooleanField(required=False)
    agents = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), many=True, required=False,
    )

    class Meta:
        model = models.WarehouseSalesSummary
        fields = ("name", "comment", "type", "date", "warehouse", "warehouses", "all_warehouses", "agents")
        extra_kwargs = {
            "comment": {"required": False},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # date нельзя менять при обновлении (снапшот привязан к ней).
        if self.instance is not None:
            self.fields["date"].required = False

    def validate(self, attrs):
        summary_type = attrs.get("type") or getattr(self.instance, "type", None)
        if summary_type == models.WarehouseSalesSummary.Type.GENERAL:
            # agents игнорируется для общей сводки
            attrs["agents"] = []

        inst = self.instance
        all_wh = attrs.get("all_warehouses", getattr(inst, "all_warehouses", False))

        # Разрешаем итоговый набор складов: приоритет warehouses, затем legacy warehouse,
        # затем (при обновлении) текущее состояние записи.
        if "warehouses" in attrs:
            resolved = list(attrs.get("warehouses") or [])
        elif "warehouse" in attrs:
            single = attrs.get("warehouse")
            resolved = [single] if single is not None else []
        elif inst is not None:
            resolved = list(models.Warehouse.objects.filter(id__in=inst.warehouse_ids())) if not all_wh else []
        else:
            resolved = []

        if all_wh:
            # По всем складам компании — явный список не нужен.
            resolved = []
        elif not resolved:
            raise serializers.ValidationError(
                {"warehouses": "Укажите хотя бы один склад или включите all_warehouses."}
            )

        # Проверка принадлежности складов компании (компания придёт из контекста создания).
        company_id = getattr(getattr(inst, "company", None), "id", None) or self.context.get("company_id")
        if company_id is not None:
            for wh in resolved:
                if getattr(wh, "company_id", None) != company_id:
                    raise serializers.ValidationError(
                        {"warehouses": f"Склад {wh.id} принадлежит другой компании."}
                    )

        # Агенты — любой сотрудник компании; чужие компании отсекаем.
        if company_id is not None and attrs.get("agents"):
            for agent in attrs["agents"]:
                if getattr(agent, "company_id", None) not in (None, company_id):
                    raise serializers.ValidationError(
                        {"agents": f"Пользователь {agent.id} из другой компании."}
                    )

        attrs["_resolved_warehouses"] = resolved
        attrs["all_warehouses"] = all_wh
        # FK warehouse держим синхронным: первый из набора либо None (для all_warehouses).
        attrs["warehouse"] = resolved[0] if resolved else None
        attrs.pop("warehouses", None)
        return attrs

    def _apply_warehouses(self, instance, warehouses):
        instance.warehouses.set(warehouses)

    def create(self, validated_data):
        warehouses = validated_data.pop("_resolved_warehouses", [])
        instance = super().create(validated_data)
        self._apply_warehouses(instance, warehouses)
        return instance

    def update(self, instance, validated_data):
        warehouses = validated_data.pop("_resolved_warehouses", None)
        instance = super().update(instance, validated_data)
        if warehouses is not None:
            self._apply_warehouses(instance, warehouses)
        return instance
