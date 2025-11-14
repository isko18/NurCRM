from rest_framework import serializers
from django.db.models import Q

from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.serializers import UserWithPermissionsSerializer


# ───────────────────────────────────────────────────────────
# Общий миксин: company/branch
# ───────────────────────────────────────────────────────────
class CompanyBranchReadOnlyMixin(serializers.ModelSerializer):
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.
    Порядок получения branch:
      1) user.primary_branch() / user.primary_branch
      2) request.branch (если middleware это положил)
      3) None (глобальная запись компании)
    """
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    def _auto_branch(self):
        request = self.context.get("request")
        if not request:
            return None
        user = getattr(request, "user", None)

        # пробуем user.primary_branch() как функцию
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val:
                    return val
            except Exception:
                pass
        # или просто атрибут
        if primary:
            return primary

        # middleware мог положить request.branch
        if hasattr(request, "branch"):
            return request.branch

        # глобально по компании
        return None

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user and getattr(request.user, "company_id", None):
            validated_data["company"] = request.user.company
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        if request and request.user and getattr(request.user, "company_id", None):
            validated_data["company"] = request.user.company
            validated_data["branch"] = self._auto_branch()
        return super().update(instance, validated_data)


# ─── CASHFLOW: внутри кассы (вложенный) ────────────────────
class CashFlowInsideCashboxSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashFlow
        fields = [
            'id',
            'type',
            'name',
            'amount',
            'status',
            'created_at',
            'source_cashbox_flow_id',
            'source_business_operation_id',
        ]


# ─── CASHBOX: с вложенными CashFlow ────────────────────────
class CashboxWithFlowsSerializer(CompanyBranchReadOnlyMixin):
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        allow_null=True,
        required=False,
    )
    department_name = serializers.SerializerMethodField()

    # Вариант А (как у тебя сейчас): просто все связанные CashFlow
    cashflows = CashFlowInsideCashboxSerializer(source='flows', many=True, read_only=True)

    # Если нужно только утверждённые операции, то вместо строки выше можно:
    # cashflows = serializers.SerializerMethodField()
    #
    # def get_cashflows(self, obj):
    #     qs = obj.flows.filter(status=CashFlow.Status.APPROVED)
    #     return CashFlowInsideCashboxSerializer(qs, many=True).data

    # поле-флаг кассы
    is_consumption = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cashbox
        fields = [
            'id',
            'company',
            'branch',
            'department',
            'department_name',
            'name',
            'is_consumption',
            'cashflows',
        ]
        read_only_fields = [
            'id',
            'company',
            'branch',
            'department_name',
            'cashflows',
            'is_consumption',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ограничим доступные отделы: только этой компании и либо глобальные, либо текущего филиала
        request = self.context.get('request')
        if request and getattr(request.user, 'company_id', None):
            target_branch = self._auto_branch()
            qs = Department.objects.filter(company_id=request.user.company_id)
            if target_branch is not None:
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=target_branch))
            else:
                qs = qs.filter(branch__isnull=True)
            self.fields['department'].queryset = qs

    def get_department_name(self, obj):
        return obj.department.name if obj.department else None

    def validate(self, attrs):
        dept = attrs.get('department') or getattr(self.instance, 'department', None)
        target_branch = self._auto_branch()
        request = self.context.get('request')

        if dept and target_branch is not None and dept.branch_id not in (None, getattr(target_branch, "id", None)):
            raise serializers.ValidationError('Отдел принадлежит другому филиалу.')

        if dept and request and dept.company_id != request.user.company_id:
            raise serializers.ValidationError('Отдел должен принадлежать вашей компании.')

        return attrs


# ─── CASHBOX: краткий (для Department / списков) ───────────
class CashboxSerializer(CompanyBranchReadOnlyMixin):
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        allow_null=True,
        required=False,
    )
    department_name = serializers.SerializerMethodField()
    analytics = serializers.SerializerMethodField()
    is_consumption = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cashbox
        fields = [
            'id',
            'company',
            'branch',
            'department',
            'department_name',
            'name',
            'is_consumption',
            'analytics',
        ]
        read_only_fields = [
            'id',
            'company',
            'branch',
            'department_name',
            'analytics',
            'is_consumption',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        request = self.context.get('request')
        if request and getattr(request.user, 'company_id', None):
            target_branch = self._auto_branch()
            qs = Department.objects.filter(company_id=request.user.company_id)
            if target_branch is not None:
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=target_branch))
            else:
                qs = qs.filter(branch__isnull=True)
            self.fields['department'].queryset = qs

    def get_department_name(self, obj):
        return obj.department.name if obj.department else None

    def get_analytics(self, obj):
        # вызывает Cashbox.get_summary() из модели
        return obj.get_summary()

    def validate(self, attrs):
        dept = attrs.get('department') or getattr(self.instance, 'department', None)
        target_branch = self._auto_branch()
        request = self.context.get('request')

        if dept and request and dept.company_id != request.user.company_id:
            raise serializers.ValidationError('Отдел должен принадлежать вашей компании.')

        if dept and target_branch is not None and dept.branch_id not in (None, getattr(target_branch, "id", None)):
            raise serializers.ValidationError('Отдел принадлежит другому филиалу.')

        return attrs


# ─── CASHFLOW: основной ────────────────────────────────────
class CashFlowSerializer(CompanyBranchReadOnlyMixin):
    cashbox = serializers.PrimaryKeyRelatedField(queryset=Cashbox.objects.all())
    cashbox_name = serializers.SerializerMethodField()

    class Meta:
        model = CashFlow
        fields = [
            'id',
            'company',
            'branch',
            'cashbox',
            'cashbox_name',
            'type',
            'name',
            'amount',
            'created_at',
            'status',
            'source_cashbox_flow_id',
            'source_business_operation_id',
        ]
        read_only_fields = [
            'id',
            'created_at',
            'cashbox_name',
            'company',
            'branch',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ограничиваем доступные кассы:
        # - только из моей компании
        # - только глобальные кассы (branch is NULL) или кассы моего филиала
        request = self.context.get('request')
        if request and getattr(request.user, 'company_id', None):
            target_branch = self._auto_branch()
            qs = Cashbox.objects.filter(company_id=request.user.company_id)
            if target_branch is not None:
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=target_branch))
            else:
                qs = qs.filter(branch__isnull=True)
            self.fields['cashbox'].queryset = qs

    def get_cashbox_name(self, obj):
        if obj.cashbox.department:
            return obj.cashbox.department.name
        return obj.cashbox.name or "Свободная касса"

    def validate(self, attrs):
        request = self.context.get('request')
        cashbox = attrs.get('cashbox') or getattr(self.instance, 'cashbox', None)
        target_branch = self._auto_branch()

        if cashbox and request and cashbox.company_id != request.user.company_id:
            raise serializers.ValidationError('Касса должна принадлежать вашей компании.')

        if cashbox and target_branch is not None and cashbox.branch_id not in (None, getattr(target_branch, "id", None)):
            raise serializers.ValidationError('Касса принадлежит другому филиалу.')

        return attrs


# ─── DEPARTMENT: основной ──────────────────────────────────
class DepartmentSerializer(CompanyBranchReadOnlyMixin):
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
            'id',
            'name',
            'company',
            'branch',
            'color',
            'employees',
            'employees_data',
            'cashbox',
            'analytics',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'created_at',
            'company',
            'branch',
            'cashbox',
            'employees',
            'analytics',
        ]

    def _assign_employees_and_permissions(self, department, employees_data):
        # локальный импорт, чтобы не ловить циклический импорт
        from apps.users.models import User

        # список разрешений, которые мы разрешаем обновлять пачкой
        permission_fields = [
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services',
            'can_view_debts',

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
            'can_view_cafe_cook', 'can_view_cafe_inventory',

            # школа / CRM
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent', 'can_view_catalog', 'can_view_branch'
        ]

        for entry in employees_data:
            user_id = entry.get("id")
            try:
                user = User.objects.get(id=user_id, company=department.company)
            except User.DoesNotExist:
                continue

            # привязка к отделу
            department.employees.add(user)

            # массовое проставление флагов-доступов
            for field in permission_fields:
                if field in entry:
                    setattr(user, field, entry[field])

            user.save()

    def create(self, validated_data):
        employees_data = validated_data.pop('employees_data', [])
        obj = super().create(validated_data)  # company/branch установит миксин
        if employees_data:
            self._assign_employees_and_permissions(obj, employees_data)
        return obj

    def update(self, instance, validated_data):
        employees_data = validated_data.pop('employees_data', None)
        obj = super().update(instance, validated_data)  # company/branch обновит миксин

        # если employees_data пришёл вообще (включая пустой список) —
        # мы пересобираем состав отдела
        if employees_data is not None:
            instance.employees.clear()
            if employees_data:
                self._assign_employees_and_permissions(instance, employees_data)
        return obj

    def get_analytics(self, obj):
        return obj.cashflow_summary()


# ─── DEPARTMENT: только аналитика ──────────────────────────
class DepartmentAnalyticsSerializer(serializers.ModelSerializer):
    analytics = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'name', 'analytics']

    def get_analytics(self, obj):
        return obj.cashflow_summary()
