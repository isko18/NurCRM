from rest_framework import serializers
from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.serializers import UserListSerializer, UserWithPermissionsSerializer


# ─── CASHFLOW: внутри кассы ───────────────────────────────
class CashFlowInsideCashboxSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashFlow
        fields = ['id', 'type', 'name', 'amount', 'created_at']


# ─── CASHBOX: c вложенными CashFlow ────────────────────────
class CashboxWithFlowsSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), allow_null=True, required=False
    )
    department_name = serializers.SerializerMethodField()
    cashflows = CashFlowInsideCashboxSerializer(source='flows', many=True, read_only=True)

    class Meta:
        model = Cashbox
        fields = ['id', 'company', 'department', 'department_name', 'name', 'cashflows']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and getattr(request.user, 'company_id', None):
            # Разрешаем выбирать только отделы своей компании
            self.fields['department'].queryset = Department.objects.filter(
                company_id=request.user.company_id
            )

    def get_department_name(self, obj):
        return obj.department.name if obj.department else None

    def validate(self, attrs):
        request = self.context.get('request')
        dept = attrs.get('department') or getattr(self.instance, 'department', None)
        if dept and request and dept.company_id != request.user.company_id:
            raise serializers.ValidationError('Отдел должен принадлежать вашей компании.')
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('company', None)
        return super().update(instance, validated_data)


# ─── CASHBOX: краткий (для Department) ─────────────────────
class CashboxSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), allow_null=True, required=False
    )
    department_name = serializers.SerializerMethodField()
    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Cashbox
        fields = ['id', 'company', 'department', 'department_name', 'name', 'analytics']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and getattr(request.user, 'company_id', None):
            self.fields['department'].queryset = Department.objects.filter(
                company_id=request.user.company_id
            )

    def get_department_name(self, obj):
        return obj.department.name if obj.department else None

    def get_analytics(self, obj):
        return obj.get_summary()

    def validate(self, attrs):
        request = self.context.get('request')
        dept = attrs.get('department') or getattr(self.instance, 'department', None)
        if dept and request and dept.company_id != request.user.company_id:
            raise serializers.ValidationError('Отдел должен принадлежать вашей компании.')
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('company', None)
        return super().update(instance, validated_data)


# ─── CASHFLOW: основной ────────────────────────────────────
class CashFlowSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    cashbox = serializers.PrimaryKeyRelatedField(queryset=Cashbox.objects.all())
    cashbox_name = serializers.SerializerMethodField()

    class Meta:
        model = CashFlow
        fields = [
            'id',
            'company',
            'cashbox',
            'cashbox_name',
            'type',
            'name',
            'amount',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'cashbox_name', 'company']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and getattr(request.user, 'company_id', None):
            # Разрешаем выбирать только кассы своей компании
            self.fields['cashbox'].queryset = Cashbox.objects.filter(
                company_id=request.user.company_id
            )

    def get_cashbox_name(self, obj):
        if obj.cashbox.department:
            return obj.cashbox.department.name
        return obj.cashbox.name or "Свободная касса"

    def validate(self, attrs):
        request = self.context.get('request')
        cashbox = attrs.get('cashbox') or getattr(self.instance, 'cashbox', None)
        if cashbox and request and cashbox.company_id != request.user.company_id:
            raise serializers.ValidationError('Касса должна принадлежать вашей компании.')
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('company', None)
        return super().update(instance, validated_data)


# ─── DEPARTMENT: основной ──────────────────────────────────
class DepartmentSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    cashbox = CashboxSerializer(read_only=True)
    employees = UserWithPermissionsSerializer(many=True, read_only=True)

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

        permission_fields = [
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process', 'can_view_additional_services',

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
        ]

        for entry in employees_data:
            user_id = entry.get("id")
            try:
                user = User.objects.get(id=user_id, company=department.company)
            except User.DoesNotExist:
                continue  # или raise serializers.ValidationError

            department.employees.add(user)

            for field in permission_fields:
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
