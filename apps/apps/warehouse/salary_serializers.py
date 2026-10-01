# apps/warehouse/salary_serializers.py
from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from apps.warehouse import models as m


def _money2(v) -> str:
    try:
        return str(Decimal(v or 0).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return "0.00"


def _user_name(user) -> str:
    if not user:
        return ""
    full = ""
    if hasattr(user, "get_full_name"):
        try:
            full = (user.get_full_name() or "").strip()
        except Exception:
            full = ""
    if not full:
        full = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip()
    return full or getattr(user, "email", None) or str(getattr(user, "id", "") or "")


# ─────────────────────────────────────────────────────────────
# Ставки складов
# ─────────────────────────────────────────────────────────────
class WarehouseRateRowSerializer(serializers.Serializer):
    """Строка таблицы ставок. На входе — Warehouse (с reverse OneToOne salary_rate)."""
    warehouse = serializers.UUIDField(source="id", read_only=True)
    warehouse_name = serializers.CharField(source="name", read_only=True)
    retail_percent = serializers.SerializerMethodField()
    wholesale_percent = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    @staticmethod
    def _rate(obj):
        try:
            return obj.salary_rate
        except m.WarehouseSalaryRate.DoesNotExist:
            return None
        except Exception:
            return None

    def get_retail_percent(self, obj):
        r = self._rate(obj)
        return _money2(r.retail_percent if r else 0)

    def get_wholesale_percent(self, obj):
        r = self._rate(obj)
        return _money2(r.wholesale_percent if r else 0)

    def get_updated_at(self, obj):
        r = self._rate(obj)
        return r.updated_at.isoformat() if (r and r.updated_at) else None


class WarehouseRateUpdateSerializer(serializers.Serializer):
    retail_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    wholesale_percent = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)

    def _check_range(self, value, field):
        if value is None:
            return value
        if value < Decimal("0") or value > Decimal("100"):
            raise serializers.ValidationError({field: ["Процент должен быть от 0 до 100"]})
        return value

    def validate_retail_percent(self, value):
        return self._check_range(value, "retail_percent")

    def validate_wholesale_percent(self, value):
        return self._check_range(value, "wholesale_percent")

    def validate(self, attrs):
        if "retail_percent" not in attrs and "wholesale_percent" not in attrs:
            raise serializers.ValidationError(
                {"detail": "Укажите retail_percent и/или wholesale_percent."}
            )
        return attrs


# ─────────────────────────────────────────────────────────────
# Начисления
# ─────────────────────────────────────────────────────────────
class AgentSalaryAccrualSerializer(serializers.ModelSerializer):
    agent_name = serializers.SerializerMethodField()
    sale_number = serializers.CharField(source="sale.number", read_only=True, allow_null=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True, allow_null=True)
    sale_amount = serializers.SerializerMethodField()
    percent = serializers.SerializerMethodField()
    amount = serializers.SerializerMethodField()

    class Meta:
        model = m.AgentSalaryAccrual
        fields = (
            "id", "created_at",
            "agent", "agent_name",
            "sale", "sale_number",
            "warehouse", "warehouse_name",
            "sale_type", "sale_amount", "percent", "amount",
            "status", "is_correction",
        )

    def get_agent_name(self, obj):
        return _user_name(getattr(obj, "agent", None))

    def get_sale_amount(self, obj):
        return _money2(obj.sale_amount)

    def get_percent(self, obj):
        return _money2(obj.percent)

    def get_amount(self, obj):
        return _money2(obj.amount)


# ─────────────────────────────────────────────────────────────
# Выплаты
# ─────────────────────────────────────────────────────────────
class AgentSalaryPayoutSerializer(serializers.ModelSerializer):
    agent_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    amount = serializers.SerializerMethodField()

    class Meta:
        model = m.AgentSalaryPayout
        fields = (
            "id", "created_at",
            "agent", "agent_name",
            "amount", "comment",
            "created_by", "created_by_name",
        )

    def get_agent_name(self, obj):
        return _user_name(getattr(obj, "agent", None))

    def get_created_by_name(self, obj):
        return _user_name(getattr(obj, "created_by", None))

    def get_amount(self, obj):
        return _money2(obj.amount)


class AgentSalaryPayoutCreateSerializer(serializers.Serializer):
    agent = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    comment = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_amount(self, value):
        if value is None or value <= Decimal("0"):
            raise serializers.ValidationError(["Сумма должна быть больше 0."])
        return value
