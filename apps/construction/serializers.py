from rest_framework import serializers
from django.db.models import Q

from apps.construction.models import Cashbox, CashFlow
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
            'name',
            'is_consumption',
            'cashflows',
        ]
        read_only_fields = [
            'id',
            'company',
            'branch',
            'cashflows',
            'is_consumption',
        ]


# ─── CASHBOX: краткий (для списков) ────────────────────────
class CashboxSerializer(CompanyBranchReadOnlyMixin):
    analytics = serializers.SerializerMethodField()
    is_consumption = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cashbox
        fields = [
            'id',
            'company',
            'branch',
            'name',
            'is_consumption',
            'analytics',
        ]
        read_only_fields = [
            'id',
            'company',
            'branch',
            'analytics',
            'is_consumption',
        ]

    def get_analytics(self, obj):
        # вызывает Cashbox.get_summary() из модели
        return obj.get_summary()


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
        if obj.cashbox.branch:
            return f"Касса филиала {obj.cashbox.branch.name}"
        return obj.cashbox.name or f"Касса компании {obj.company.name}"

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
