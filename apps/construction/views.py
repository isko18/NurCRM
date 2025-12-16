from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.shortcuts import get_object_or_404

from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.users.models import Branch

from apps.construction.serializers import (
    CashboxSerializer,
    CashFlowSerializer,
    CashboxWithFlowsSerializer,
    CashShiftListSerializer,
    CashShiftOpenSerializer,
    CashShiftCloseSerializer,
    CashFlowBulkStatusSerializer
)

# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
def _get_company(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None

    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company

    br = getattr(user, "branch", None)
    if br is not None and getattr(br, "company", None):
        return br.company

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
        primary_m = (
            memberships
            .filter(is_primary=True, branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if primary_m and primary_m.branch:
            return primary_m.branch

        any_m = (
            memberships
            .filter(branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if any_m and any_m.branch:
            return any_m.branch

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


def _get_active_branch(request):
    user = getattr(request, "user", None)
    company = _get_company(user)
    if not company:
        setattr(request, "branch", None)
        return None

    company_id = getattr(company, "id", None)

    if not _is_owner_like(user):
        fixed = _fixed_branch_from_user(user, company)
        setattr(request, "branch", fixed if fixed else None)
        return fixed if fixed else None

    branch_id = request.query_params.get("branch") if hasattr(request, "query_params") else request.GET.get("branch")
    if branch_id:
        try:
            br = Branch.objects.get(id=branch_id, company_id=company_id)
            setattr(request, "branch", br)
            return br
        except (Branch.DoesNotExist, ValueError):
            pass

    if hasattr(request, "branch"):
        b = getattr(request, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    setattr(request, "branch", None)
    return None


def _guess_sale_model():
    """
    Пытаемся найти модель Sale без жёсткого импорта:
    - должна иметь FK cashbox -> Cashbox
    - желательно поля status, total, payment_method, shift
    """
    candidates = []
    for m in apps.get_models():
        try:
            concrete = {f.name: f for f in m._meta.get_fields() if getattr(f, "concrete", False)}
        except Exception:
            continue

        if "cashbox" not in concrete:
            continue

        f = concrete["cashbox"]
        if not getattr(f, "is_relation", False):
            continue
        if getattr(f, "related_model", None) is not Cashbox:
            continue

        has_total = "total" in concrete
        has_status = "status" in concrete
        has_pm = "payment_method" in concrete
        has_shift = "shift" in concrete

        score = (10 if has_total else 0) + (10 if has_status else 0) + (5 if has_pm else 0) + (3 if has_shift else 0)

        name = (m.__name__ or "").lower()
        if "sale" in name:
            score += 3

        candidates.append((score, m))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else None


SALE_MODEL = None


def get_sale_model():
    """
    ✅ Важно: не вычислять на импорте файла.
    В dev autoreload/apps registry это реально ломает жизнь.
    """
    global SALE_MODEL
    if SALE_MODEL is None:
        SALE_MODEL = _guess_sale_model()
    return SALE_MODEL


def _choice_value(model, enum_name: str, member: str, fallback: str):
    """
    Достаём значение из TextChoices/Enum максимально мягко.
    """
    enum = getattr(model, enum_name, None)
    v = getattr(enum, member, None)
    return getattr(v, "value", None) or v or fallback


# ─────────────────────────────────────────────────────────────
# base mixin: company + branch scope
# ─────────────────────────────────────────────────────────────
class CompanyBranchScopedMixin:
    permission_classes = [permissions.IsAuthenticated]

    def _company(self):
        return _get_company(getattr(self.request, "user", None))

    def _active_branch(self):
        return _get_active_branch(self.request)

    def _model_has_field(self, queryset, field_name: str) -> bool:
        return field_name in {f.name for f in queryset.model._meta.concrete_fields}

    def _scoped_queryset(self, base_qs):
        if getattr(self, "swagger_fake_view", False):
            return base_qs.none()

        company = self._company()
        if not company:
            return base_qs.none()

        qs = base_qs
        if self._model_has_field(qs, "company"):
            qs = qs.filter(company=company)

        if self._model_has_field(qs, "branch"):
            br = self._active_branch()
            if br is not None:
                qs = qs.filter(branch=br)

        return qs

    def _inject_company_branch_on_save(self, serializer):
        company = self._company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        br = self._active_branch()

        model = getattr(getattr(serializer, "Meta", None), "model", None)
        kwargs = {}

        if model:
            model_fields = {f.name for f in model._meta.concrete_fields}
            if "company" in model_fields:
                kwargs["company"] = company
            if "branch" in model_fields and br is not None:
                kwargs["branch"] = br
        else:
            kwargs["company"] = company

        serializer.save(**kwargs)

    def perform_update(self, serializer):
        company = self._company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        model = getattr(getattr(serializer, "Meta", None), "model", None)
        kwargs = {}

        if model:
            model_fields = {f.name for f in model._meta.concrete_fields}
            if "company" in model_fields:
                kwargs["company"] = company
        else:
            kwargs["company"] = company

        serializer.save(**kwargs)


# ─────────────────────────────────────────────────────────────
# CASHBOXES
# ─────────────────────────────────────────────────────────────
class CashboxListCreateView(CompanyBranchScopedMixin, generics.ListCreateAPIView):
    queryset = Cashbox.objects.select_related("company", "branch")
    serializer_class = CashboxSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())

    def perform_create(self, serializer):
        self._inject_company_branch_on_save(serializer)

    def list(self, request, *args, **kwargs):
        """
        ✅ Ускорение: analytics считаем пачкой только для касс текущей страницы,
        и отдаём в serializer через context["analytics_map"].
        """
        z = Decimal("0.00")

        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        cashboxes = page if page is not None else list(qs)

        ids = [cb.id for cb in cashboxes]

        analytics_map = {
            str(cb_id): {
                "income_total": z,
                "expense_total": z,
                "sales_count": 0,
                "sales_total": z,
                "cash_sales_total": z,
                "noncash_sales_total": z,
                "open_shift_expected_cash": None,
            }
            for cb_id in ids
        }

        if ids:
            # ---- flows (approved) by cashbox ----
            flows = (
                CashFlow.objects
                .filter(cashbox_id__in=ids, status=CashFlow.Status.APPROVED)
                .values("cashbox_id")
                .annotate(
                    income=Sum("amount", filter=Q(type=CashFlow.Type.INCOME)),
                    expense=Sum("amount", filter=Q(type=CashFlow.Type.EXPENSE)),
                )
            )
            for r in flows:
                k = str(r["cashbox_id"])
                analytics_map[k]["income_total"] = r["income"] or z
                analytics_map[k]["expense_total"] = r["expense"] or z

            # ---- sales (paid) by cashbox ----
            sale_model = get_sale_model()
            if sale_model is not None:
                paid_value = _choice_value(sale_model, "Status", "PAID", "paid")
                cash_value = _choice_value(sale_model, "PaymentMethod", "CASH", "cash")

                sales = (
                    sale_model.objects
                    .filter(cashbox_id__in=ids, status=paid_value)
                    .values("cashbox_id")
                    .annotate(
                        cnt=Count("id"),
                        total_sum=Sum("total"),
                        cash_sum=Sum("total", filter=Q(payment_method=cash_value)),
                        noncash_sum=Sum("total", filter=~Q(payment_method=cash_value)),
                    )
                )
                for r in sales:
                    k = str(r["cashbox_id"])
                    analytics_map[k]["sales_count"] = r["cnt"] or 0
                    analytics_map[k]["sales_total"] = r["total_sum"] or z
                    analytics_map[k]["cash_sales_total"] = r["cash_sum"] or z
                    analytics_map[k]["noncash_sales_total"] = r["noncash_sum"] or z

            # ---- open shift expected cash (batch) ----
            open_shifts = (
                CashShift.objects
                .filter(cashbox_id__in=ids, status=CashShift.Status.OPEN)
                .only("id", "cashbox_id", "opening_cash", "opened_at")
                .order_by("cashbox_id", "-opened_at")
            )

            open_by_cashbox = {}
            for sh in open_shifts:
                if sh.cashbox_id not in open_by_cashbox:
                    open_by_cashbox[sh.cashbox_id] = sh

            if open_by_cashbox:
                open_ids = [s.id for s in open_by_cashbox.values()]

                # flows внутри open shift
                shift_flows = (
                    CashFlow.objects
                    .filter(shift_id__in=open_ids, status=CashFlow.Status.APPROVED)
                    .values("shift__cashbox_id")
                    .annotate(
                        income=Sum("amount", filter=Q(type=CashFlow.Type.INCOME)),
                        expense=Sum("amount", filter=Q(type=CashFlow.Type.EXPENSE)),
                    )
                )
                sf_map = {r["shift__cashbox_id"]: r for r in shift_flows}

                # cash sales внутри open shift
                cash_sales_map = {}
                sale_model = get_sale_model()
                if sale_model is not None:
                    paid_value = _choice_value(sale_model, "Status", "PAID", "paid")
                    cash_value = _choice_value(sale_model, "PaymentMethod", "CASH", "cash")

                    cash_sales = (
                        sale_model.objects
                        .filter(shift_id__in=open_ids, status=paid_value, payment_method=cash_value)
                        .values("shift__cashbox_id")
                        .annotate(cash_sum=Sum("total"))
                    )
                    cash_sales_map = {r["shift__cashbox_id"]: (r["cash_sum"] or z) for r in cash_sales}

                for cb_id, sh in open_by_cashbox.items():
                    k = str(cb_id)
                    inc = (sf_map.get(cb_id) or {}).get("income") or z
                    exp = (sf_map.get(cb_id) or {}).get("expense") or z
                    cash_sales_total = cash_sales_map.get(cb_id, z)
                    opening_cash = sh.opening_cash or z
                    analytics_map[k]["open_shift_expected_cash"] = opening_cash + cash_sales_total + inc - exp

        serializer = self.get_serializer(
            cashboxes,
            many=True,
            context={"request": request, "analytics_map": analytics_map},
        )

        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class CashboxDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Cashbox.objects.select_related("company", "branch")
    serializer_class = CashboxWithFlowsSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


# ─────────────────────────────────────────────────────────────
# CASHFLOWS
# ─────────────────────────────────────────────────────────────
class CashFlowListCreateView(CompanyBranchScopedMixin, generics.ListCreateAPIView):
    queryset = CashFlow.objects.select_related(
        "company", "branch",
        "cashbox", "cashbox__branch",
        "shift", "shift__cashier",
        "cashier",
    )
    serializer_class = CashFlowSerializer

    def get_queryset(self):
        qs = self._scoped_queryset(super().get_queryset())

        shift_id = self.request.query_params.get("shift")
        cashier_id = self.request.query_params.get("cashier")
        cashbox_id = self.request.query_params.get("cashbox")

        if cashbox_id:
            qs = qs.filter(cashbox_id=cashbox_id)
        if shift_id:
            qs = qs.filter(shift_id=shift_id)
        if cashier_id:
            qs = qs.filter(cashier_id=cashier_id)

        return qs

    def perform_create(self, serializer):
        self._inject_company_branch_on_save(serializer)


class CashFlowDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = CashFlow.objects.select_related(
        "company", "branch",
        "cashbox", "cashbox__branch",
        "shift", "shift__cashier",
        "cashier",
    )
    serializer_class = CashFlowSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


# ─────────────────────────────────────────────────────────────
# OWNER-ONLY VIEWS
# ─────────────────────────────────────────────────────────────
class CashboxOwnerDetailView(CompanyBranchScopedMixin, generics.ListAPIView):
    serializer_class = CashboxWithFlowsSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            qs = Cashbox.objects.select_related("company", "branch")
        else:
            company = _get_company(user)
            if not (company and (getattr(user, "owned_company", None) or getattr(user, "is_admin", False))):
                raise PermissionDenied("Только владельцы/админы могут просматривать кассы.")
            qs = Cashbox.objects.filter(company=company).select_related("company", "branch")
        return self._scoped_queryset(qs)


class CashboxOwnerDetailSingleView(CompanyBranchScopedMixin, generics.RetrieveAPIView):
    serializer_class = CashboxWithFlowsSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            qs = Cashbox.objects.select_related("company", "branch")
        else:
            company = _get_company(user)
            if not (company and (getattr(user, "owned_company", None) or getattr(user, "is_admin", False))):
                return Cashbox.objects.none()
            qs = Cashbox.objects.filter(company=company).select_related("company", "branch")
        return self._scoped_queryset(qs)


# ─────────────────────────────────────────────────────────────
# CASHSHIFTS (СМЕНЫ)
# ─────────────────────────────────────────────────────────────
class CashShiftListView(CompanyBranchScopedMixin, generics.ListAPIView):
    serializer_class = CashShiftListSerializer

    def get_queryset(self):
        qs = CashShift.objects.select_related(
            "company", "branch", "cashbox", "cashbox__branch", "cashier"
        )

        qs = self._scoped_queryset(qs)

        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cashier=user)

        cashbox_id = self.request.query_params.get("cashbox")
        status = self.request.query_params.get("status")
        if cashbox_id:
            qs = qs.filter(cashbox_id=cashbox_id)
        if status in ("open", "closed"):
            qs = qs.filter(status=status)

        return qs


class CashShiftDetailView(CompanyBranchScopedMixin, generics.RetrieveAPIView):
    serializer_class = CashShiftListSerializer

    def get_queryset(self):
        qs = CashShift.objects.select_related(
            "company", "branch", "cashbox", "cashbox__branch", "cashier"
        )
        qs = self._scoped_queryset(qs)

        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(cashier=user)

        return qs


class CashShiftOpenView(CompanyBranchScopedMixin, generics.CreateAPIView):
    """
    ✅ Открыть смену:
      - кассир открывает себе
      - owner/admin может открыть на другого кассира (cashier)
    ✅ Важно: atomic нужен для select_for_update в сериализаторе
    """
    serializer_class = CashShiftOpenSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        shift = serializer.save()
        out = CashShiftListSerializer(shift, context={"request": request}).data
        return Response(out, status=201)


class CashShiftCloseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        company = getattr(request.user, "company", None) or getattr(request.user, "owned_company", None)
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        shift = get_object_or_404(
            CashShift.objects.select_related("company", "cashier", "cashbox"),
            id=pk,
            company=company,
        )

        user = request.user
        is_owner_like = (
            getattr(user, "is_superuser", False)
            or getattr(user, "owned_company", None)
            or getattr(user, "is_admin", False)
            or getattr(user, "role", None) in ("owner", "admin")
        )
        if not is_owner_like and shift.cashier_id != user.id:
            raise PermissionDenied("Нельзя закрыть чужую смену.")

        ser = CashShiftCloseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            ser.save(shift=shift)
        except Exception as e:
            raise ValidationError(str(e))

        out = CashShiftListSerializer(shift, context={"request": request}).data
        return Response(out, status=200)

class CashFlowBulkStatusUpdateView(CompanyBranchScopedMixin, generics.GenericAPIView):
    serializer_class = CashFlowBulkStatusSerializer

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        items = ser.validated_data["items"]

        ids = list({it["id"] for it in items})

        qs = self._scoped_queryset(CashFlow.objects.filter(id__in=ids))

        existing_ids = set(qs.values_list("id", flat=True))
        missing = [str(i) for i in ids if i not in existing_ids]
        if missing:
            raise ValidationError({"missing_ids": missing})

        whens = [When(id=it["id"], then=Value(it["status"])) for it in items]
        updated = qs.update(status=Case(*whens, output_field=CharField()))

        return Response({"count": updated, "updated_ids": [str(i) for i in ids]}, status=200)