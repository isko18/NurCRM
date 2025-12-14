from decimal import Decimal
from rest_framework import serializers
from django.db.models import Q
from django.contrib.auth import get_user_model

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.users.models import Branch

User = get_user_model()


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
    role = getattr(user, "role", None)
    if role in ("owner", "admin"):
        return True
    if getattr(user, "owned_company", None):
        return True
    if getattr(user, "is_admin", False):
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
        m = memberships.filter(branch__company_id=company_id).select_related("branch").first()
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
        if request and request.user:
            company = _get_company_from_user(request.user)
            if company is not None:
                validated_data["company"] = company

            br = self._auto_branch()
            if br is not None:
                validated_data["branch"] = br

        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        if request and request.user:
            company = _get_company_from_user(request.user)
            if company is not None:
                validated_data["company"] = company

            br = self._auto_branch()
            if br is not None:
                validated_data["branch"] = br

        return super().update(instance, validated_data)


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
    ✅ 1 OPEN смена на 1 кассу.
    """

    cashier = serializers.PrimaryKeyRelatedField(required=False, allow_null=True, queryset=User.objects.none())
    cashbox = serializers.PrimaryKeyRelatedField(queryset=Cashbox.objects.all())

    class Meta:
        model = CashShift
        fields = ["id", "cashbox", "cashier", "opening_cash"]
        read_only_fields = ["id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company = _get_company_from_user(user)

        if company:
            self.fields["cashier"].queryset = User.objects.filter(company=company)
        else:
            self.fields["cashier"].queryset = User.objects.none()

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

        chosen_cashier = attrs.get("cashier") or None
        if chosen_cashier is None:
            attrs["cashier"] = user
        else:
            if not _is_owner_like(user) and chosen_cashier.id != user.id:
                raise serializers.ValidationError({"cashier": "Нельзя открыть смену на другого кассира."})

        cashier = attrs["cashier"]

        existing = (
            CashShift.objects
            .select_for_update()
            .select_related("cashier")
            .filter(company=company, cashbox=cashbox, status=CashShift.Status.OPEN)
            .first()
        )

        if existing:
            if existing.cashier_id == cashier.id:
                attrs["_existing_shift"] = existing
                return attrs

            who = (
                getattr(existing.cashier, "email", None)
                or getattr(existing.cashier, "username", None)
                or str(existing.cashier_id)
            )
            raise serializers.ValidationError({"cashbox": f"Касса уже открыта другим кассиром: {who}."})

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

    def get_analytics(self, obj):
        # ✅ если view передала пачкой — берём отсюда (быстро)
        amap = self.context.get("analytics_map")
        if amap:
            return amap.get(str(obj.id)) or {
                "income_total": "0.00",
                "expense_total": "0.00",
                "sales_count": 0,
                "sales_total": "0.00",
                "cash_sales_total": "0.00",
                "noncash_sales_total": "0.00",
                "open_shift_expected_cash": None,
            }

        # ✅ иначе (detail / старые вызовы) — считаем как раньше
        return obj.get_summary()


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

        sh_qs = CashShift.objects.filter(company=company)
        if not _is_owner_like(user):
            sh_qs = sh_qs.filter(cashier=user)
        self.fields["shift"].queryset = sh_qs

    def get_cashbox_name(self, obj):
        if obj.cashbox and obj.cashbox.branch:
            return f"Касса филиала {obj.cashbox.branch.name}"
        return obj.cashbox.name or f"Касса компании {obj.company.name}"

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
            if shift.status != CashShift.Status.OPEN:
                raise serializers.ValidationError({"shift": "Нельзя привязать движение к закрытой смене."})
            if user and (not _is_owner_like(user)) and shift.cashier_id != user.id:
                raise serializers.ValidationError({"shift": "Это не ваша смена."})

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        cashbox = validated_data.get("cashbox")
        shift = validated_data.get("shift", None)

        if not shift and cashbox:
            open_shift = (
                CashShift.objects
                .filter(cashbox=cashbox, status=CashShift.Status.OPEN)
                .order_by("-opened_at")
                .first()
            )
            if open_shift:
                if user and (not _is_owner_like(user)) and open_shift.cashier_id != user.id:
                    raise serializers.ValidationError({"cashbox": "Касса открыта другим кассиром. Нельзя делать движения."})
                validated_data["shift"] = open_shift

        if user and "cashier" not in validated_data:
            validated_data["cashier"] = user

        return super().create(validated_data)
