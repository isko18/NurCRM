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

    employees_data = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False
    )

    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = [
            'id', 'name', 'company', 'color',
            'employees', 'employees_data', 'cashbox',
            'analytics', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'company', 'cashbox', 'employees', 'analytics']

    def _assign_employees_and_permissions(self, department, employees_data):
        from apps.users.models import User  # локальный импорт во избежание циклов

        for entry in employees_data:
            user_id = entry.get("id")
            try:
                user = User.objects.get(id=user_id, company=department.company)
            except User.DoesNotExist:
                continue  # или raise serializers.ValidationError

            department.employees.add(user)

            # Обновляем права (если переданы)
            for field in [
                'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
                'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking'
            ]:
                if field in entry:
                    setattr(user, field, entry[field])
            user.save()

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['company'] = request.user.company

        employees_data = validated_data.pop('employees_data', [])
        department = Department.objects.create(**validated_data)

        self._assign_employees_and_permissions(department, employees_data)
        return department

    def update(self, instance, validated_data):
        validated_data.pop('company', None)
        employees_data = validated_data.pop('employees_data', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if employees_data is not None:
            instance.employees.clear()
            self._assign_employees_and_permissions(instance, employees_data)

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
