from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction
from django.db.models import Sum, Count, Q, Exists, OuterRef
from django.db.models import Case, When, Value, CharField

from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date

from rest_framework import generics, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination

from apps.construction.models import Cashbox, CashFlow, CashFlowCategory, CashShift

from apps.ekassa.runtime import schedule_after_commit
from apps.ekassa.shift_bridge import (
    sync_ekassa_after_local_shift_close_by_id,
    sync_ekassa_after_local_shift_open_by_id,
)

from apps.construction.serializers import (
    CashboxSerializer,
    CashFlowSerializer,
    CashFlowCategorySerializer,
    CashboxWithFlowsSerializer,
    CashFlowInsideCashboxSerializer,
    CashShiftListSerializer,
    CashShiftOpenSerializer,
    CashShiftCloseSerializer,
    CashFlowBulkStatusSerializer
)

from apps.construction.utils import (
    get_company_from_user as _get_company,
    is_owner_like as _is_owner_like,
    fixed_branch_from_user as _fixed_branch_from_user,
    get_active_branch as _get_active_branch,
)


def _guess_sale_model():
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
    global SALE_MODEL
    if SALE_MODEL is None:
        SALE_MODEL = _guess_sale_model()
    return SALE_MODEL


def _choice_value(model, enum_name: str, member: str, fallback: str):
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
        qs = self._scoped_queryset(super().get_queryset())
        users_param = self.request.query_params.get("users")
        if not users_param:
            return qs
        user_ids = [x.strip() for x in users_param.split(",") if x.strip()]
        if not user_ids:
            return qs

        shift_exists = CashShift.objects.filter(cashbox_id=OuterRef("pk"), cashier_id__in=user_ids)
        q = Q(Exists(shift_exists))

        sale_model = get_sale_model()
        if sale_model is not None:
            fields = {f.name for f in sale_model._meta.concrete_fields}
            if "user" in fields and "cashbox" in fields:
                sale_qs = sale_model.objects.filter(user_id__in=user_ids)
                if "shift" in fields:
                    sale_qs = sale_qs.filter(Q(cashbox_id=OuterRef("pk")) | Q(shift__cashbox_id=OuterRef("pk")))
                else:
                    sale_qs = sale_qs.filter(cashbox_id=OuterRef("pk"))
                q |= Q(Exists(sale_qs))

        return qs.filter(q)

    def perform_create(self, serializer):
        self._inject_company_branch_on_save(serializer)

    def list(self, request, *args, **kwargs):
        """
        ✅ Ускорение: analytics считаем пачкой.
        ✅ ВАЖНО: теперь у кассы может быть несколько OPEN смен.
            - отдаём open_shifts: список по кассирам
            - оставляем open_shift_expected_cash (для совместимости) как "самая свежая open смена"
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

                # backward compatible (one value)
                "open_shift_expected_cash": None,

                # new (correct)
                "open_shifts": [],
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

            # ---- OPEN shifts (many) ----
            open_shifts = (
                CashShift.objects
                .filter(cashbox_id__in=ids, status=CashShift.Status.OPEN)
                .select_related("cashier")
                .only("id", "cashbox_id", "cashier_id", "opening_cash", "opened_at")
                .order_by("cashbox_id", "-opened_at")
            )

            # group shifts by cashbox
            by_cashbox = {}
            for sh in open_shifts:
                by_cashbox.setdefault(sh.cashbox_id, []).append(sh)

            if by_cashbox:
                all_open_ids = [sh.id for lst in by_cashbox.values() for sh in lst]

                # flows inside open shifts
                shift_flows = (
                    CashFlow.objects
                    .filter(shift_id__in=all_open_ids, status=CashFlow.Status.APPROVED)
                    .values("shift_id")
                    .annotate(
                        income=Sum("amount", filter=Q(type=CashFlow.Type.INCOME)),
                        expense=Sum("amount", filter=Q(type=CashFlow.Type.EXPENSE)),
                    )
                )
                sf_map = {r["shift_id"]: r for r in shift_flows}

                # cash sales inside open shifts
                cash_sales_map = {}
                sale_model = get_sale_model()
                if sale_model is not None:
                    paid_value = _choice_value(sale_model, "Status", "PAID", "paid")
                    cash_value = _choice_value(sale_model, "PaymentMethod", "CASH", "cash")

                    cash_sales = (
                        sale_model.objects
                        .filter(shift_id__in=all_open_ids, status=paid_value, payment_method=cash_value)
                        .values("shift_id")
                        .annotate(cash_sum=Sum("total"))
                    )
                    cash_sales_map = {r["shift_id"]: (r["cash_sum"] or z) for r in cash_sales}

                # build per-cashbox open_shifts list
                for cb_id, shifts in by_cashbox.items():
                    k = str(cb_id)
                    items = []

                    for sh in shifts:
                        inc = (sf_map.get(sh.id) or {}).get("income") or z
                        exp = (sf_map.get(sh.id) or {}).get("expense") or z
                        cash_sales_total = cash_sales_map.get(sh.id, z)
                        opening_cash = sh.opening_cash or z
                        expected = opening_cash + cash_sales_total + inc - exp

                        items.append({
                            "shift_id": str(sh.id),
                            "cashier_id": str(sh.cashier_id),
                            "opened_at": sh.opened_at.isoformat() if sh.opened_at else None,
                            "opening_cash": str(opening_cash),
                            "expected_cash": str(expected),
                        })

                    analytics_map[k]["open_shifts"] = items

                    # backward compatible single value: most recent open shift
                    if items:
                        analytics_map[k]["open_shift_expected_cash"] = items[0]["expected_cash"]

        serializer = self.get_serializer(
            cashboxes,
            many=True,
            context={"request": request, "analytics_map": analytics_map},
        )

        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class CashboxDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Cashbox.objects.select_related("company", "branch").prefetch_related(
        "flows__category",
        "flows__cashier",
        "flows__shift",
    )
    serializer_class = CashboxWithFlowsSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


# ─────────────────────────────────────────────────────────────
# CASHFLOWS
# ─────────────────────────────────────────────────────────────
class CashFlowListPagination(PageNumberPagination):
    page_size_query_param = "page_size"
    # Без верхней границы ?page_size=100000 выгружает всю кассу одним запросом.
    max_page_size = 200


def _csv_choices(qp, single_key: str, multi_key: str, allowed, *, error: str) -> list:
    """
    ?key=a или ?keys=a,b → список значений из allowed.
    Неизвестное значение — 400, а не молча пустой список.
    """
    raw = (qp.get(multi_key) or qp.get(single_key) or "").strip()
    if not raw:
        return []

    values = []
    for chunk in raw.split(","):
        v = chunk.strip().lower()
        if not v:
            continue
        if v not in allowed:
            raise ValidationError({single_key: [error]})
        if v not in values:
            values.append(v)
    return values


def _parse_amount(raw, field: str):
    """Сумма для фильтра: число ≥ 0, иначе 400. Пусто → None."""
    s = str(raw or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        value = Decimal(s)
    except (InvalidOperation, ValueError):
        raise ValidationError({field: ["Некорректная сумма."]})
    if value < 0:
        raise ValidationError({field: ["Сумма не может быть отрицательной."]})
    return value


class CashFlowListCreateView(CompanyBranchScopedMixin, generics.ListCreateAPIView):
    queryset = CashFlow.objects.select_related(
        "company", "branch",
        "cashbox", "cashbox__branch",
        "shift", "shift__cashier",
        "cashier",
        "category",
    )
    serializer_class = CashFlowSerializer
    pagination_class = CashFlowListPagination

    def get_queryset(self):
        qs = self._scoped_queryset(super().get_queryset())
        qp = self.request.query_params

        # ✅ 1 касса: ?cashbox=<uuid>
        cashbox_id = qp.get("cashbox")
        if cashbox_id:
            qs = qs.filter(cashbox_id=cashbox_id)

        # ✅ много касс: ?cashboxes=<uuid>,<uuid>,...
        cashboxes = qp.get("cashboxes")
        if cashboxes:
            ids = [x.strip() for x in cashboxes.split(",") if x.strip()]
            qs = qs.filter(cashbox_id__in=ids)

        # ✅ по смене: ?shift=<uuid>
        shift_id = qp.get("shift")
        if shift_id:
            qs = qs.filter(shift_id=shift_id)

        # ✅ по кассиру: ?cashier=<uuid>
        cashier_id = qp.get("cashier")
        if cashier_id:
            qs = qs.filter(cashier_id=cashier_id)

        # ✅ по категории: ?category=<uuid>
        category_id = qp.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)

        # ✅ по типу: ?type=expense|income (или ?types=expense,income)
        types = _csv_choices(
            qp, "type", "types", CashFlow.Type.values,
            error="Допустимые значения: income, expense.",
        )
        if types:
            qs = qs.filter(type__in=types)

        # ✅ по статусу: ?status=pending|approved|rejected (или ?statuses=...)
        # pending — это «Заявки» (ожидают подтверждения).
        statuses = _csv_choices(
            qp, "status", "statuses", CashFlow.Status.values,
            error="Допустимые значения: pending, approved, rejected.",
        )
        if statuses:
            qs = qs.filter(status__in=statuses)

        # ✅ поиск: ?search=<текст> — по названию операции и названию категории
        search = (qp.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(category__title__icontains=search)
            )

        # ✅ по сумме: ?amount_min=100&amount_max=5000
        amount_min = _parse_amount(qp.get("amount_min"), "amount_min")
        if amount_min is not None:
            qs = qs.filter(amount__gte=amount_min)
        amount_max = _parse_amount(qp.get("amount_max"), "amount_max")
        if amount_max is not None:
            qs = qs.filter(amount__lte=amount_max)

        # ✅ по периоду (по created_at): ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
        date_from_raw = (qp.get("date_from") or "").strip()
        if date_from_raw:
            df = parse_date(date_from_raw)
            if df is None:
                raise ValidationError({"date_from": ["Некорректная дата."]})
            qs = qs.filter(created_at__date__gte=df)

        date_to_raw = (qp.get("date_to") or "").strip()
        if date_to_raw:
            dt = parse_date(date_to_raw)
            if dt is None:
                raise ValidationError({"date_to": ["Некорректная дата."]})
            qs = qs.filter(created_at__date__lte=dt)

        # ✅ сортировка: ?ordering=-created_at|amount|category_title|... (whitelist)
        ordering = (qp.get("ordering") or "").strip()
        if ordering:
            allowed = {
                "created_at": "created_at",
                "amount": "amount",
                "type": "type",
                "category_title": "category__title",
                "cashbox_name": "cashbox__name",
            }
            field = allowed.get(ordering.lstrip("-"))
            if field:
                qs = qs.order_by(("-" if ordering.startswith("-") else "") + field, "-id")
                return qs

        # Пагинация требует полного порядка: created_at у операций одной секунды
        # совпадает, и без тай-брейка по id строки «прыгают» между страницами.
        return qs.order_by("-created_at", "-id")

    def perform_create(self, serializer):
        self._inject_company_branch_on_save(serializer)

class CashFlowDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = CashFlow.objects.select_related(
        "company", "branch",
        "cashbox", "cashbox__branch",
        "shift", "shift__cashier",
        "cashier",
        "category",
    )
    serializer_class = CashFlowSerializer

    def get_queryset(self):
        return self._scoped_queryset(super().get_queryset())


class CashFlowCategoryListCreateView(CompanyBranchScopedMixin, generics.ListCreateAPIView):
    queryset = CashFlowCategory.objects.select_related("company", "branch")
    serializer_class = CashFlowCategorySerializer
    pagination_class = None

    def get_queryset(self):
        qs = self._scoped_queryset(super().get_queryset()).order_by("title")
        q = (self.request.query_params.get("search") or "").strip()
        if q:
            qs = qs.filter(title__icontains=q)
        return qs

    def perform_create(self, serializer):
        # Категория: company/branch задаются в CashFlowCategorySerializer (branch опционально, null = на всю компанию).
        serializer.save()


class CashFlowCategoryDetailView(CompanyBranchScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = CashFlowCategory.objects.select_related("company", "branch")
    serializer_class = CashFlowCategorySerializer

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
            if not (company and _is_owner_like(user)):
                raise PermissionDenied("Только владельцы/админы могут просматривать кассы.")
            qs = Cashbox.objects.filter(company=company).select_related("company", "branch")
        return self._scoped_queryset(qs)


class CashboxOwnerDetailSingleView(CompanyBranchScopedMixin, generics.RetrieveAPIView):
    serializer_class = CashboxWithFlowsSerializer
    pagination_class = PageNumberPagination

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            qs = Cashbox.objects.select_related("company", "branch")
        else:
            company = _get_company(user)
            if not (company and _is_owner_like(user)):
                return Cashbox.objects.none()
            qs = Cashbox.objects.filter(company=company).select_related("company", "branch")
        return self._scoped_queryset(qs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        flows_qs = (
            CashFlow.objects.filter(cashbox_id=instance.id)
            .select_related("category", "cashier", "shift", "shift__cashier")
            .order_by("-created_at")
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(flows_qs, request, view=self)
        context = self.get_serializer_context()
        flows_data = CashFlowInsideCashboxSerializer(page, many=True, context=context).data
        payload = {
            "id": instance.id,
            "company": instance.company_id,
            "branch": instance.branch_id,
            "name": instance.name,
            "is_consumption": instance.is_consumption,
            "cashflows": flows_data,
            "count": paginator.page.paginator.count,
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
        }
        return Response(payload)


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
        cashbox_id = self.request.query_params.get("cashbox")
        status_q = self.request.query_params.get("status")
        if not _is_owner_like(user) and status_q != CashShift.Status.OPEN:
            qs = qs.filter(cashier=user)
        if cashbox_id:
            qs = qs.filter(cashbox_id=cashbox_id)
        if status_q in ("open", "closed"):
            qs = qs.filter(status=status_q)

        # Новые смены сверху
        return qs.order_by("-opened_at", "-id")


class CashShiftDetailView(CompanyBranchScopedMixin, generics.RetrieveAPIView):
    serializer_class = CashShiftListSerializer

    def get_queryset(self):
        qs = CashShift.objects.select_related(
            "company", "branch", "cashbox", "cashbox__branch", "cashier"
        )
        qs = self._scoped_queryset(qs)

        user = self.request.user
        if not _is_owner_like(user):
            qs = qs.filter(Q(status=CashShift.Status.OPEN) | Q(cashier=user))

        return qs


class CashShiftOpenView(CompanyBranchScopedMixin, generics.CreateAPIView):
    """
    ✅ Открыть смену:
      - кассир открывает себе
      - owner/admin может открыть на другого кассира (cashier)
    ✅ atomic нужен для select_for_update в serializer.validate
    """
    serializer_class = CashShiftOpenSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        shift = serializer.save()
        schedule_after_commit(sync_ekassa_after_local_shift_open_by_id, shift.id)
        out = CashShiftListSerializer(shift, context={"request": request}).data
        return Response(out, status=201)


class CashShiftCloseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        company = _get_company(request.user)
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        shift = get_object_or_404(
            CashShift.objects.select_related("company", "cashier", "cashbox"),
            id=pk,
            company=company,
        )

        user = request.user
        if not _is_owner_like(user) and shift.cashier_id != user.id:
            raise PermissionDenied("Нельзя закрыть чужую смену.")

        ser = CashShiftCloseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            ser.save(shift=shift)
        except Exception as e:
            raise ValidationError(str(e))

        schedule_after_commit(sync_ekassa_after_local_shift_close_by_id, shift.id)
        out = CashShiftListSerializer(shift, context={"request": request}).data
        return Response(out, status=200)


class CashFlowBulkStatusUpdateView(CompanyBranchScopedMixin, generics.GenericAPIView):
    serializer_class = CashFlowBulkStatusSerializer
    CHUNK_SIZE = 1000

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)

        items = ser.validated_data.get("items") or []
        if not items:
            return Response({"count": 0, "updated_ids": []}, status=200)

        id_to_status = {}
        for it in items:
            _id = it["id"]
            id_to_status[_id] = it["status"]

        ids = list(id_to_status.keys())

        qs = self._scoped_queryset(CashFlow.objects.filter(id__in=ids))

        existing_ids = set(qs.values_list("id", flat=True))
        missing = [str(i) for i in ids if i not in existing_ids]
        if missing:
            raise ValidationError({"missing_ids": missing})

        updated_ids = []
        updated_count = 0

        for i in range(0, len(ids), self.CHUNK_SIZE):
            chunk_ids = ids[i:i + self.CHUNK_SIZE]

            whens = [
                When(id=_id, then=Value(id_to_status[_id]))
                for _id in chunk_ids
            ]

            chunk_qs = qs.filter(id__in=chunk_ids)

            updated_count += chunk_qs.update(
                status=Case(*whens, output_field=CharField())
            )
            updated_ids.extend([str(x) for x in chunk_ids])

        return Response(
            {"count": updated_count, "updated_ids": updated_ids},
            status=200
        )
