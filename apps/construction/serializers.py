from decimal import Decimal

from django.db.models import Q
from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.construction.models import Cashbox, CashFlow, CashFlowCategory, CashShift
from apps.users.models import Branch

from apps.construction.utils import (
    get_company_from_user as _get_company_from_user,
    is_owner_like as _is_owner_like,
    fixed_branch_from_user as _fixed_branch_from_user,
    get_active_branch,
)

User = get_user_model()


def _resolve_branch_for_request(request):
    """Обертка для get_active_branch для совместимости."""
    return get_active_branch(request)


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
    payment_breakdown = serializers.SerializerMethodField()

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
            "payment_breakdown",
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

    def get_payment_breakdown(self, obj):
        return obj.calc_payment_breakdown()

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
    MAX_OPEN_COMPANY_SHIFTS = 3

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
            self.fields["cashier"].queryset = User.objects.filter(Q(company=company) | Q(owned_company=company))
        else:
            self.fields["cashier"].queryset = User.objects.none()

        # cashboxes: кассы компании
        if company:
            target_branch = _resolve_branch_for_request(request) if request else None
            qs = Cashbox.objects.filter(company=company)

            if target_branch is not None and not _is_owner_like(user):
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=target_branch))

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

        open_shifts_count = (
            CashShift.objects
            .filter(company=company, status=CashShift.Status.OPEN)
            .count()
        )
        if open_shifts_count >= self.MAX_OPEN_COMPANY_SHIFTS:
            raise serializers.ValidationError(
                {"shift": f"В компании уже открыто {self.MAX_OPEN_COMPANY_SHIFTS} смены. Закройте одну смену перед открытием новой."}
            )

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
    category_title = serializers.CharField(source="category.title", read_only=True, default="")

    def get_cashier_display(self, obj):
        u = getattr(obj, "cashier", None)
        if not u:
            return None
        return (
            getattr(u, "get_full_name", lambda: "")()
            or getattr(u, "email", None)
            or getattr(u, "username", None)
        )

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
            "category",
            "category_title",
        ]
        read_only_fields = fields


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
    balance = serializers.SerializerMethodField()
    current_balance = serializers.SerializerMethodField()
    currency = serializers.CharField(default="KGS", read_only=True)
    is_active = serializers.BooleanField(default=True, read_only=True)

    class Meta:
        model = Cashbox
        fields = [
            "id", "company", "branch", "name",
            "is_consumption", "balance", "current_balance", "currency", "is_active",
            "analytics",
        ]
        read_only_fields = [
            "id", "company", "branch", "analytics", "is_consumption",
            "balance", "current_balance", "currency", "is_active",
        ]

    def get_balance(self, obj):
        amap = self.context.get("analytics_map")
        if amap and str(obj.id) in amap:
            a = amap[str(obj.id)]
            inc = Decimal(str(a.get("income_total") or "0.00"))
            exp = Decimal(str(a.get("expense_total") or "0.00"))
            return f"{(inc - exp):.2f}"
        inc = obj.flows.filter(status="approved", type="income").aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        exp = obj.flows.filter(status="approved", type="expense").aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        return f"{(inc - exp):.2f}"

    def get_current_balance(self, obj):
        return self.get_balance(obj)
    def to_representation(self, instance):
        data = super().to_representation(instance)

        # если name пустой — проверяем, первая ли это касса компании
        if not data.get("name"):
            first_cashbox_id = (
                Cashbox.objects
                .filter(company=instance.company)
                .order_by("created_at")
                .values_list("id", flat=True)
                .first()
            )

            if instance.id == first_cashbox_id:
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
# CashFlowCategory
# ─────────────────────────────────────────────────────────────
class CashFlowCategorySerializer(CompanyBranchReadOnlyMixin):
    """Категория опционально привязана к филиалу: branch=null — на всю компанию."""

    branch = serializers.PrimaryKeyRelatedField(
        queryset=Branch.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = CashFlowCategory
        fields = ["id", "company", "branch", "title", "created_at"]
        read_only_fields = ["id", "company", "created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company = _get_company_from_user(user) if user else None
        if company and "branch" in self.fields:
            self.fields["branch"].queryset = Branch.objects.filter(company=company)

    def validate_title(self, value):
        v = (value or "").strip()
        if not v:
            raise serializers.ValidationError("Укажите название категории.")
        return v

    def validate(self, attrs):
        br = attrs.get("branch")
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company = _get_company_from_user(user) if user else None
        if br is not None and company and br.company_id != company.id:
            raise serializers.ValidationError({"branch": "Филиал другой компании."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if user:
            company = _get_company_from_user(user)
            if company is not None:
                validated_data["company"] = company
        if "branch" not in validated_data:
            validated_data["branch"] = self._auto_branch()
        return serializers.ModelSerializer.create(self, validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if user:
            company = _get_company_from_user(user)
            if company is not None:
                validated_data["company"] = company
        return serializers.ModelSerializer.update(self, instance, validated_data)


# ─────────────────────────────────────────────────────────────
# CashFlow
# ─────────────────────────────────────────────────────────────
class CashFlowSerializer(CompanyBranchReadOnlyMixin):
    cashbox = serializers.PrimaryKeyRelatedField(queryset=Cashbox.objects.all())
    cashbox_name = serializers.SerializerMethodField()

    shift = serializers.PrimaryKeyRelatedField(
        queryset=CashShift.objects.all(),
        required=False,
        allow_null=True,
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=CashFlowCategory.objects.all(),
        required=False,
        allow_null=True,
    )
    category_title = serializers.CharField(source="category.title", read_only=True, default="")

    cashier = serializers.ReadOnlyField(source="cashier.id")
    cashier_display = serializers.SerializerMethodField()
    # Алиас для аналитики Производства (фронт читает user_name|created_by_name|...).
    user_name = serializers.SerializerMethodField()

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
            "source_kind",
            "source_id",
            "shift",
            "category",
            "category_title",
            "cashier",
            "cashier_display",
            "user_name",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "cashbox_name",
            "company",
            "branch",
            "cashier",
            "cashier_display",
            "user_name",
            "category_title",
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

        cat_qs = CashFlowCategory.objects.filter(company=company)
        if target_branch is not None:
            cat_qs = cat_qs.filter(Q(branch__isnull=True) | Q(branch=target_branch))
        self.fields["category"].queryset = cat_qs

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

    def get_user_name(self, obj):
        # Тот же автор, что и cashier_display — отдельный ключ для аналитики Производства.
        return self.get_cashier_display(obj)

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        cashbox = attrs.get("cashbox") or getattr(self.instance, "cashbox", None)

        category = attrs.get("category") if "category" in attrs else getattr(self.instance, "category", None)
        if category is not None:
            company = _get_company_from_user(user) if user else None
            if company and category.company_id != company.id:
                raise serializers.ValidationError({"category": "Категория другой компании."})
            if cashbox:
                cb_br = cashbox.branch_id
                cat_br = category.branch_id
                if cat_br is not None and cat_br != cb_br:
                    raise serializers.ValidationError(
                        {"category": "Категория привязана к другому филиалу. Выберите общую категорию или категорию этого филиала."}
                    )

        # amount: в БД стоит CheckConstraint amount__gt=0.
        # На фронте часто отправляют отрицательное значение для "расхода" —
        # нормализуем и сохраняем всегда положительное значение.
        if "amount" in attrs:
            amount = attrs.get("amount")
            if amount is None:
                raise serializers.ValidationError({"amount": "Обязательное поле."})
            try:
                amount = abs(amount)
            except Exception:
                raise serializers.ValidationError({"amount": "Некорректное значение суммы."})
            if amount <= 0:
                raise serializers.ValidationError({"amount": "Сумма должна быть больше 0."})
            attrs["amount"] = amount

        # важно: различаем "shift не передали" и "shift=None"
        shift_provided = "shift" in attrs
        shift = attrs.get("shift") if shift_provided else getattr(self.instance, "shift", None)

        if request and cashbox:
            company = _get_company_from_user(user)
            if cashbox.company_id != getattr(company, "id", None):
                raise serializers.ValidationError("Касса должна принадлежать вашей компании.")

        target_branch = self._auto_branch()
        if cashbox and target_branch is not None and cashbox.branch_id not in (None, getattr(target_branch, "id", None)):
            raise serializers.ValidationError("Касса принадлежит другому филиалу.")

        # ✅ строгие проверки ТОЛЬКО если shift передали и он не None
        if shift_provided and shift is not None:
            if cashbox and shift.cashbox_id != cashbox.id:
                raise serializers.ValidationError({"shift": "Смена относится к другой кассе."})

            if shift.status != CashShift.Status.OPEN:
                raise serializers.ValidationError({"shift": "Нельзя делать движение по закрытой смене."})

            if user and (not _is_owner_like(user)) and shift.cashier_id != user.id:
                raise serializers.ValidationError({"shift": "Это не ваша смена."})

        # ✅ если shift не передали или shift=None — это “общий режим”, разрешаем
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None

        # ✅ НИКАКОГО авто-поиска смены. Если shift нет — создаём общий cashflow.
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
