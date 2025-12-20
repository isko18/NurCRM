from decimal import Decimal

from django.db.models import Q
from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.users.models import Branch

User = get_user_model()


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
def _get_company_from_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None

    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company

    br = getattr(user, "branch", None)
    if br is not None:
        return getattr(br, "company", None)

    memberships = getattr(user, "branch_memberships", None)
    if memberships is not None:
        m = memberships.select_related("branch__company").first()
        if m and m.branch and m.branch.company:
            return m.branch.company

    return None


def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    if getattr(user, "owned_company", None):
        return True

    if getattr(user, "is_admin", False):
        return True

    role = getattr(user, "role", None)
    if role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор"):
        return True

    return False


def _fixed_branch_from_user(user, company):
    if not user or not company:
        return None

    company_id = getattr(company, "id", None)

    memberships = getattr(user, "branch_memberships", None)
    if memberships is not None:
        m = (
            memberships.filter(is_primary=True, branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if m and m.branch:
            return m.branch

        m = (
            memberships.filter(branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if m and m.branch:
            return m.branch

    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                return val
        except Exception:
            pass

    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        return primary

    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        try:
            return Branch.objects.get(id=branch_ids[0], company_id=company_id)
        except Branch.DoesNotExist:
            pass

    return None


def _resolve_branch_for_request(request):
    if not request:
        return None

    user = getattr(request, "user", None)
    company = _get_company_from_user(user)
    if not company:
        return None
    company_id = getattr(company, "id", None)

    fixed = _fixed_branch_from_user(user, company)
    if fixed is not None and not _is_owner_like(user):
        return fixed

    branch_id = request.query_params.get("branch") if hasattr(request, "query_params") else request.GET.get("branch")
    if branch_id:
        try:
            return Branch.objects.get(id=branch_id, company_id=company_id)
        except (Branch.DoesNotExist, ValueError):
            pass

    if hasattr(request, "branch"):
        b = getattr(request, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    return None


# ─────────────────────────────────────────────────────────────
# base serializer mixin
# ─────────────────────────────────────────────────────────────
class CompanyBranchReadOnlyMixin(serializers.ModelSerializer):
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
        user = getattr(request, "user", None) if request else None

        if user:
            company = _get_company_from_user(user)
            if company is not None:
                validated_data["company"] = company

            br = self._auto_branch()
            if br is not None:
                validated_data["branch"] = br

        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        if user:
            company = _get_company_from_user(user)
            if company is not None:
                validated_data["company"] = company

            br = self._auto_branch()
            if br is not None:
                validated_data["branch"] = br

        return super().update(instance, validated_data)


# ─────────────────────────────────────────────────────────────
# CashShift
# ─────────────────────────────────────────────────────────────
class CashShiftListSerializer(serializers.ModelSerializer):
    cashbox_name = serializers.SerializerMethodField()
    cashier_display = serializers.SerializerMethodField()

    expected_cash = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    cash_diff = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = CashShift
        fields = [
            "id",
            "company",
            "branch",
            "cashbox",
            "cashbox_name",
            "cashier",
            "cashier_display",
            "status",
            "opened_at",
            "closed_at",
            "opening_cash",
            "closing_cash",
            "income_total",
            "expense_total",
            "sales_count",
            "sales_total",
            "cash_sales_total",
            "noncash_sales_total",
            "expected_cash",
            "cash_diff",
        ]
        read_only_fields = fields

    def get_cashbox_name(self, obj):
        if obj.cashbox and obj.cashbox.branch:
            return f"Касса филиала {obj.cashbox.branch.name}"
        return getattr(obj.cashbox, "name", None) or "Касса"

    def get_cashier_display(self, obj):
        u = obj.cashier
        if not u:
            return None
        return (
            getattr(u, "get_full_name", lambda: "")()
            or getattr(u, "email", None)
            or getattr(u, "username", None)
        )

    def to_representation(self, obj):
        data = super().to_representation(obj)

        # ✅ OPEN: live-цифры без записи в БД
        if obj.status == CashShift.Status.OPEN:
            t = obj.calc_live_totals()

            data["income_total"] = str(t["income_total"])
            data["expense_total"] = str(t["expense_total"])
            data["sales_count"] = int(t["sales_count"])
            data["sales_total"] = str(t["sales_total"])
            data["cash_sales_total"] = str(t["cash_sales_total"])
            data["noncash_sales_total"] = str(t["noncash_sales_total"])

            data["expected_cash"] = str(t["expected_cash"])
            data["cash_diff"] = "0.00"

        return data


class CashShiftOpenSerializer(serializers.ModelSerializer):
    """
    ✅ Разрешаем несколько OPEN смен на одну кассу.
    ✅ Запрещаем только повторную OPEN смену этому же кассиру на этой кассе.
    """

    cashier = serializers.PrimaryKeyRelatedField(required=False, allow_null=True, queryset=User.objects.none())
    cashbox = serializers.PrimaryKeyRelatedField(queryset=Cashbox.objects.none())

    class Meta:
        model = CashShift
        fields = ["id", "cashbox", "cashier", "opening_cash"]
        read_only_fields = ["id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company = _get_company_from_user(user)

        # cashiers
        if company:
            self.fields["cashier"].queryset = User.objects.filter(company=company)
        else:
            self.fields["cashier"].queryset = User.objects.none()

        # cashboxes: строго по выбранному branch
        if company:
            target_branch = _resolve_branch_for_request(request) if request else None
            qs = Cashbox.objects.filter(company=company)

            if target_branch is not None:
                qs = qs.filter(branch=target_branch)
            else:
                qs = qs.filter(branch__isnull=True)

            self.fields["cashbox"].queryset = qs
        else:
            self.fields["cashbox"].queryset = Cashbox.objects.none()

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company = _get_company_from_user(user)

        cashbox = attrs.get("cashbox")
        if not (company and cashbox):
            raise serializers.ValidationError("Нет company/cashbox.")

        if cashbox.company_id != company.id:
            raise serializers.ValidationError({"cashbox": "Касса другой компании."})

        # cashier
        chosen_cashier = attrs.get("cashier") or None
        if chosen_cashier is None:
            if not user:
                raise serializers.ValidationError({"cashier": "Нужен кассир."})
            attrs["cashier"] = user
        else:
            if not _is_owner_like(user) and chosen_cashier.id != getattr(user, "id", None):
                raise serializers.ValidationError({"cashier": "Нельзя открыть смену на другого кассира."})

        cashier = attrs["cashier"]

        # ✅ теперь проверяем только "есть ли уже OPEN смена этого кассира на этой кассе"
        existing = (
            CashShift.objects
            .select_for_update()
            .filter(company=company, cashbox=cashbox, cashier=cashier, status=CashShift.Status.OPEN)
            .order_by("-opened_at")
            .first()
        )
        if existing:
            attrs["_existing_shift"] = existing

        return attrs

    def create(self, validated_data):
        existing = validated_data.pop("_existing_shift", None)
        if existing:
            return existing

        cashbox = validated_data["cashbox"]
        cashier = validated_data["cashier"]

        return CashShift.objects.create(
            company=cashbox.company,
            branch=cashbox.branch,
            cashbox=cashbox,
            cashier=cashier,
            opening_cash=validated_data.get("opening_cash") or Decimal("0.00"),
            status=CashShift.Status.OPEN,
        )


class CashShiftCloseSerializer(serializers.Serializer):
    closing_cash = serializers.DecimalField(max_digits=12, decimal_places=2)

    def save(self, shift: CashShift):
        shift.close(self.validated_data["closing_cash"])
        return shift


# ─────────────────────────────────────────────────────────────
# Cashbox
# ─────────────────────────────────────────────────────────────
class CashFlowInsideCashboxSerializer(serializers.ModelSerializer):
    cashier_display = serializers.SerializerMethodField()

    class Meta:
        model = CashFlow
        fields = [
            "id",
            "type",
            "name",
            "amount",
            "status",
            "created_at",
            "source_cashbox_flow_id",
            "source_business_operation_id",
            "shift",
            "cashier",
            "cashier_display",
        ]

    def get_cashier_display(self, obj):
        u = getattr(obj, "cashier", None)
        if not u:
            return None
        return (
            getattr(u, "get_full_name", lambda: "")()
            or getattr(u, "email", None)
            or getattr(u, "username", None)
        )


class CashboxWithFlowsSerializer(CompanyBranchReadOnlyMixin):
    cashflows = CashFlowInsideCashboxSerializer(source="flows", many=True, read_only=True)
    is_consumption = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cashbox
        fields = ["id", "company", "branch", "name", "is_consumption", "cashflows"]
        read_only_fields = ["id", "company", "branch", "cashflows", "is_consumption"]


class CashboxSerializer(CompanyBranchReadOnlyMixin):
    analytics = serializers.SerializerMethodField()
    is_consumption = serializers.BooleanField(read_only=True)

    class Meta:
        model = Cashbox
        fields = ["id", "company", "branch", "name", "is_consumption", "analytics"]
        read_only_fields = ["id", "company", "branch", "analytics", "is_consumption"]
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not data.get("name"):
            data["name"] = "Основная касса компании"
        return data
    def get_analytics(self, obj):
        amap = self.context.get("analytics_map")
        if amap is not None:
            a = amap.get(str(obj.id))
            if a is None:
                return {
                    "income_total": "0.00",
                    "expense_total": "0.00",
                    "sales_count": 0,
                    "sales_total": "0.00",
                    "cash_sales_total": "0.00",
                    "noncash_sales_total": "0.00",
                    "open_shift_expected_cash": None,
                }

            def _d(v):
                return str(v) if isinstance(v, Decimal) else v

            return {
                "income_total": _d(a.get("income_total")),
                "expense_total": _d(a.get("expense_total")),
                "sales_count": int(a.get("sales_count") or 0),
                "sales_total": _d(a.get("sales_total")),
                "cash_sales_total": _d(a.get("cash_sales_total")),
                "noncash_sales_total": _d(a.get("noncash_sales_total")),
                "open_shift_expected_cash": _d(a.get("open_shift_expected_cash")),
            }

        return obj.get_summary()


# ─────────────────────────────────────────────────────────────
# CashFlow
# ─────────────────────────────────────────────────────────────
class CashFlowSerializer(CompanyBranchReadOnlyMixin):
    cashbox = serializers.PrimaryKeyRelatedField(queryset=Cashbox.objects.all())
    cashbox_name = serializers.SerializerMethodField()

    shift = serializers.PrimaryKeyRelatedField(queryset=CashShift.objects.all(), required=False, allow_null=True)
    cashier = serializers.ReadOnlyField(source="cashier.id")
    cashier_display = serializers.SerializerMethodField()

    class Meta:
        model = CashFlow
        fields = [
            "id",
            "company",
            "branch",
            "cashbox",
            "cashbox_name",
            "type",
            "name",
            "amount",
            "created_at",
            "status",
            "source_cashbox_flow_id",
            "source_business_operation_id",
            "shift",
            "cashier",
            "cashier_display",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "cashbox_name",
            "company",
            "branch",
            "cashier",
            "cashier_display",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        request = self.context.get("request")
        if not request:
            return

        user = getattr(request, "user", None)
        company = _get_company_from_user(user)
        if not company:
            self.fields["cashbox"].queryset = Cashbox.objects.none()
            self.fields["shift"].queryset = CashShift.objects.none()
            return

        target_branch = self._auto_branch()

        cb_qs = Cashbox.objects.filter(company=company)
        if target_branch is not None:
            cb_qs = cb_qs.filter(Q(branch__isnull=True) | Q(branch=target_branch))
        self.fields["cashbox"].queryset = cb_qs

        # shifts: owner видит все, обычный пользователь — только свои
        sh_qs = CashShift.objects.filter(company=company)
        if not _is_owner_like(user):
            sh_qs = sh_qs.filter(cashier=user)
        self.fields["shift"].queryset = sh_qs
    def get_name(self, obj):
        return obj.name or "Основная касса компании"
    def get_cashbox_name(self, obj):
        if obj.cashbox and obj.cashbox.branch:
            return f"Касса филиала {obj.cashbox.branch.name}"
        return getattr(obj.cashbox, "name", None) or f"Касса компании {obj.company.name}"

    def get_cashier_display(self, obj):
        u = getattr(obj, "cashier", None)
        if not u:
            return None
        return (
            getattr(u, "get_full_name", lambda: "")()
            or getattr(u, "email", None)
            or getattr(u, "username", None)
        )

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        cashbox = attrs.get("cashbox") or getattr(self.instance, "cashbox", None)
        shift = attrs.get("shift") if "shift" in attrs else getattr(self.instance, "shift", None)

        if request and cashbox:
            company = _get_company_from_user(user)
            if cashbox.company_id != getattr(company, "id", None):
                raise serializers.ValidationError("Касса должна принадлежать вашей компании.")

        target_branch = self._auto_branch()
        if cashbox and target_branch is not None and cashbox.branch_id not in (None, getattr(target_branch, "id", None)):
            raise serializers.ValidationError("Касса принадлежит другому филиалу.")

        if shift:
            if cashbox and shift.cashbox_id != cashbox.id:
                raise serializers.ValidationError({"shift": "Смена относится к другой кассе."})

            # ✅ в кассовых движениях нормальный смысл — только OPEN смена
            if shift.status != CashShift.Status.OPEN:
                raise serializers.ValidationError({"shift": "Нельзя делать движение по закрытой смене."})

            if user and (not _is_owner_like(user)) and shift.cashier_id != user.id:
                raise serializers.ValidationError({"shift": "Это не ваша смена."})

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        cashbox = validated_data.get("cashbox")
        shift = validated_data.get("shift", None)

        # ✅ shift не передали — цепляем OPEN смену именно ЭТОГО пользователя в этой кассе
        if not shift and cashbox:
            if not user:
                raise serializers.ValidationError({"shift": "Нужен пользователь для выбора смены."})

            open_shift = (
                CashShift.objects
                .filter(
                    cashbox=cashbox,
                    status=CashShift.Status.OPEN,
                    cashier=user,
                )
                .order_by("-opened_at")
                .first()
            )

            if not open_shift:
                raise serializers.ValidationError({"shift": "У вас нет открытой смены на этой кассе."})

            validated_data["shift"] = open_shift

        if user and "cashier" not in validated_data:
            validated_data["cashier"] = user

        return super().create(validated_data)


class CashFlowBulkStatusItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=CashFlow.Status.choices)


class CashFlowBulkStatusSerializer(serializers.Serializer):
    items = CashFlowBulkStatusItemSerializer(many=True)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("Пустой список.")
        if len(items) > 50000:
            raise serializers.ValidationError("Слишком много. Максимум 50 000 за раз.")
        return items
