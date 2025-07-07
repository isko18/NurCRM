from rest_framework import serializers
from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.serializers import UserListSerializer  


class CashboxSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Cashbox
        fields = ['id', 'department', 'department_name', 'analytics']

    def get_analytics(self, obj):
        return obj.get_summary()


class DepartmentSerializer(serializers.ModelSerializer):
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
        read_only_fields = ['id', 'created_at', 'cashbox', 'employees', 'analytics']

    def create(self, validated_data):
        employee_ids = validated_data.pop('employee_ids', [])
        department = Department.objects.create(**validated_data)

        if employee_ids:
            department.employees.set(employee_ids)

        return department

    def update(self, instance, validated_data):
        employee_ids = validated_data.pop('employee_ids', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        if employee_ids is not None:
            instance.employees.set(employee_ids)

        return instance

    def get_analytics(self, obj):
        return obj.cashflow_summary()


class CashFlowSerializer(serializers.ModelSerializer):
    cashbox_name = serializers.CharField(source='cashbox.department.name', read_only=True)

    class Meta:
        model = CashFlow
        fields = [
            'id',
            'cashbox',
            'cashbox_name',
            'type',
            'name',
            'amount',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'cashbox_name']


class DepartmentAnalyticsSerializer(serializers.ModelSerializer):
    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'name', 'analytics']

    def get_analytics(self, obj):
        return obj.cashflow_summary()
