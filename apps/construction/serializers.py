from rest_framework import serializers
from django.db.models import Q

from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.serializers import UserWithPermissionsSerializer
from apps.users.models import Branch  # нужен для ?branch=... в _auto_branch


# ───────────────────────────────────────────────────────────
# Общие helpers, синхронные с views
# ───────────────────────────────────────────────────────────

def _get_company_from_user(user):
    """Компания текущего пользователя (owner/company, fallback через user.branch)."""
    if not user or not getattr(user, "is_authenticated", False):
        return None

    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company

    br = getattr(user, "branch", None)
    if br is not None:
        return getattr(br, "company", None)

    return None


def _is_owner_like(user) -> bool:
    """
    Владелец / админ / суперюзер – им позволяем переключаться между филиалами
    и не привязываем к «жёсткому» филиалу.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(user, "role", None)
    if role in ("owner", "admin"):
        return True
    if getattr(user, "owned_company", None):
        return True
    return False


def _fixed_branch_from_user(user, company):
    """
    «Жёстко» назначенный филиал сотрудника (используем только для НЕ owner-like):
      - user.primary_branch() / user.primary_branch
      - user.branch
      - единственный branch из user.branch_ids
    """
    if not user or not company:
        return None

    company_id = getattr(company, "id", None)

    # 1) primary_branch: метод или атрибут
    primary = getattr(user, "primary_branch", None)

    # 1a) как метод
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                return val
        except Exception:
            pass

    # 1b) как свойство
    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        return primary

    # 2) user.branch
    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    # 3) единственный филиал из branch_ids
    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        try:
            return Branch.objects.get(id=branch_ids[0], company_id=company_id)
        except Branch.DoesNotExist:
            pass

    return None


def _resolve_branch_for_request(request):
    """
    Аналог _get_active_branch из views, но без жёстких сайд-эффектов.
    Логика:
      1) если пользователь НЕ owner-like — возвращаем его фиксированный филиал (если есть),
         ?branch игнорируем.
      2) если пользователь owner-like ИЛИ фиксированного филиала нет:
           2.0) пробуем ?branch=<uuid> (филиал компании пользователя)
           2.1) иначе request.branch (если уже стоит и той же компании)
           2.2) иначе None.
    """
    if not request:
        return None

    user = getattr(request, "user", None)
    company = _get_company_from_user(user)
    if not company:
        return None

    company_id = getattr(company, "id", None)

    # 1) фиксированный филиал для НЕ owner-like
    fixed = _fixed_branch_from_user(user, company)
    if fixed is not None and not _is_owner_like(user):
        return fixed

    # 2) owner/admin или сотрудник без фиксированного филиала → можно ?branch
    branch_id = None
    if hasattr(request, "query_params"):
        branch_id = request.query_params.get("branch")
    elif hasattr(request, "GET"):
        branch_id = request.GET.get("branch")

    if branch_id:
        try:
            br = Branch.objects.get(id=branch_id, company_id=company_id)
            return br
        except (Branch.DoesNotExist, ValueError):
            pass

    # 3) request.branch, если уже стоит и той же компании
    if hasattr(request, "branch"):
        b = getattr(request, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    return None


# ───────────────────────────────────────────────────────────
# Общий миксин: company/branch
# ───────────────────────────────────────────────────────────
class CompanyBranchReadOnlyMixin(serializers.ModelSerializer):
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.

    Порядок получения branch (с учётом ролей):
      1) для НЕ owner-like → фиксированный филиал пользователя (primary/branch/branch_ids), ?branch игнорируется
      2) для owner/admin/superuser или сотрудника без фиксированного филиала:
           2.0) ?branch=<uuid> (если filial принадлежит компании пользователя)
           2.1) request.branch (если middleware это положил и филиал той же компании)
           2.2) иначе None (глобальная запись компании)

    При create/update:
      - company всегда берётся из пользователя;
      - branch подставляется только если _auto_branch() вернул не None.
        Это соответствует поведению во views: не перетираем branch на обновлении.
    """
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    def _auto_branch(self):
        request = self.context.get("request")
        if not request:
            return None

        user = getattr(request, "user", None)
        company = _get_company_from_user(user)
        if not company:
            return None

        return _resolve_branch_for_request(request)

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user:
            company = _get_company_from_user(request.user)
            if company is not None:
                validated_data["company"] = company

            branch = self._auto_branch()
            if branch is not None:
                validated_data["branch"] = branch

        return super().create(validated_data)

    def update(self, instance, validated_data):
        """
        company фиксируем, branch не трогаем, если _auto_branch() вернул None.
        """
        request = self.context.get("request")
        if request and request.user:
            company = _get_company_from_user(request.user)
            if company is not None:
                validated_data["company"] = company

            branch = self._auto_branch()
            if branch is not None:
                validated_data["branch"] = branch

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

    # все связанные CashFlow
    cashflows = CashFlowInsideCashboxSerializer(source='flows', many=True, read_only=True)

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
        if request:
            company = _get_company_from_user(getattr(request, "user", None))
            if company:
                target_branch = self._auto_branch()
                qs = Department.objects.filter(company=company)
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

        if dept and request:
            company = _get_company_from_user(request.user)
            if dept.company_id != getattr(company, "id", None):
                raise serializers.ValidationError('Отдел должен принадлежать вашей компании.')

        if dept and target_branch is not None and dept.branch_id not in (None, getattr(target_branch, "id", None)):
            raise serializers.ValidationError('Отдел принадлежит другому филиалу.')

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
        if request:
            company = _get_company_from_user(getattr(request, "user", None))
            if company:
                target_branch = self._auto_branch()
                qs = Department.objects.filter(company=company)
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

        if dept and request:
            company = _get_company_from_user(request.user)
            if dept.company_id != getattr(company, "id", None):
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
        # - глобальные (branch is NULL) или кассы моего филиала
        request = self.context.get('request')
        if request:
            company = _get_company_from_user(getattr(request, "user", None))
            if company:
                target_branch = self._auto_branch()
                qs = Cashbox.objects.filter(company=company)
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

        if cashbox and request:
            company = _get_company_from_user(request.user)
            if cashbox.company_id != getattr(company, "id", None):
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
