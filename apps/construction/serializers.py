from rest_framework import serializers
from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.serializers import UserListSerializer  


# ─── CASHFLOW: внутри кассы ───────────────────────────────
class CashFlowInsideCashboxSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashFlow
        fields = ['id', 'type', 'name', 'amount', 'created_at']


# ─── CASHBOX: c вложенными CashFlow ────────────────────────
class CashboxWithFlowsSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    cashflows = CashFlowInsideCashboxSerializer(source='flows', many=True, read_only=True)

    class Meta:
        model = Cashbox
        fields = ['id', 'department', 'department_name', 'cashflows']


# ─── CASHBOX: краткий (для Department) ─────────────────────
class CashboxSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Cashbox
        fields = ['id', 'department', 'department_name', 'analytics']

    def get_analytics(self, obj):
        return obj.get_summary()


# ─── CASHFLOW: основной ────────────────────────────────────
class CashFlowSerializer(serializers.ModelSerializer):
    cashbox_name = serializers.CharField(source='cashbox.department.name', read_only=True)

    class Meta:
        model = CashFlow
        fields = [
            'id',
            'cashbox',           # read-only (автоопределяется)
            'cashbox_name',
            'type',
            'name',
            'amount',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'cashbox', 'cashbox_name']


# ─── DEPARTMENT: основной ──────────────────────────────────
class DepartmentSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    cashbox = CashboxSerializer(read_only=True)
    employees = UserListSerializer(many=True, read_only=True)
    employee_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True
    )
    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = [
            'id',
            'name',
            'company',
            'employees',
            'employee_ids',
            'cashbox',
            'analytics',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'company', 'cashbox', 'employees', 'analytics']

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['company'] = request.user.company

        employee_ids = validated_data.pop('employee_ids', [])
        department = Department.objects.create(**validated_data)

        if employee_ids:
            department.employees.set(employee_ids)

        return department

    def update(self, instance, validated_data):
        validated_data.pop('company', None)
        employee_ids = validated_data.pop('employee_ids', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if employee_ids is not None:
            instance.employees.set(employee_ids)

        return instance

    def get_analytics(self, obj):
        return obj.cashflow_summary()


# ─── DEPARTMENT: аналитика ─────────────────────────────────
class DepartmentAnalyticsSerializer(serializers.ModelSerializer):
    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'name', 'analytics']

    def get_analytics(self, obj):
        return obj.cashflow_summary()
