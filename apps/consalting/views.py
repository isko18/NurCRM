from decimal import Decimal, InvalidOperation
from rest_framework import generics, permissions, status, filters
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from datetime import timedelta, datetime, date
from dateutil.relativedelta import relativedelta
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.db.models import Sum, Count, Q, Avg
from apps.main.models import Company

from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting,
    FunnelConsalting,
    FunnelStageConsalting,
    EmployeeFunnelGrant,
    LeadConsalting,
    LossReasonConsalting,
    LeadActivityConsalting,
    LeadTaskConsalting,
    FunnelUserPreferenceConsalting,
    ServiceSalaryRateConsalting,
    SalaryAccrualConsalting,
    SalaryPayoutConsalting,
    SalarySchemeConsalting,
    SalarySchemeServiceOverrideConsalting,
    SalaryDefaultsConsalting,
    BonusRuleConsalting,
    BonusTierConsalting,
    SalaryAdjustmentConsalting,
    InboundLeadConsalting,
    LeadDistributionSettingsConsalting,
    LeadFunnelHistoryConsalting,
    SubscriptionConsalting,
    SubscriptionPaymentConsalting,
    SalesPlanConsalting,
    KpiWeightsConsalting,
    CashOperationConsalting,
    CashRequestConsalting,
    CashConfirmationSettingsConsalting,
    SaleRefundConsalting,
    RegionalFunnelRoutingConsalting,
    RegionalFunnelRuleConsalting,
    LeadAdSpend,
)
from .serializers import (
    ServicesConsaltingSerializer,
    SaleConsaltingSerializer,
    SalaryConsaltingSerializer,
    RequestsConsaltingSerializer,
    BookingConsaltingSerializer,
    FunnelConsaltingSerializer,
    FunnelStageConsaltingSerializer,
    LeadConsaltingSerializer,
    LeadMoveStageSerializer,
    LeadAssignSerializer,
    LeadLoseSerializer,
    LeadWinSerializer,
    LossReasonConsaltingSerializer,
    LeadActivityConsaltingSerializer,
    LeadTaskConsaltingSerializer,
    FunnelStageReorderItemSerializer,
    FunnelUserPreferenceConsaltingSerializer,
    ServiceSalaryRateConsaltingSerializer,
    SalaryAccrualConsaltingSerializer,
    SalaryPayoutConsaltingSerializer,
    SalarySchemeConsaltingSerializer,
    SalarySchemeServiceOverrideConsaltingSerializer,
    SalaryDefaultsConsaltingSerializer,
    BonusRuleConsaltingSerializer,
    BonusTierConsaltingSerializer,
    SalaryAdjustmentConsaltingSerializer,
    InboundLeadConsaltingSerializer,
    LeadDistributionSettingsConsaltingSerializer,
    LeadFunnelHistoryConsaltingSerializer,
    SubscriptionConsaltingSerializer,
    SubscriptionPaymentConsaltingSerializer,
    SalesPlanConsaltingSerializer,
    CashOperationConsaltingSerializer,
    CashRequestConsaltingSerializer,
    CashConfirmationSettingsConsaltingSerializer,
    SaleRefundConsaltingSerializer,
    RegionalFunnelRoutingConsaltingSerializer,
    RegionalFunnelRuleConsaltingSerializer,
    LeadAdSpendSerializer,
)
from .funnel.state_machine import (
    FunnelStateMachine, StateTransitionError, allowed_next_types,
)
from .funnel.activity import ActivityLogger
from .funnel.scoring import ScoringService
from .funnel.analytics import PipelineAnalytics, SalesAnalytics
from .funnel.events import emit as emit_funnel_event
from .funnel import realtime
from .funnel.provisioning import provision_funnel_for_role
from .funnel.completion import (
    apply_completion_side_effects, ensure_subscription_deal, _add_months, accrue_salary_for_sale,
    generate_schedule,
)
from .access import (
    is_owner_like, is_consulting_supervisor, is_consulting_salesperson, get_user_region_codes,
    apply_lead_visibility, apply_client_visibility,
    visible_funnels_qs, can_view_funnel, can_manage_leads, can_manage_stages,
    can_manage_lead_ad_spend, CanManageLeadAdSpend,
)
from apps.users.models import Branch, CustomRole, User
from apps.main.models import Client
from apps.main.serializers import ClientSerializer


# ===== helpers =====
def _has_field(model_cls, field_name: str) -> bool:
    try:
        return any(f.name == field_name for f in model_cls._meta.get_fields())
    except Exception:
        return False


# ===== company + branch scoped mixin (как в барбере/букинге/кафе) =====
class CompanyBranchQuerysetMixin:
    """
    Видимость данных:
      - всегда ограничиваемся компанией пользователя
      - если у модели есть поле branch:
          * при привязке сотрудника к филиалу → только записи этого филиала
          * без филиала → все записи компании (без фильтра по branch)

    Активный филиал:
      1) «жёстко» назначенный филиал пользователя:
           - user.primary_branch() / user.primary_branch
           - user.branch
           - (опционально) единственный филиал из user.branch_ids
      2) если жёсткого филиала нет — ?branch=<uuid>, если филиал принадлежит компании
      3) request.branch (если проставляет middleware и он из этой компании)
      4) иначе None

    Создание:
      - company берём из пользователя
      - если активный филиал определён → жёстко ставим его в branch
      - если филиала нет → branch не трогаем

    Обновление:
      - company фиксируем
      - branch не меняем (не переносим запись между филиалами)
    """

    permission_classes = [permissions.IsAuthenticated]

    # --- helpers: user / company / branch ---

    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        """
        Компания пользователя:
          - сначала owned_company / company
          - если нет, пробуем взять через user.branch.company
        """
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None

        company = getattr(user, "company", None) or getattr(user, "owned_company", None)
        if company:
            return company

        # fallback: компания только через филиал пользователя
        br = getattr(user, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        return None

    def _fixed_branch_from_user(self, company):
        """
        «Жёстко» назначенный филиал сотрудника (который нельзя менять ?branch):
          - user.primary_branch() / user.primary_branch
          - user.branch
          - (опционально) единственный филиал из branch_ids
        """
        user = self._user()
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

        # 3) единственный филиал в branch_ids
        branch_ids = getattr(user, "branch_ids", None)
        if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
            try:
                return Branch.objects.get(id=branch_ids[0], company_id=company_id)
            except Branch.DoesNotExist:
                pass

        return None

    def _active_branch(self):
        """
        Итоговый активный филиал с проверкой принадлежности компании.
        """
        request = getattr(self, "request", None)
        company = self._user_company()
        if not company:
            if request:
                setattr(request, "branch", None)
            return None

        company_id = getattr(company, "id", None)

        # 1) жёстко назначенный филиал (primary / branch / branch_ids)
        fixed = self._fixed_branch_from_user(company)
        if fixed is not None:
            if request:
                setattr(request, "branch", fixed)
            return fixed

        # 2) если жёсткого филиала нет — смотрим ?branch=
        branch_id = None
        if request is not None:
            if hasattr(request, "query_params"):
                branch_id = request.query_params.get("branch")
            elif hasattr(request, "GET"):
                branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # чужой/кривой id — игнорируем
                pass

        # 3) request.branch, если middleware уже поставил корректный филиал
        if request and hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 4) филиала нет
        if request:
            setattr(request, "branch", None)
        return None

    def _auto_branch(self):
        """Алиас для _active_branch для совместимости."""
        return self._active_branch()

    # --- queryset / save hooks ---

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return qs.none()

        # ограничиваем по компании, если у модели есть такое поле
        model = qs.model
        if _has_field(model, "company"):
            qs = qs.filter(company=company)

        # если у модели есть branch — применяем логику филиала
        if _has_field(model, "branch"):
            active_branch = self._active_branch()  # None или Branch

            if active_branch is not None:
                # пользователь привязан к филиалу → только этот филиал
                qs = qs.filter(branch=active_branch)
            # если филиала нет → НЕ фильтруем по branch (видно все филиалы компании)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        model = self.get_queryset().model
        kwargs = {"company": company}

        if _has_field(model, "branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                # если есть филиал — жёстко пишем его
                kwargs["branch"] = active_branch
            # если филиала нет — branch не трогаем (можно создавать глобальные/любые по правилам сериализатора)

        serializer.save(**kwargs)

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        # company фиксируем, branch не меняем
        serializer.save(company=company)


# ==========================
# ServicesConsalting
# ==========================
class ServicesConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = ServicesConsalting.objects.prefetch_related("tariffs").all()
    serializer_class = ServicesConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in ServicesConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]

    def perform_create(self, serializer):
        from django.db import IntegrityError
        from rest_framework.exceptions import ValidationError as DRFValidationError
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        model = self.get_queryset().model
        kwargs = {"company": company}
        if _has_field(model, "branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                kwargs["branch"] = active_branch
        try:
            serializer.save(**kwargs)
        except IntegrityError:
            raise DRFValidationError({"name": ["Услуга с таким названием уже существует в этой компании."]})


class ServicesConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ServicesConsalting.objects.prefetch_related("tariffs").all()
    serializer_class = ServicesConsaltingSerializer


# ==========================
# SaleConsalting
# ==========================
class SaleConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = SaleConsalting.objects.select_related(
        "services", "tariff", "client", "user", "company"
    ).prefetch_related("items").all()
    serializer_class = SaleConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in SaleConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self._user()
        from .access import is_owner_like, is_consulting_supervisor, is_consulting_salesperson
        from apps.users.models import User

        if is_consulting_supervisor(user):
            my_regions = user.get_consulting_region_codes()
            region_user_ids = [
                u.id for u in User.objects.filter(company=user.company, is_active=True)
                if u.id == user.id or any(r in my_regions for r in u.get_consulting_region_codes())
            ]
            qs = qs.filter(Q(user_id__in=region_user_ids) | Q(lead__region_code__in=my_regions))
            region_param = self.request.query_params.get("region")
            if region_param and region_param in my_regions:
                qs = qs.filter(lead__region_code=region_param)
        elif is_consulting_salesperson(user):
            qs = qs.filter(user=user)
        elif not is_owner_like(user) and not getattr(user, "can_view_all_sales", False):
            qs = qs.filter(user=user)
        else:
            region_param = self.request.query_params.get("region")
            if region_param:
                qs = qs.filter(lead__region_code=region_param)
        return qs

    def perform_create(self, serializer):
        # company/branch — миксин; user — текущий оператор
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        model = self.get_queryset().model
        if _has_field(model, "branch"):
            sale = serializer.save(company=company, branch=self._active_branch(), user=self.request.user)
        else:
            sale = serializer.save(company=company, user=self.request.user)

        from .funnel.cash_confirmation import needs_confirmation
        from .models import CashRequestConsalting, CashOperationConsalting

        payment_method = getattr(sale, "payment_method", None) or self.request.data.get("payment_method") or "cash"
        if needs_confirmation(company, payment_method, self.request.user):
            sale.status = SaleConsalting.Status.PENDING_CONFIRMATION
            sale.save(update_fields=["status"])
            CashRequestConsalting.objects.create(
                company=company,
                sale=sale,
                user=self.request.user,
                client=sale.client,
                kind=CashRequestConsalting.Kind.SALE,
                direction="income",
                amount=sale.total,
                payment_method=payment_method,
                status=CashRequestConsalting.Status.PENDING,
            )
        else:
            CashOperationConsalting.objects.create(
                company=company,
                user=self.request.user,
                sale=sale,
                kind=CashOperationConsalting.Kind.SALE,
                direction=CashOperationConsalting.Direction.INCOME,
                amount=sale.total,
                payment_method=payment_method,
                comment=f"Прямая продажа: {sale.services.name if sale.services else 'Услуга'}",
            )
            from .funnel.completion import create_sale_side_effects
            create_sale_side_effects(sale)
            accrue_salary_for_sale(sale, seller=self.request.user)


class SaleConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SaleConsalting.objects.select_related(
        "services", "tariff", "client", "user", "company"
    ).prefetch_related("items").all()
    serializer_class = SaleConsaltingSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self._user()
        from .access import is_owner_like
        if not is_owner_like(user) and not getattr(user, "can_view_all_sales", False):
            qs = qs.filter(user=user)
        return qs

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.status == SaleConsalting.Status.CANCELED:
            raise PermissionDenied("Отменённую продажу нельзя редактировать.")
        if instance.status == SaleConsalting.Status.PENDING_CONFIRMATION:
            raise PermissionDenied("Продажу, ожидающую подтверждения кассы, нельзя редактировать.")
        serializer.save()

    def perform_destroy(self, instance):
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Удаление продажи доступно только руководителю.")
        if instance.status != SaleConsalting.Status.CANCELED:
            from apps.consalting.models import SubscriptionConsalting, CashRequestConsalting
            has_sub = SubscriptionConsalting.objects.filter(sale=instance).exists()
            has_cash = CashRequestConsalting.objects.filter(sale=instance).exists()
            if has_sub or has_cash:
                raise PermissionDenied("Продажу с оформленными подписками или кассовыми операциями необходимо отменять через отмену, а не удалять.")
        instance.delete()


class SaleConsaltingAnalyticsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Агрегированная аналитика продаж консалтинга (согласно docs-consaltion/analytics.md).
    GET /api/consalting/analytics/
    GET /api/consalting/sales/analytics/
    Поддерживаемые параметры:
      * period_start / date_from
      * period_end / date_to
      * branch
      * user / employee
      * service
    """
    queryset = SaleConsalting.objects.all()
    serializer_class = SaleConsaltingSerializer

    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        params = request.query_params
        date_from = params.get("period_start") or params.get("date_from") or None
        date_to = params.get("period_end") or params.get("date_to") or None
        user = params.get("user") or params.get("employee") or None
        service = params.get("service") or None
        branch = params.get("branch") or None

        data = SalesAnalytics.compute(
            company,
            date_from=date_from,
            date_to=date_to,
            branch=branch,
            user=user,
            service=service,
        )
        return Response(data)


class _ConsaltingAnalyticsBase(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """Общий разбор параметров для операционных отчётов консалтинга."""
    queryset = LeadConsalting.objects.all()
    serializer_class = LeadConsaltingSerializer

    def _params(self, request):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        p = request.query_params
        return company, {
            "date_from": p.get("period_start") or p.get("date_from") or None,
            "date_to": p.get("period_end") or p.get("date_to") or None,
            "branch": p.get("branch") or None,
        }


class ConsaltingDashboardAnalyticsView(_ConsaltingAnalyticsBase):
    """Сводная аналитика консалтинга + сравнение с предыдущим периодом.

    GET /api/consalting/analytics/dashboard/?date_from=&date_to=&branch=
    """

    def get(self, request, *args, **kwargs):
        from .funnel.analytics_ops import DashboardAnalytics
        company, kw = self._params(request)
        return Response(DashboardAnalytics.compute(company, **kw))


class ConsaltingMessengerAnalyticsView(_ConsaltingAnalyticsBase):
    """Аналитика переписки WhatsApp/Wazzup: скорость ответа, объём, неотвеченные.

    GET /api/consalting/analytics/messenger/?date_from=&date_to=&branch=&owner=
    """

    def get(self, request, *args, **kwargs):
        from .funnel.analytics_ops import MessengerAnalytics
        company, kw = self._params(request)
        kw["owner"] = request.query_params.get("owner") or None
        return Response(MessengerAnalytics.compute(company, **kw))


class ConsaltingSourceAnalyticsView(_ConsaltingAnalyticsBase):
    """Источники заявок и их конверсия в лид/сделку.

    GET /api/consalting/analytics/sources/?date_from=&date_to=&branch=
    """

    def get(self, request, *args, **kwargs):
        from .funnel.analytics_ops import SourceAnalytics
        company, kw = self._params(request)
        return Response(SourceAnalytics.compute(company, **kw))


class ConsaltingManagerAnalyticsView(_ConsaltingAnalyticsBase):
    """Нагрузка и результативность сотрудников по лидам.

    GET /api/consalting/analytics/managers/?date_from=&date_to=&branch=
    """

    def get(self, request, *args, **kwargs):
        from .funnel.analytics_ops import ManagerAnalytics
        company, kw = self._params(request)
        return Response(ManagerAnalytics.compute(company, **kw))


class ConsaltingDebtsAnalyticsView(_ConsaltingAnalyticsBase):
    """Current subscription arrears and subscription sales made on credit."""
    def get(self, request, *args, **kwargs):
        company, kw = self._params(request)
        today = timezone.localdate()
        branch = kw.get("branch")
        subscriptions = SubscriptionConsalting.objects.filter(company=company)
        if branch:
            subscriptions = subscriptions.filter(client__branch_id=branch)

        overdue = []
        debtor_map = {}
        def person(client):
            return (getattr(client, "full_name", "") or getattr(client, "title", "") or "—", getattr(client, "phone", "") or "")
        def add_debt(client, amount, days):
            if not client:
                return
            row = debtor_map.setdefault(str(client.id), {"client_id": str(client.id), "client_name": person(client)[0], "phone": person(client)[1], "total_debt": Decimal("0"), "max_days_overdue": 0})
            row["total_debt"] += amount
            row["max_days_overdue"] = max(row["max_days_overdue"], max(days or 0, 0))

        for p in SubscriptionPaymentConsalting.objects.filter(subscription__in=subscriptions, status=SubscriptionPaymentConsalting.Status.OVERDUE).select_related("subscription__client", "subscription__service", "subscription__tariff", "subscription__lead__owner"):
            sub, client = p.subscription, p.subscription.client
            days = max((today - p.due_date).days, 0)
            overdue.append({"payment_id": str(p.id), "subscription_id": str(sub.id), "client_id": str(client.id), "client_name": person(client)[0], "phone": person(client)[1], "service_display": getattr(sub.service, "name", None), "tariff_display": getattr(sub.tariff, "name", None), "amount": p.amount, "due_date": p.due_date, "days_overdue": days, "owner": (sub.lead.owner.get_full_name() if sub.lead_id and sub.lead.owner_id else None)})
            add_debt(client, p.amount, days)

        debt_rows = []
        active = subscriptions.filter(status=SubscriptionConsalting.Status.ACTIVE, sale__payment_mode__in=["debt", "installment"], sale__status=SaleConsalting.Status.COMPLETED).select_related("sale", "client", "service", "tariff", "lead__owner")
        for sub in active:
            sale = sub.sale
            paid = CashOperationConsalting.objects.filter(sale=sale, direction=CashOperationConsalting.Direction.INCOME).aggregate(v=Sum("amount"))["v"] or Decimal("0")
            # An operation may not yet exist for a recorded prepayment.
            paid = max(paid, Decimal("0"))
            total = Decimal(str(sale.total or 0))
            remaining = max(total - paid, Decimal("0"))
            if not remaining:
                continue
            due = sale.created_at.date() + relativedelta(months=sale.debt_months or 0)
            days = max((today - due).days, 0)
            client = sub.client
            debt_rows.append({"subscription_id": str(sub.id), "sale_id": str(sale.id), "client_id": str(client.id), "client_name": person(client)[0], "phone": person(client)[1], "service_display": getattr(sub.service, "name", None), "tariff_display": getattr(sub.tariff, "name", None), "payment_mode": sale.payment_mode, "debt_months": sale.debt_months, "amount_total": total, "amount_paid": paid, "amount_remaining": remaining, "start_date": sale.created_at.date(), "due_date": due, "days_overdue": days, "owner": (sale.user.get_full_name() if sale.user_id else (sub.lead.owner.get_full_name() if sub.lead_id and sub.lead.owner_id else None))})
            add_debt(client, remaining, days)

        overdue.sort(key=lambda x: x["days_overdue"], reverse=True)
        debt_rows.sort(key=lambda x: x["amount_remaining"], reverse=True)
        overdue_total = sum((x["amount"] for x in overdue), Decimal("0")); debt_total = sum((x["amount_remaining"] for x in debt_rows), Decimal("0"))
        buckets = {"0-30": [Decimal("0"), 0], "31-60": [Decimal("0"), 0], "61-90": [Decimal("0"), 0], "90+": [Decimal("0"), 0]}
        for amount, days in [(x["amount"], x["days_overdue"]) for x in overdue] + [(x["amount_remaining"], x["days_overdue"]) for x in debt_rows]:
            key = "90+" if days > 90 else "61-90" if days > 60 else "31-60" if days > 30 else "0-30"; buckets[key][0] += amount; buckets[key][1] += 1
        kpis = {"debtors_count": {"current": len(debtor_map), "previous": 0, "diff": len(debtor_map), "percent": 0}, "total_debt": {"current": overdue_total + debt_total, "previous": 0, "diff": overdue_total + debt_total, "percent": 0}, "overdue_amount": {"current": overdue_total, "previous": 0, "diff": overdue_total, "percent": 0}, "overdue_count": {"current": len(overdue)}, "debt_mode_amount": {"current": debt_total, "previous": 0, "diff": debt_total, "percent": 0}, "debt_mode_count": {"current": len(debt_rows)}}
        labels = {"0-30":"0–30 дн.", "31-60":"31–60 дн.", "61-90":"61–90 дн.", "90+":"90+ дн."}
        for key, value in buckets.items(): kpis["bucket_" + key.replace("-", "_").replace("+", "_plus")] = value[0]
        return Response({"kpis": kpis, "aging": [{"bucket": k, "bucket_label": labels[k], "amount": v[0], "count": v[1]} for k,v in buckets.items()], "overdue_subscriptions": overdue, "debt_subscriptions": debt_rows, "top_debtors": sorted(debtor_map.values(), key=lambda x: x["total_debt"], reverse=True)[:50]})



# ==========================
# SalaryConsalting
# ==========================
class SalaryConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = SalaryConsalting.objects.select_related("user", "company").all()
    serializer_class = SalaryConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in SalaryConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class SalaryConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SalaryConsalting.objects.select_related("user", "company").all()
    serializer_class = SalaryConsaltingSerializer


def _notify_request_assigned(request_obj):
    """Отправить WebSocket-уведомление сотруднику о назначении заявки."""
    try:
        user = request_obj.assigned_to
        if not user:
            return
        client_display = ""
        if request_obj.client:
            client_display = (
                getattr(request_obj.client, "full_name", None)
                or getattr(request_obj.client, "name", None)
                or getattr(request_obj.client, "phone", None)
                or ""
            )
        client_name = client_display or "клиента"
        payload = {
            "id": str(request_obj.id),
            "title": f"Вам назначена заявка от {client_name}",
            "message": request_obj.name or "Заявка клиента",
            "request_id": str(request_obj.id),
            "client_id": str(request_obj.client_id) if request_obj.client_id else None,
            "url": "/crm/consulting/client-requests",
        }
        from .funnel.realtime import notify_user
        notify_user(str(user.id), "request.assigned", payload)
    except Exception as e:
        import logging
        logging.getLogger("nurcrm").warning("_notify_request_assigned error: %s", e)


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company", "assigned_to").all()
    serializer_class = RequestsConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in RequestsConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]

    def get_queryset(self):
        qs = super().get_queryset().select_related("client", "company", "assigned_to")
        assigned_to_param = self.request.query_params.get("assigned_to")
        if assigned_to_param:
            val = assigned_to_param.strip().lower()
            if val in ("none", "null", "unassigned"):
                qs = qs.filter(assigned_to__isnull=True)
            else:
                qs = qs.filter(assigned_to_id=assigned_to_param)
        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        target_branch = self._active_branch()
        kwargs = {"company": company}
        if target_branch:
            kwargs["branch"] = target_branch
        obj = serializer.save(**kwargs)
        if obj.assigned_to_id:
            _notify_request_assigned(obj)


class RequestsConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company", "assigned_to").all()
    serializer_class = RequestsConsaltingSerializer

    def get_queryset(self):
        return super().get_queryset().select_related("client", "company", "assigned_to")

    def perform_update(self, serializer):
        old_assigned = serializer.instance.assigned_to_id
        obj = serializer.save()
        if obj.assigned_to_id and obj.assigned_to_id != old_assigned:
            _notify_request_assigned(obj)


def _get_target_user_ids_for_request(request_obj):
    user_ids = set()
    if hasattr(request_obj, "owner_id") and request_obj.owner_id:
        user_ids.add(str(request_obj.owner_id))
    if hasattr(request_obj, "created_by_id") and request_obj.created_by_id:
        user_ids.add(str(request_obj.created_by_id))
    if not user_ids and request_obj.company_id:
        company = request_obj.company
        if hasattr(company, "owner_id") and company.owner_id:
            user_ids.add(str(company.owner_id))
        else:
            from apps.users.models import User
            owners = User.objects.filter(company_id=request_obj.company_id, role__in=["owner", "admin", "ROP"]).values_list("id", flat=True)
            for uid in owners:
                user_ids.add(str(uid))
    return user_ids


def _notify_request_accepted(request_obj, acceptor):
    try:
        from .funnel.realtime import notify_user
        user_display = f"{acceptor.first_name or ''} {acceptor.last_name or ''}".strip() or getattr(acceptor, "email", "Сотрудник")
        payload = {
            "id": str(request_obj.id),
            "title": f"{user_display} принял заявку",
            "message": request_obj.name or "Заявка клиента",
            "request_id": str(request_obj.id),
            "client_id": str(request_obj.client_id) if request_obj.client_id else None,
            "url": "/crm/consulting/client-requests",
        }
        target_ids = _get_target_user_ids_for_request(request_obj)
        for uid in target_ids:
            if uid != str(acceptor.id):
                notify_user(uid, "request.accepted", payload)
    except Exception as e:
        import logging
        logging.getLogger("nurcrm").warning("_notify_request_accepted error: %s", e)


def _notify_request_declined(request_obj, decliner, reason):
    try:
        from .funnel.realtime import notify_user
        user_display = f"{decliner.first_name or ''} {decliner.last_name or ''}".strip() or getattr(decliner, "email", "Сотрудник")
        payload = {
            "id": str(request_obj.id),
            "title": f"{user_display} отказался от заявки",
            "message": f"Причина: {reason}",
            "request_id": str(request_obj.id),
            "decline_reason": reason,
            "client_id": str(request_obj.client_id) if request_obj.client_id else None,
            "url": "/crm/consulting/client-requests",
        }
        target_ids = _get_target_user_ids_for_request(request_obj)
        for uid in target_ids:
            if uid != str(decliner.id):
                notify_user(uid, "request.declined", payload)
    except Exception as e:
        import logging
        logging.getLogger("nurcrm").warning("_notify_request_declined error: %s", e)


class RequestsConsaltingAcceptView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/requests/<id>/accept/ — сотрудник принимает заявку.
    """
    serializer_class = RequestsConsaltingSerializer

    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        request_obj = get_object_or_404(RequestsConsalting, pk=pk, company=company)

        if request_obj.assigned_to_id != request.user.id:
            raise PermissionDenied("Принять заявку может только назначенный сотрудник.")

        if request_obj.acceptance != RequestsConsalting.Acceptance.PENDING:
            return Response({"error": "Заявка уже обработана."}, status=status.HTTP_409_CONFLICT)

        request_obj.acceptance = RequestsConsalting.Acceptance.ACCEPTED
        request_obj.status = RequestsConsalting.Status.IN_WORK
        request_obj.save(update_fields=["acceptance", "status", "updated_at"])

        _notify_request_accepted(request_obj, request.user)

        return Response(RequestsConsaltingSerializer(request_obj, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)


class RequestsConsaltingDeclineView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/requests/<id>/decline/ — сотрудник отказывается от заявки.
    """
    serializer_class = RequestsConsaltingSerializer

    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        request_obj = get_object_or_404(RequestsConsalting, pk=pk, company=company)

        if request_obj.assigned_to_id != request.user.id:
            raise PermissionDenied("Отказаться от заявки может только назначенный сотрудник.")

        if request_obj.acceptance != RequestsConsalting.Acceptance.PENDING:
            return Response({"error": "Заявка уже обработана."}, status=status.HTTP_409_CONFLICT)

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"reason": ["Обязательное поле."]}, status=status.HTTP_400_BAD_REQUEST)

        request_obj.decline_reason = reason
        request_obj.acceptance = RequestsConsalting.Acceptance.DECLINED
        request_obj.assigned_to = None
        request_obj.status = RequestsConsalting.Status.NEW
        request_obj.save(update_fields=["decline_reason", "acceptance", "assigned_to", "status", "updated_at"])

        _notify_request_declined(request_obj, request.user, reason)

        return Response(RequestsConsaltingSerializer(request_obj, context=self.get_serializer_context()).data, status=status.HTTP_200_OK)


# ==========================
# BookingConsalting
# ==========================
class BookingConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = BookingConsalting.objects.select_related("employee", "company").all()
    serializer_class = BookingConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in BookingConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class BookingConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BookingConsalting.objects.select_related("employee", "company").all()
    serializer_class = BookingConsaltingSerializer


# ==========================
# Клиенты (consalting namespace)
# ==========================
class ClientVisibilityMixin:
    """
    Видимость клиентов поверх company/branch (как у лидов):
      * клиент без продавца (salesperson=None) — общий пул, виден всем;
      * клиент с продавцом — только своему продавцу и руководителям.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return qs
        sector_param = self.request.query_params.get("sector")
        if sector_param and sector_param != "all":
            qs = qs.filter(sector__in=[sector_param, "all"])
        return apply_client_visibility(qs, getattr(self.request, "user", None))


class ClientConsaltingListCreateView(ClientVisibilityMixin, CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    Клиенты консалтинга (общая модель main.Client, scope по компании/филиалу).
    GET/POST /api/consalting/clients/
    """
    queryset = Client.objects.select_related("company", "branch", "salesperson", "service").all()
    serializer_class = ClientSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "type", "date", "salesperson", "service", "branch", "sector"]
    search_fields = ["full_name", "phone", "email", "llc", "inn"]
    ordering_fields = ["created_at", "updated_at", "date", "full_name"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        kwargs = {"company": company}
        if not serializer.validated_data.get("sector"):
            kwargs["sector"] = "consalting"
        active_branch = self._active_branch()
        if active_branch is not None:
            kwargs["branch"] = active_branch
        # сотрудник создаёт клиента всегда «на себя» (нельзя завести чужого);
        # руководитель может оставить пул или назначить продавца через payload.
        if not is_owner_like(self.request.user):
            kwargs["salesperson"] = self.request.user
        serializer.save(**kwargs)


class ClientConsaltingRetrieveUpdateDestroyView(ClientVisibilityMixin, CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /api/consalting/clients/<uuid:pk>/
    """
    queryset = Client.objects.select_related("company", "branch", "salesperson", "service").all()
    serializer_class = ClientSerializer


def serialize_tenant_account(client):
    """The common response shape for tenant creation, lookup-linking and status."""
    nur_comp = client.nur_company
    sub_plan = nur_comp.subscription_plan if nur_comp else None
    sector = nur_comp.sector if nur_comp else None
    end_date = None
    if nur_comp and nur_comp.end_date:
        end_date = nur_comp.end_date.date().isoformat() if hasattr(nur_comp.end_date, "date") else str(nur_comp.end_date)
    return {
        "provision_status": client.provision_status,
        "provision_status_display": client.get_provision_status_display(),
        "provision_error": client.provision_error or None,
        "provisioned_at": client.provisioned_at.isoformat() if client.provisioned_at else None,
        "nur_company_id": str(client.nur_company_id) if client.nur_company_id else None,
        "company_name": nur_comp.name if nur_comp else None,
        "owner_email": nur_comp.owner.email if (nur_comp and nur_comp.owner) else None,
        "end_date": end_date,
        "subscription_plan": {"id": str(sub_plan.id), "name": sub_plan.name} if sub_plan else None,
        "sector": {"id": str(sector.id), "name": sector.name} if sector else None,
    }


class TenantAccountLookupView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """Find a minimal, safe representation of an existing NurCRM account by email."""

    def get(self, request, *args, **kwargs):
        if not is_owner_like(request.user):
            raise PermissionDenied("Искать CRM-аккаунты может только руководитель.")

        email = (request.query_params.get("email") or "").strip()
        company = None
        if email:
            company = Company.objects.select_related("owner", "sector").filter(
                owner__email__iexact=email
            ).first()

        if not company:
            return Response({"match": None})

        end_date = company.end_date.date().isoformat() if company.end_date else None
        return Response({"match": {
            "nur_company_id": str(company.id),
            "company_name": company.name,
            "owner_email": company.owner.email,
            "sector": {"id": str(company.sector_id), "name": company.sector.name} if company.sector_id else None,
            "end_date": end_date,
        }})


class ClientTenantAccountView(ClientVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Информация о CRM-аккаунте клиента (§10.5).
    GET /api/consalting/clients/<uuid:pk>/tenant-account/
    """
    queryset = Client.objects.select_related("nur_company", "nur_company__owner", "nur_company__sector", "nur_company__subscription_plan").all()

    def get(self, request, *args, **kwargs):
        client = self.get_object()
        company = self._user_company()
        if company and client.company_id != company.id:
            raise PermissionDenied("Нет доступа к клиенту.")

        nur_comp = client.nur_company
        sector = nur_comp.sector if nur_comp else None

        if not sector:
            from .funnel.tenant_lifecycle import resolve_provision_sector_id
            from apps.users.models import Sector
            sale = SaleConsalting.objects.filter(client=client).order_by("-created_at").first()
            lead = LeadConsalting.objects.filter(client=client).order_by("-created_at").first()
            tariff = (sale.tariff if sale else None) or (lead.tariff if lead else None)
            try:
                exp_sec_id = resolve_provision_sector_id(tariff=tariff)
                sector = Sector.objects.filter(id=exp_sec_id).first()
            except Exception:
                pass

        data = serialize_tenant_account(client)
        # Until an account is provisioned the UI still needs the proposed sector.
        if not nur_comp and sector:
            data["sector"] = {"id": str(sector.id), "name": sector.name}
        return Response(data)


class ClientLinkTenantView(ClientVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """Attach this consulting client to an existing NurCRM tenant account."""
    queryset = Client.objects.select_related("nur_company", "nur_company__owner", "nur_company__sector", "nur_company__subscription_plan").all()

    def post(self, request, *args, **kwargs):
        client = self.get_object()
        company = self._user_company()
        if company and client.company_id != company.id:
            raise PermissionDenied("Нет доступа к клиенту.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Привязывать CRM-аккаунты может только руководитель.")

        nur_company_id = request.data.get("nur_company_id")
        if not nur_company_id:
            return Response({"detail": "Укажите nur_company_id."}, status=status.HTTP_400_BAD_REQUEST)

        from django.core.exceptions import ValidationError as DjangoValidationError
        from .funnel.tenant_lifecycle import TenantLinkConflict, link_existing_tenant
        try:
            link_existing_tenant(client=client, nur_company_id=nur_company_id, actor=request.user)
        except TenantLinkConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            detail = getattr(exc, "message_dict", None) or {"detail": exc.messages[0]}
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)

        client.refresh_from_db()
        return Response(serialize_tenant_account(client))


class ClientProvisionTenantView(ClientVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Ручной запуск/повтор создания CRM-аккаунта клиента (§10.5).
    POST /api/consalting/clients/<uuid:pk>/provision-tenant/
    """
    queryset = Client.objects.select_related("nur_company").all()

    def post(self, request, *args, **kwargs):
        client = self.get_object()
        company = self._user_company()
        if company and client.company_id != company.id:
            raise PermissionDenied("Нет доступа к клиенту.")

        if not is_owner_like(request.user):
            raise PermissionDenied("Создавать CRM-аккаунты может только руководитель.")

        if client.nur_company_id and client.provision_status == Client.ProvisionStatus.CREATED:
            return Response({"detail": "Аккаунт уже создан."}, status=status.HTTP_400_BAD_REQUEST)

        # Ищем последнюю подтверждённую продажу / лид клиента
        sale = SaleConsalting.objects.filter(client=client).order_by("-created_at").first()
        lead = LeadConsalting.objects.filter(client=client).order_by("-created_at").first()
        tariff = (sale.tariff if sale else None) or (lead.tariff if lead else None)

        from django.core.exceptions import ValidationError as DjangoValidationError
        from .funnel.tenant_lifecycle import provision_tenant_account

        crm_sector = request.data.get("crm_sector")

        try:
            res = provision_tenant_account(
                client=client,
                sale=sale,
                lead=lead,
                tariff=tariff,
                actor=request.user,
                crm_sector=crm_sector,
            )
        except DjangoValidationError as e:
            err_payload = getattr(e, "message_dict", None)
            if not err_payload:
                msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
                err_payload = {"detail": msg}
            return Response(err_payload, status=status.HTTP_400_BAD_REQUEST)

        client.refresh_from_db()
        data = serialize_tenant_account(client)
        data["generated_password"] = res.generated_password
        return Response(data, status=status.HTTP_200_OK)


# ==========================
# FunnelConsalting (воронка продаж)
# ==========================
class FunnelConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = FunnelConsalting.objects.prefetch_related("stages").all()
    serializer_class = FunnelConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active", "branch", "funnel_kind", "is_main", "custom_role"]

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return qs
        # видимость: owner/admin — все; сотрудник — роль + main/grants
        return visible_funnels_qs(qs, self.request.user)

    def create(self, request, *args, **kwargs):
        # Fallback фронта: POST /funnels/ с custom_role должен быть идемпотентным —
        # воронка роли уже могла быть создана сигналом при создании роли.
        role_id = request.data.get("custom_role")
        if role_id:
            if not is_owner_like(request.user):
                raise PermissionDenied("Создавать воронки может только владелец или администратор.")
            company = self._user_company()
            if not company:
                raise PermissionDenied("У пользователя не настроена компания.")
            try:
                role = CustomRole.objects.get(id=role_id)
            except (CustomRole.DoesNotExist, ValueError, TypeError):
                return Response({"custom_role": "Роль не найдена."}, status=status.HTTP_400_BAD_REQUEST)
            if role.company_id not in (None, company.id):
                return Response({"custom_role": "Роль не из вашей компании."}, status=status.HTTP_400_BAD_REQUEST)

            funnel, created = provision_funnel_for_role(role, name=request.data.get("name"))
            data = self.get_serializer(funnel).data
            return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        user = self.request.user
        is_mgr = is_owner_like(user)
        is_sup = is_consulting_supervisor(user)
        can_create = getattr(user, "can_create_funnel", False)

        if not is_mgr and not is_sup and not can_create:
            raise PermissionDenied("Нет права на создание воронок.")

        parent = serializer.validated_data.get("parent_funnel")
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        kwargs = {"company": company}
        target_branch = self._active_branch()
        if target_branch:
            kwargs["branch"] = target_branch

        if is_mgr:
            if parent:
                parent_region = getattr(parent, "region_code", "") or (parent.regional_rules.first().region_code if parent.regional_rules.exists() else "")
                kwargs["region_code"] = parent_region
                kwargs["funnel_kind"] = FunnelConsalting.FunnelKind.CUSTOM
            else:
                kwargs["funnel_kind"] = FunnelConsalting.FunnelKind.CUSTOM
        elif is_sup:
            sup_regions = get_user_region_codes(user)
            if parent:
                parent_region = getattr(parent, "region_code", "") or (parent.regional_rules.first().region_code if parent.regional_rules.exists() else "")
                if parent_region and parent_region not in sup_regions:
                    raise PermissionDenied("Регион вне вашей зоны.")
                kwargs["region_code"] = parent_region
            else:
                reg_code = sup_regions[0] if sup_regions else ""
                parent = FunnelConsalting.objects.filter(company=company, region_code=reg_code, funnel_kind=FunnelConsalting.FunnelKind.REGION).first()
                if not parent and reg_code:
                    parent = FunnelConsalting.objects.filter(company=company, regional_rules__region_code=reg_code).first()
                kwargs["parent_funnel"] = parent
                parent_region = getattr(parent, "region_code", "") or (parent.regional_rules.first().region_code if parent and parent.regional_rules.exists() else "")
                kwargs["region_code"] = parent_region or reg_code
            kwargs["funnel_kind"] = FunnelConsalting.FunnelKind.EMPLOYEE
            kwargs["owner_user"] = user
        else:
            emp_regions = get_user_region_codes(user)
            reg_code = emp_regions[0] if emp_regions else ""
            parent = None
            if reg_code:
                parent = FunnelConsalting.objects.filter(company=company, region_code=reg_code, funnel_kind=FunnelConsalting.FunnelKind.REGION).first()
                if not parent:
                    parent = FunnelConsalting.objects.filter(company=company, regional_rules__region_code=reg_code).first()
            kwargs["parent_funnel"] = parent
            parent_region = getattr(parent, "region_code", "") or (parent.regional_rules.first().region_code if parent and parent.regional_rules.exists() else "")
            kwargs["region_code"] = parent_region or reg_code
            kwargs["funnel_kind"] = FunnelConsalting.FunnelKind.EMPLOYEE
            kwargs["owner_user"] = user

        funnel = serializer.save(**kwargs)

        if not funnel.stages.exists():
            FunnelStageConsalting.objects.create(
                company=company, funnel=funnel, name="Новый", order=1, system_key="intake",
                is_system=True, stage_type=FunnelStageConsalting.StageType.NEW_LEAD, color="#3498db"
            )
            FunnelStageConsalting.objects.create(
                company=company, funnel=funnel, name="В работе", order=2, system_key="in_progress",
                is_system=True, stage_type=FunnelStageConsalting.StageType.NURTURE, color="#f39c12"
            )
            FunnelStageConsalting.objects.create(
                company=company, funnel=funnel, name="Завершено", order=3, system_key="completed",
                is_system=True, stage_type=FunnelStageConsalting.StageType.COMPLETED,
                is_final=True, is_success=True, color="#2ecc71"
            )

        if funnel.owner_user:
            EmployeeFunnelGrant.objects.get_or_create(
                employee=funnel.owner_user,
                funnel=funnel,
                defaults={"can_manage_leads": True, "can_manage_stages": True}
            )

        try:
            from .funnel.realtime import notify_company
            notify_company(company.id, "funnel.created", serializer.data)
        except Exception:
            pass


class FunnelConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = FunnelConsalting.objects.prefetch_related("stages").all()
    serializer_class = FunnelConsaltingSerializer

    def perform_update(self, serializer):
        user = self.request.user
        funnel = serializer.instance
        is_mgr = is_owner_like(user)
        is_sup = is_consulting_supervisor(user)
        is_owner_user = (funnel.owner_user_id == user.id)

        if not (is_mgr or is_sup or is_owner_user or can_manage_stages(user, funnel)):
            raise PermissionDenied("Изменять воронку может только владелец, руководитель или автор.")

        if not is_mgr and not is_sup:
            if "parent_funnel" in serializer.validated_data:
                serializer.validated_data.pop("parent_funnel")
        elif is_sup and "parent_funnel" in serializer.validated_data:
            parent = serializer.validated_data["parent_funnel"]
            if parent:
                sup_regions = get_user_region_codes(user)
                parent_region = getattr(parent, "region_code", "") or (parent.regional_rules.first().region_code if parent.regional_rules.exists() else "")
                if parent_region and parent_region not in sup_regions:
                    raise PermissionDenied("Регион вне вашей зоны.")
                serializer.validated_data["region_code"] = parent_region
        elif is_mgr and "parent_funnel" in serializer.validated_data:
            parent = serializer.validated_data["parent_funnel"]
            if parent:
                parent_region = getattr(parent, "region_code", "") or (parent.regional_rules.first().region_code if parent.regional_rules.exists() else "")
                serializer.validated_data["region_code"] = parent_region

        if "owner_user" in serializer.validated_data:
            serializer.validated_data.pop("owner_user")

        funnel = serializer.save()

        try:
            from .funnel.realtime import notify_company
            notify_company(funnel.company_id, "funnel.updated", serializer.data)
        except Exception:
            pass

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        user = request.user
        is_mgr = is_owner_like(user)
        is_owner_user = (instance.owner_user_id == user.id)

        # Main/static funnels are routing infrastructure and can never be
        # removed.  A role funnel, however, may be removed by management;
        # regular employees must not delete it even if they happen to be set
        # as its owner.
        if instance.is_main or instance.is_static or instance.funnel_kind == FunnelConsalting.FunnelKind.MAIN:
            raise PermissionDenied("Нет прав на удаление этой воронки.")

        if not is_mgr and (
            not is_owner_user
            or instance.funnel_kind == FunnelConsalting.FunnelKind.ROLE
            or instance.custom_role_id is not None
        ):
            raise PermissionDenied("Нет прав на удаление этой воронки.")

        open_leads_count = instance.leads.exclude(
            queue_status__in=(
                LeadConsalting.QueueStatus.CONVERTED,
                LeadConsalting.QueueStatus.REJECTED,
            )
        ).count()
        if open_leads_count > 0:
            return Response(
                {
                    "detail": (
                        f"В воронке есть {open_leads_count} незакрытых лид(ов). "
                        "Перенесите или закройте их перед удалением."
                    )
                },
                status=status.HTTP_409_CONFLICT
            )

        company_id = instance.company_id
        funnel_id = instance.id
        response = super().destroy(request, *args, **kwargs)

        try:
            from .funnel.realtime import notify_company
            notify_company(company_id, "funnel.deleted", {"id": str(funnel_id)})
        except Exception:
            pass

        return response


def _serialize_board(funnel, request, context):
    """Собирает payload доски воронки со счётчиками и суммами (§4.2)."""
    from django.db.models import Q, Count, Sum
    from datetime import timedelta
    from apps.consalting.models import WhatsAppMessageConsalting
    from .access import is_owner_like, is_consulting_supervisor, is_consulting_salesperson, get_user_region_codes

    user = request.user
    is_mgr = is_owner_like(user)
    is_sup = is_consulting_supervisor(user)
    is_sales = is_consulting_salesperson(user)

    # Основная воронка является общей доской компании. Региональная маршрутизация
    # физически оставляет лид в воронке региона, однако руководитель должен видеть
    # такой лид и на общей доске. Не меняем ``lead.funnel``: это представление, а не
    # перенос лида между воронками.
    if funnel.is_main:
        base_qs = LeadConsalting.objects.filter(
            company=funnel.company,
            is_archived=False,
        )
    else:
        base_qs = LeadConsalting.objects.filter(funnel=funnel, is_archived=False)

    # 1. Защита доступа на уровне строки (§1, §5)
    if is_mgr or is_sup:
        pass
    elif is_sales:
        base_qs = base_qs.filter(owner=user)
    else:
        base_qs = base_qs.filter(Q(owner=user) | Q(owner__isnull=True))

    # 2. Фильтры поиска, грейда, риска, неотвеченных и ответственного
    search = request.GET.get("search")
    if search:
        base_qs = base_qs.filter(
            Q(title__icontains=search) | Q(full_name__icontains=search) |
            Q(phone__icontains=search) | Q(email__icontains=search)
        )

    grade = request.GET.get("grade")
    if grade:
        base_qs = base_qs.filter(score_grade=grade)

    if request.GET.get("at_risk") in ("true", "1", True):
        base_qs = base_qs.filter(is_at_risk=True)

    if request.GET.get("needs_reply") in ("true", "1", True):
        base_qs = base_qs.filter(
            whatsapp_messages__direction=WhatsAppMessageConsalting.Direction.INBOUND
        ).exclude(whatsapp_messages__status=WhatsAppMessageConsalting.Status.READ)

    specific_owner = request.GET.get("owner")
    if specific_owner and (is_mgr or is_sup):
        base_qs = base_qs.filter(owner_id=specific_owner)

    # 3. Счётчики по всем скоупам (mine, pool, all) с учётом фильтров
    scope_agg = base_qs.aggregate(
        all_cnt=Count("id"),
        mine_cnt=Count("id", filter=Q(owner=user)),
        pool_cnt=Count("id", filter=Q(owner__isnull=True)),
    )
    scope_counts = {
        "mine": scope_agg["mine_cnt"] or 0,
        "pool": (scope_agg["pool_cnt"] or 0) if not is_sales else 0,
        "all": (scope_agg["all_cnt"] or 0) if (is_mgr or is_sup) else None,
    }

    # 4. Применение запрошенного скоупа (owner_scope)
    can_see_all = is_mgr or is_sup
    owner_scope = request.GET.get("owner_scope")
    if not owner_scope:
        owner_scope = "all" if can_see_all else "mine"
    elif not can_see_all and owner_scope == "all":
        owner_scope = "mine"
    elif is_sales and owner_scope == "pool":
        owner_scope = "mine"

    scoped_qs = base_qs
    if owner_scope == "mine":
        scoped_qs = scoped_qs.filter(owner=user)
    elif owner_scope == "pool":
        scoped_qs = scoped_qs.filter(owner__isnull=True)

    # 5. Итого по скоупу
    totals_agg = scoped_qs.aggregate(
        cnt=Count("id"),
        amt=Sum("estimated_value")
    )
    totals = {
        "count": totals_agg["cnt"] or 0,
        "amount": float(totals_agg["amt"] or 0),
    }

    columns = []
    now = timezone.now()
    board_stages = list(funnel.stages.all().order_by("order", "created_at"))
    # A funnel may contain several custom stages with the same semantic type.
    # Route external (regional) cards only into the first such column, otherwise
    # a card would be duplicated on the aggregate board.
    canonical_stage_ids = {}
    if funnel.is_main:
        for board_stage in board_stages:
            canonical_stage_ids.setdefault(board_stage.stage_type, board_stage.id)

    for stage in board_stages:
        # У региональных воронок свои объекты стадий. На общей доске объединяем
        # лиды по типу стадии (new_lead, in_work, won, ...), а не по UUID стадии.
        # Поэтому лид из «Ош» на стадии new_lead отображается в «Новый лид»
        # основной воронки, но остаётся лидом воронки «Ош».
        if funnel.is_main:
            stage_filter = Q(stage=stage)
            if canonical_stage_ids[stage.stage_type] == stage.id:
                stage_filter |= (
                    Q(stage__stage_type=stage.stage_type) & ~Q(funnel=funnel)
                )
            stage_qs = scoped_qs.filter(stage_filter)
        else:
            stage_qs = scoped_qs.filter(stage=stage)
        stage_qs = stage_qs.select_related("stage", "owner", "client")
        stage_count = stage_qs.count()
        stage_amount = float(stage_qs.aggregate(s=Sum("estimated_value"))["s"] or 0)

        effective_sla = stage.sla_hours or funnel.stage_sla_hours
        overdue_count = 0
        if effective_sla:
            cutoff = now - timedelta(hours=effective_sla)
            overdue_count = stage_qs.filter(stage_entered_at__lt=cutoff).count()

        stage_leads = list(stage_qs)
        columns.append({
            "stage": FunnelStageConsaltingSerializer(stage, context=context).data,
            "count": stage_count,
            "amount": stage_amount,
            "overdue_count": overdue_count,
            "leads": LeadConsaltingSerializer(stage_leads, many=True, context=context).data,
        })

    no_stage_qs = scoped_qs.filter(stage__isnull=True).select_related("owner", "client")
    unassigned_leads = list(no_stage_qs)

    funnel_data = FunnelConsaltingSerializer(funnel, context=context).data
    # The board can apply owner/search/status filters.  Reuse its exact
    # aggregate for the badge so ``funnel.leads_count`` and ``totals.count``
    # never describe different sets of cards in one response.
    funnel_data["leads_count"] = totals["count"]

    return {
        "funnel": funnel_data,
        "scope_counts": scope_counts,
        "totals": totals,
        "columns": columns,
        "unassigned": LeadConsaltingSerializer(unassigned_leads, many=True, context=context).data,
    }


class FunnelBoardView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Канбан-доска воронки: стадии со списком лидов в каждой.
    GET /api/consalting/funnels/<uuid:pk>/board/
    """
    queryset = FunnelConsalting.objects.all()
    serializer_class = FunnelConsaltingSerializer

    def get(self, request, *args, **kwargs):
        funnel = self.get_object()
        if not can_view_funnel(request.user, funnel):
            raise PermissionDenied("Нет доступа к этой воронке.")
        return Response(_serialize_board(funnel, request, self.get_serializer_context()))


class FunnelBoardsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Доски всех доступных пользователю воронок одним запросом.
    GET /api/consalting/funnels/boards/
    """
    queryset = FunnelConsalting.objects.prefetch_related("stages").all()
    serializer_class = FunnelConsaltingSerializer

    def get(self, request, *args, **kwargs):
        funnels = visible_funnels_qs(self.get_queryset(), request.user).filter(is_active=True)
        context = self.get_serializer_context()
        boards = {
            str(funnel.id): _serialize_board(funnel, request, context)
            for funnel in funnels
        }
        return Response({"boards": boards})


class FunnelForRoleView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Создать (или вернуть существующую) воронку для кастомной роли.
    POST /api/consalting/funnels/for-role/  { "custom_role": "<uuid>", "name"?: "..." }
    """
    queryset = FunnelConsalting.objects.all()
    serializer_class = FunnelConsaltingSerializer

    def post(self, request, *args, **kwargs):
        if not is_owner_like(request.user):
            raise PermissionDenied("Создавать воронку роли может только владелец или администратор.")

        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        role_id = request.data.get("custom_role")
        if not role_id:
            return Response({"custom_role": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            role = CustomRole.objects.get(id=role_id)
        except (CustomRole.DoesNotExist, ValueError, TypeError):
            return Response({"custom_role": "Роль не найдена."}, status=status.HTTP_400_BAD_REQUEST)
        if role.company_id not in (None, company.id):
            return Response({"custom_role": "Роль не из вашей компании."}, status=status.HTTP_400_BAD_REQUEST)

        funnel, created = provision_funnel_for_role(role, name=request.data.get("name"))
        data = FunnelConsaltingSerializer(funnel, context=self.get_serializer_context()).data
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


# ==========================
# FunnelStageConsalting (стадии)
# ==========================
class FunnelStageConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = FunnelStageConsalting.objects.select_related("funnel").all()
    serializer_class = FunnelStageConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["funnel", "is_final", "is_success", "branch"]

    def create(self, request, *args, **kwargs):
        # Fallback фронта: создание системной стадии идемпотентно —
        # системные стадии уже могли быть созданы provisioning'ом воронки роли.
        system_key = request.data.get("system_key")
        funnel_id = request.data.get("funnel")
        if system_key and funnel_id:
            existing = FunnelStageConsalting.objects.filter(
                funnel_id=funnel_id, system_key=system_key
            ).first()
            if existing:
                data = self.get_serializer(existing).data
                return Response(data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)

    # company/branch проставляются из воронки в сериализаторе
    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        # несистемные стадии добавляет тот, у кого manage_stages на воронке (раздел 1.5)
        funnel = serializer.validated_data.get("funnel")
        if funnel and not can_manage_stages(self.request.user, funnel):
            raise PermissionDenied("Нет прав управлять стадиями этой воронки.")
        serializer.save()


class FunnelStageConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = FunnelStageConsalting.objects.select_related("funnel").all()
    serializer_class = FunnelStageConsaltingSerializer

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if serializer.instance.is_system:
            raise PermissionDenied("Системную стадию нельзя изменить или удалить.")
        if not can_manage_stages(self.request.user, serializer.instance.funnel):
            raise PermissionDenied("Нет прав управлять стадиями этой воронки.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.is_system:
            raise PermissionDenied("Системную стадию нельзя изменить или удалить.")
        if not can_manage_stages(self.request.user, instance.funnel):
            raise PermissionDenied("Нет прав управлять стадиями этой воронки.")
        instance.delete()


class FunnelStageReorderView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Bulk-переупорядочивание стадий одним запросом.
    POST /api/consalting/funnel-stages/reorder/
        [ { "id": "<uuid>", "order": 0 }, { "id": "<uuid>", "order": 1 }, ... ]

    Права — те же, что у PATCH /funnel-stages/<id>/: can_manage_stages(funnel)
    для каждой затронутой воронки. Системные стадии в списке → 400.
    Можно передавать стадии нескольких воронок сразу.
    """
    queryset = FunnelStageConsalting.objects.select_related("funnel").all()
    serializer_class = FunnelStageReorderItemSerializer

    def post(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        if not isinstance(request.data, list) or not request.data:
            return Response(
                {"detail": "Ожидается непустой список объектов {id, order}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = self.get_serializer(data=request.data, many=True)
        ser.is_valid(raise_exception=True)

        # последнее значение order для каждого id (на случай дублей)
        order_map = {str(item["id"]): item["order"] for item in ser.validated_data}

        stages = list(
            FunnelStageConsalting.objects.select_related("funnel").filter(
                id__in=order_map.keys(), company=company
            )
        )
        found = {str(s.id) for s in stages}
        missing = sorted(set(order_map) - found)
        if missing:
            return Response(
                {"detail": "Стадии не найдены или из другой компании.", "ids": missing},
                status=status.HTTP_404_NOT_FOUND,
            )

        system_ids = sorted(str(s.id) for s in stages if s.is_system)
        if system_ids:
            return Response(
                {"detail": "Системные стадии нельзя переупорядочивать.", "ids": system_ids},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # права: те же, что у PATCH — на каждую затронутую воронку
        funnels = {s.funnel_id: s.funnel for s in stages}
        for funnel in funnels.values():
            if not can_manage_stages(request.user, funnel):
                raise PermissionDenied("Нет прав управлять стадиями этой воронки.")

        # Запрос может содержать лишь часть стадий (например, одну перетащенную) с
        # позицией, которую уже занимает другая стадия. Поэтому пересобираем порядок
        # ЦЕЛИКОМ по каждой затронутой воронке: запрошенные стадии встают на свои
        # места, остальные сдвигаются, итог — плотная нумерация 0..N-1 без коллизий.
        all_stages = list(
            FunnelStageConsalting.objects.filter(funnel_id__in=funnels.keys())
        )
        by_funnel = {}
        for s in all_stages:
            by_funnel.setdefault(s.funnel_id, []).append(s)

        to_update = []
        for funnel_id, group in by_funnel.items():
            # ключ сортировки: запрошенные стадии — по новому order и приоритетом 0
            # (занимают слот раньше «сдвигаемой» прежней), остальные — по текущему order.
            def sort_key(s):
                sid = str(s.id)
                if sid in order_map:
                    return (order_map[sid], 0, s.order, sid)
                return (s.order, 1, s.order, sid)

            ordered = sorted(group, key=sort_key)
            for new_order, s in enumerate(ordered):
                if s.order != new_order:
                    s.order = new_order
                    to_update.append(s)

        # два прохода, чтобы не нарушить UniqueConstraint(funnel, order):
        # сначала «паркуем» в заведомо свободные значения выше всех существующих
        # (order — PositiveIntegerField, поэтому только положительные), потом — целевые.
        from django.db.models import Max
        max_existing = FunnelStageConsalting.objects.filter(
            funnel_id__in=funnels.keys()
        ).aggregate(m=Max("order"))["m"] or 0
        try:
            with transaction.atomic():
                if to_update:
                    parked = []
                    for idx, s in enumerate(to_update):
                        final = s.order
                        s.order = max_existing + 1 + idx
                        parked.append((s, final))
                    FunnelStageConsalting.objects.bulk_update(to_update, ["order"])
                    for s, final in parked:
                        s.order = final
                    FunnelStageConsalting.objects.bulk_update(to_update, ["order"])
        except IntegrityError:
            return Response(
                {"detail": "Конфликт порядка стадий: значения order не уникальны в пределах воронки."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        all_stages.sort(key=lambda s: (str(s.funnel_id), s.order))
        data = FunnelStageConsaltingSerializer(
            all_stages, many=True, context=self.get_serializer_context()
        ).data
        return Response({"updated": len(to_update), "stages": data})


# ==========================
# Пользовательские предпочтения по воронкам
# ==========================
class FunnelUserPreferenceView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Персональный порядок воронок-строк (per-user), ранее хранившийся в localStorage.
    GET   /api/consalting/user-preferences/   → { "funnel_order": [...] }
    PATCH /api/consalting/user-preferences/   { "funnel_order": [...] }

    GET отдаёт только существующие/видимые пользователю воронки (исчезнувшие
    игнорируются), сохраняя порядок. Если предпочтений нет — funnel_order: [].
    """
    serializer_class = FunnelUserPreferenceConsaltingSerializer

    def get_queryset(self):  # для swagger/DRF
        return FunnelUserPreferenceConsalting.objects.none()

    def _visible_funnel_ids(self):
        qs = FunnelConsalting.objects.all()
        company = self._user_company()
        if company:
            qs = qs.filter(company=company)
        return {str(fid) for fid in visible_funnels_qs(qs, self.request.user).values_list("id", flat=True)}

    def get(self, request, *args, **kwargs):
        pref = FunnelUserPreferenceConsalting.objects.filter(user=request.user).first()
        stored = pref.funnel_order if pref else []
        visible = self._visible_funnel_ids()
        funnel_order = [fid for fid in stored if fid in visible]
        return Response({"funnel_order": funnel_order})

    def patch(self, request, *args, **kwargs):
        payload = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        if "owner" not in payload:
            oid = payload.get("owner_id") or payload.get("new_owner_id")
            if oid:
                payload["owner"] = oid
        ser = self.get_serializer(data=payload)
        ser.is_valid(raise_exception=True)
        funnel_order = ser.validated_data["funnel_order"]
        pref, _ = FunnelUserPreferenceConsalting.objects.get_or_create(user=request.user)
        pref.funnel_order = funnel_order
        pref.save(update_fields=["funnel_order", "updated_at"])
        return Response({"funnel_order": funnel_order})

    # позволяем и PUT как алиас PATCH (на случай, если фронт пошлёт PUT)
    def put(self, request, *args, **kwargs):
        return self.patch(request, *args, **kwargs)


# ==========================
# LeadConsalting (карточки лидов)
# ==========================
class LeadVisibilityMixin:
    """
    Видимость лидов поверх company/branch:
      * лид без владельца (owner=None) — общий пул, виден всем;
      * взятый лид — только владельцу и руководителям (owner/admin компании).
    """

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return qs
        return apply_lead_visibility(qs, getattr(self.request, "user", None))


class LeadPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


class LeadConsaltingListCreateView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner", "client", "company").all()
    serializer_class = LeadConsaltingSerializer
    pagination_class = LeadPagination

    def get_queryset(self):
        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return LeadConsalting.objects.none()

        qp = self.request.query_params

        queue_param = qp.get("queue")
        if queue_param:
            qp_lower = queue_param.lower().strip()
            if qp_lower == "new":
                qs = qs.filter(queue_status__in=["new", "assigned"])
            elif qp_lower in ("in_work", "deferred", "converted", "rejected"):
                qs = qs.filter(queue_status=qp_lower)

        status_param = qp.get("status")
        if status_param and not queue_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            if statuses:
                qs = qs.filter(queue_status__in=statuses)

        owner_param = qp.get("owner")
        if owner_param:
            op_lower = owner_param.lower().strip()
            if op_lower in ("none", "null", "unassigned"):
                qs = qs.filter(owner__isnull=True)
            elif op_lower in ("mine", "my"):
                qs = qs.filter(owner=self.request.user)
            elif op_lower not in ("all", ""):
                try:
                    import uuid
                    qs = qs.filter(owner_id=uuid.UUID(owner_param))
                except ValueError:
                    pass

        channel_param = qp.get("channel")
        if channel_param:
            qs = qs.filter(channel=channel_param)

        region_param = qp.get("region")
        if region_param:
            qs = qs.filter(region_code=region_param)

        search_param = qp.get("search")
        if search_param:
            s = search_param.strip()
            qs = qs.filter(
                Q(title__icontains=s) |
                Q(full_name__icontains=s) |
                Q(phone__icontains=s) |
                Q(description__icontains=s)
            )

        date_from = qp.get("date_from")
        if date_from:
            try:
                from datetime import datetime
                df = datetime.strptime(date_from.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=df)
            except ValueError:
                pass

        date_to = qp.get("date_to")
        if date_to:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_to.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=dt)
            except ValueError:
                pass

        overdue_param = qp.get("overdue")
        if overdue_param and overdue_param.lower() in ("true", "1"):
            qs = qs.filter(
                queue_status="deferred",
                remind_at__lte=timezone.now()
            )

        funnel_param = qp.get("funnel")
        if funnel_param:
            try:
                import uuid
                qs = qs.filter(funnel_id=uuid.UUID(funnel_param))
            except ValueError:
                pass

        ordering = qp.get("ordering", "-created_at")
        allowed_orderings = {
            "created_at": "created_at",
            "-created_at": "-created_at",
            "updated_at": "updated_at",
            "-updated_at": "-updated_at",
            "remind_at": "remind_at",
            "-remind_at": "-remind_at",
            "full_name": "full_name",
            "-full_name": "-full_name",
            "title": "title",
            "-title": "-title",
            "queue_status": "queue_status",
            "-queue_status": "-queue_status",
        }
        ord_field = allowed_orderings.get(ordering, "-created_at")
        qs = qs.order_by(ord_field)
        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        funnel = serializer.validated_data.get("funnel")
        if not funnel:
            funnel = FunnelConsalting.objects.filter(company=company, is_main=True).first() or FunnelConsalting.objects.filter(company=company).first()
            if not funnel:
                funnel = FunnelConsalting.objects.create(company=company, name="Основная воронка", is_main=True)

        stage = serializer.validated_data.get("stage")
        if not stage and funnel:
            stage = funnel.stages.order_by("order").first()
            if not stage:
                stage = FunnelStageConsalting.objects.create(company=company, funnel=funnel, name="Новый", order=1, system_key="intake")

        if funnel and not can_manage_leads(self.request.user, funnel):
            raise PermissionDenied("Нет прав создавать лиды в этой воронке.")

        channel = serializer.validated_data.get("channel") or "manual"
        queue_status = serializer.validated_data.get("queue_status") or "new"

        lead = serializer.save(
            company=company,
            funnel=funnel,
            stage=stage,
            channel=channel,
            queue_status=queue_status,
            address=serializer.validated_data.get("address") or "",
        )

        try:
            InboundLeadConsalting.objects.create(
                company=company,
                lead=lead,
                full_name=lead.full_name or lead.title,
                phone=lead.phone,
                email=lead.email,
                source=lead.channel or "manual",
                message=lead.description,
                status=lead.queue_status,
                owner=lead.owner,
                remind_at=lead.remind_at,
                defer_reason=lead.defer_reason,
                defer_comment=lead.defer_comment,
                defer_count=lead.defer_count,
                deferred_at=lead.deferred_at,
                reminded_at=lead.reminded_at,
                reject_reason=lead.reject_reason,
                reject_comment=lead.reject_comment,
                first_reply_at=lead.first_reply_at,
                converted_at=lead.converted_at,
                closed_at=lead.closed_at,
                external_id=lead.inbound_external_id or f"lead:{lead.id}",
            )
        except Exception:
            pass

        realtime.lead_created(lead)


class LeadConsaltingCountersView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    GET /api/consalting/leads/counters/ — Счётчики по статусам очереди единой базы лидов.
    """
    queryset = LeadConsalting.objects.all()

    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            return Response({"all": 0, "new": 0, "in_work": 0, "deferred": 0, "converted": 0, "rejected": 0, "overdue": 0})

        qs = self.filter_queryset(self.get_queryset()).filter(company=company)
        qp = request.query_params

        owner_param = qp.get("owner")
        if owner_param:
            op_lower = owner_param.lower().strip()
            if op_lower in ("none", "null", "unassigned"):
                qs = qs.filter(owner__isnull=True)
            elif op_lower in ("mine", "my"):
                qs = qs.filter(owner=request.user)
            elif op_lower not in ("all", ""):
                try:
                    import uuid
                    qs = qs.filter(owner_id=uuid.UUID(owner_param))
                except ValueError:
                    pass

        channel_param = qp.get("channel")
        if channel_param:
            qs = qs.filter(channel=channel_param)

        region_param = qp.get("region")
        if region_param:
            qs = qs.filter(region_code=region_param)

        search_param = qp.get("search")
        if search_param:
            s = search_param.strip()
            qs = qs.filter(
                Q(title__icontains=s) |
                Q(full_name__icontains=s) |
                Q(phone__icontains=s) |
                Q(description__icontains=s)
            )

        date_from = qp.get("date_from")
        if date_from:
            try:
                from datetime import datetime
                df = datetime.strptime(date_from.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=df)
            except ValueError:
                pass

        date_to = qp.get("date_to")
        if date_to:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_to.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=dt)
            except ValueError:
                pass

        funnel_param = qp.get("funnel")
        if funnel_param:
            try:
                import uuid
                qs = qs.filter(funnel_id=uuid.UUID(funnel_param))
            except ValueError:
                pass

        now = timezone.now()
        return Response({
            "all": qs.count(),
            "new": qs.filter(queue_status__in=["new", "assigned"]).count(),
            "in_work": qs.filter(queue_status="in_work").count(),
            "deferred": qs.filter(queue_status="deferred").count(),
            "converted": qs.filter(queue_status="converted").count(),
            "rejected": qs.filter(queue_status="rejected").count(),
            "overdue": qs.filter(queue_status="deferred", remind_at__lte=now).count(),
        })


class LeadConsaltingAnalyticsView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    GET /api/consalting/leads/analytics/ — Аналитика единой базы лидов.
    """
    queryset = LeadConsalting.objects.all()

    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            return Response({})

        qs = self.filter_queryset(self.get_queryset()).filter(company=company)
        qp = request.query_params

        date_from = qp.get("date_from")
        if date_from:
            try:
                from datetime import datetime
                df = datetime.strptime(date_from.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=df)
            except ValueError:
                pass

        date_to = qp.get("date_to")
        if date_to:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_to.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=dt)
            except ValueError:
                pass

        owner_param = qp.get("owner")
        if owner_param and owner_param not in ("all", ""):
            try:
                import uuid
                qs = qs.filter(owner_id=uuid.UUID(owner_param))
            except ValueError:
                pass

        channel_param = qp.get("channel")
        if channel_param:
            qs = qs.filter(channel=channel_param)

        region_param = qp.get("region")
        if region_param:
            qs = qs.filter(region_code=region_param)

        total = qs.count()
        converted = qs.filter(queue_status="converted").count()
        rejected = qs.filter(queue_status="rejected").count()
        conversion_rate = round((converted / total * 100), 2) if total > 0 else 0.0

        by_channel = list(
            qs.values("channel")
              .annotate(count=Count("id"))
              .order_by("-count")
        )

        by_user = list(
            qs.values("owner_id", "owner__first_name", "owner__last_name", "owner__email")
              .annotate(count=Count("id"))
              .order_by("-count")
        )

        by_day = list(
            qs.extra(select={'day': "DATE(created_at)"})
              .values('day')
              .annotate(count=Count("id"))
              .order_by("day")
        )

        defer_reasons = list(
            qs.filter(queue_status="deferred")
              .values("defer_reason")
              .annotate(count=Count("id"))
              .order_by("-count")
        )

        reject_reasons = list(
            qs.filter(queue_status="rejected")
              .values("reject_reason")
              .annotate(count=Count("id"))
              .order_by("-count")
        )

        return Response({
            "total_leads": total,
            "converted_leads": converted,
            "rejected_leads": rejected,
            "conversion_rate": conversion_rate,
            "by_channel": by_channel,
            "by_user": by_user,
            "by_day": by_day,
            "defer_reasons": defer_reasons,
            "reject_reasons": reject_reasons,
        })


class LeadConsaltingRetrieveUpdateDestroyView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner", "client", "company").all()
    serializer_class = LeadConsaltingSerializer

    def perform_update(self, serializer):
        lead = serializer.instance
        if not can_manage_leads(self.request.user, lead.funnel):
            raise PermissionDenied("Нет прав изменять лиды в этой воронке.")
        # Ток назначенный сотрудник или руководитель может взаимодействовать с лидом
        if lead.owner_id and lead.owner_id != self.request.user.id and not is_owner_like(self.request.user):
            raise PermissionDenied("С этим лидом может взаимодействовать только назначенный сотрудник.")
        # завершённый лид редактирует только owner/admin
        if (lead.stage and lead.stage.system_key == "completed"
                and not is_owner_like(self.request.user)):
            raise PermissionDenied("Завершённый лид может изменять только владелец или администратор.")
        prev_owner_id = lead.owner_id
        super().perform_update(serializer)
        lead = serializer.instance
        # смена владельца через обычный PATCH трактуем как взятие/возврат
        if lead.owner_id != prev_owner_id:
            if lead.owner_id:
                realtime.lead_claimed(lead)
            else:
                realtime.lead_released(lead)
        else:
            realtime.lead_updated(lead)

    def perform_destroy(self, instance):
        if instance.owner_id and instance.owner_id != self.request.user.id and not is_owner_like(self.request.user):
            raise PermissionDenied("Удалять лид может только назначенный сотрудник или руководитель.")
        realtime.lead_deleted(instance)
        instance.delete()


class LeadMoveStageView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Перемещение лида в другую стадию его воронки — через машину состояний.
    POST /api/consalting/leads/<uuid:pk>/move-stage/  { "stage": "<uuid>" }

    В «мягком» режиме (CONSALTING_FUNNEL_STRICT=False) недопустимые переходы
    выполняются, но фиксируются как нарушения в timeline. В «строгом» — 400.

    Сотрудник может двигать только свои лиды или ничьи из общего пула; руководитель — любых.
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage").all()
    serializer_class = LeadMoveStageSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав двигать лиды в этой воронке.")
        if lead.owner_id and lead.owner_id != request.user.id and not is_owner_like(request.user):
            raise PermissionDenied("С этим лидом может взаимодействовать только назначенный сотрудник.")
        # завершённый лид двигает только owner/admin
        if (lead.stage and lead.stage.system_key == "completed"
                and not is_owner_like(request.user)):
            raise PermissionDenied("Завершённый лид может перемещать только владелец или администратор.")
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        stage = ser.validated_data["stage"]

        if stage.funnel_id != lead.funnel_id:
            return Response(
                {"stage": "Стадия относится к другой воронке."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            lead = FunnelStateMachine.transition(lead, stage, actor=request.user)
        except StateTransitionError as e:
            return Response(
                {"detail": "Переход запрещён", "errors": e.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # сайд-эффекты завершения: продажа-аналитика + абонентка (зарплата — отдельно).
        # Триггерим на любой успешной терминальной стадии (won/completed), а не только
        # на системной «completed» — иначе выигрыш в пользовательской воронке или на
        # WON-стадии не создаёт продажу. Идемпотентно (проверка по lead внутри).
        if stage.is_success:
            apply_completion_side_effects(lead, actor=request.user)
        return Response(
            LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data
        )


class LeadClaimView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    «Взять» лид себе: ставит owner=текущий пользователь.
    POST /api/consalting/leads/<uuid:pk>/claim/

    Доступны лиды из общего пула (owner=None) или уже свои в пределах разрешённого региона.
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()
    serializer_class = LeadConsaltingSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return qs
        user = getattr(self.request, "user", None)
        if is_owner_like(user):
            return qs
        if not user or not getattr(user, "is_authenticated", False):
            return qs.none()
        if is_consulting_supervisor(user):
            user_regions = get_user_region_codes(user)
            return qs.filter(region_code__in=user_regions)
        if is_consulting_salesperson(user):
            user_regions = get_user_region_codes(user)
            q = Q(owner=user) | Q(owner__isnull=True)
            if user_regions:
                q &= Q(region_code__in=user_regions)
            return qs.filter(q)
        return qs.filter(Q(owner__isnull=True) | Q(owner=user))

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав брать лиды в этой воронке.")
        if is_consulting_salesperson(request.user):
            user_regions = get_user_region_codes(request.user)
            if user_regions and lead.region_code and lead.region_code not in user_regions:
                raise PermissionDenied("Нельзя брать лиды из чужого региона.")
        if lead.owner_id and lead.owner_id != request.user.id and not is_owner_like(request.user):
            return Response(
                {"detail": "Лид уже взят другим сотрудником."},
                status=status.HTTP_409_CONFLICT,
            )
        if lead.owner_id != request.user.id:
            lead.owner = request.user
            lead.save(update_fields=["owner", "updated_at"])
            realtime.lead_claimed(lead)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadReleaseView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Вернуть лид в общий пул: снимает owner.
    POST /api/consalting/leads/<uuid:pk>/release/

    Сотрудник может вернуть только свой лид; руководитель — любой.
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if lead.owner_id and lead.owner_id != request.user.id and not is_owner_like(request.user):
            raise PermissionDenied("Нельзя вернуть чужой лид.")
        if lead.owner_id is not None:
            lead.owner = None
            lead.save(update_fields=["owner", "updated_at"])
            realtime.lead_released(lead)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadAssignView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Назначить ответственного (только руководитель).
    POST /api/consalting/leads/<uuid:pk>/assign/  { "owner": "<user-uuid>" }
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()
    serializer_class = LeadAssignSerializer

    def post(self, request, *args, **kwargs):
        is_mgr = is_owner_like(request.user)
        is_sup = is_consulting_supervisor(request.user)
        if not is_mgr and not is_sup:
            raise PermissionDenied("Назначать ответственного может только руководитель.")

        company = self._user_company()
        lead = self.get_object()

        if is_sup:
            my_regions = request.user.get_consulting_region_codes()
            lead_region = lead.region_code
            if not lead_region and lead.funnel:
                rule = lead.funnel.regional_rules.filter(is_active=True).first()
                lead_region = rule.region_code if rule else ""
            if lead_region not in my_regions:
                raise PermissionDenied("Нет доступа к лидам вне вашего региона.")

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        owner = ser.validated_data["owner"]

        if company and getattr(owner, "company_id", None) not in (None, company.id):
            return Response({"owner": "Сотрудник из другой компании."},
                            status=status.HTTP_400_BAD_REQUEST)

        if is_sup:
            owner_regions = owner.get_consulting_region_codes()
            lead_reg = lead.region_code
            if not lead_reg and lead.funnel:
                rule = lead.funnel.regional_rules.filter(is_active=True).first()
                lead_reg = rule.region_code if rule else ""
            if lead_reg and lead_reg not in owner_regions:
                raise PermissionDenied("Назначить можно только сотрудника этого региона.")

        if lead.owner_id != owner.id:
            lead.owner = owner
            if lead.queue_status in ("new", None, ""):
                lead.queue_status = "assigned"
            lead.save(update_fields=["owner", "queue_status", "updated_at"])
            realtime.lead_claimed(lead)
            # персональное уведомление назначенному сотруднику
            realtime.notify_user(owner.id, "lead.assigned", realtime.serialize_lead(lead))
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadDeferView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/leads/<uuid:pk>/defer/ — отложить лид.
    Body: { "remind_at": "ISO-8601", "reason": "...", "comment": "..." }
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        remind_at_str = request.data.get("remind_at")
        reason = (request.data.get("reason") or "").strip()
        comment = (request.data.get("comment") or "").strip()

        if not remind_at_str:
            return Response({"remind_at": "Укажите дату и время напоминания."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from django.utils.dateparse import parse_datetime, parse_date
            remind_at = parse_datetime(str(remind_at_str).strip())
            if not remind_at:
                d = parse_date(str(remind_at_str).strip())
                if d:
                    import datetime
                    remind_at = timezone.make_aware(datetime.datetime.combine(d, datetime.time.min))
            if not remind_at:
                return Response({"remind_at": "Неверный формат даты."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"remind_at": "Неверный формат даты."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        lead.queue_status = "deferred"
        lead.remind_at = remind_at
        lead.defer_reason = reason
        lead.defer_comment = comment
        lead.deferred_at = now
        lead.defer_count = (lead.defer_count or 0) + 1
        lead.save()

        InboundLeadConsalting.objects.filter(lead=lead).update(
            status="deferred",
            remind_at=remind_at,
            defer_reason=reason,
            defer_comment=comment,
            deferred_at=now,
            defer_count=lead.defer_count,
        )

        realtime.lead_updated(lead)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadResumeView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/leads/<uuid:pk>/resume/ — вернуть лид в работу.
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        now = timezone.now()
        lead.queue_status = "in_work"
        lead.remind_at = None
        lead.reminded_at = now
        lead.save()

        InboundLeadConsalting.objects.filter(lead=lead).update(
            status="in_work",
            remind_at=None,
            reminded_at=now,
        )

        realtime.lead_updated(lead)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadTransferOwnerView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Передать лид другому сотруднику компании.
    POST /api/consalting/leads/<uuid:pk>/transfer-owner/
    Body: { "new_owner_id": "<uuid>" } или { "owner": "<uuid>" }

    Права:
      - Текущий назначенный сотрудник (lead.owner)
      - Руководитель / Владелец компании (is_owner_like)
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()

        if lead.owner_id and lead.owner_id != request.user.id and not is_owner_like(request.user):
            raise PermissionDenied("Передать лид может только его текущий ответственный или руководитель.")

        new_owner_id = request.data.get("new_owner_id") or request.data.get("owner_id") or request.data.get("owner")
        if not new_owner_id:
            return Response({"detail": "Укажите ID нового сотрудника ('new_owner_id' или 'owner')."}, status=status.HTTP_400_BAD_REQUEST)

        company = self._user_company()
        new_owner = get_object_or_404(User, id=new_owner_id, company=company)

        old_owner_name = lead.owner.full_name if lead.owner else "Не назначен"

        if lead.owner_id != new_owner.id:
            lead.owner = new_owner
            lead.save(update_fields=["owner", "updated_at"])

            ActivityLogger.log(
                lead,
                activity_type=LeadActivityConsalting.Type.MESSAGE,
                actor=request.user,
                title="Передача лида сотруднику",
                body=f"Лид передан сотруднику {new_owner.full_name or new_owner.email} (ранее: {old_owner_name})."
            )

            realtime.lead_claimed(lead)

            try:
                from apps.main.realtime import create_and_publish_notification
                create_and_publish_notification(
                    company=company,
                    user=new_owner,
                    title=f"📥 Вам передан лид: {lead.full_name}",
                    message=f"Лид передан от сотрудника {request.user.full_name or request.user.email}.",
                    type="lead_assigned",
                    level="info",
                    url=f"/consalting/leads/{lead.id}",
                    data={"lead_id": str(lead.id), "phone": lead.phone}
                )
            except Exception as e:
                logger.warning("Failed to publish lead_assigned notification: %s", e)

        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadMarkMessagesReadView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Пометить сообщения лида как прочитанные (сбросить счётчик непрочитанных).
    POST /api/consalting/leads/<uuid:pk>/mark-read/
    """
    queryset = LeadConsalting.objects.all()

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        from .models import ChatReadStateConsalting, WhatsAppMessageConsalting, WazzupAccountConsalting
        from .funnel.wazzup import WazzupConsaltingService

        now = timezone.now()
        unread_qs = WhatsAppMessageConsalting.objects.filter(
            lead=lead, direction=WhatsAppMessageConsalting.Direction.INBOUND
        )
        state, created = ChatReadStateConsalting.objects.get_or_create(
            lead=lead,
            employee=request.user,
            defaults={"last_read_at": now},
        )
        if created:
            marked_read_count = unread_qs.count()
        else:
            marked_read_count = (
                unread_qs.filter(created_at__gt=state.last_read_at).count()
                if state.last_read_at else unread_qs.count()
            )
            state.last_read_at = now
            state.save(update_fields=["last_read_at"])

        account = WazzupAccountConsalting.objects.filter(company=lead.company, is_active=True).first()
        if account and lead.phone:
            WazzupConsaltingService.mark_chat_read(account, lead.phone)

        realtime.lead_updated(lead)
        return Response({"status": "ok", "marked_read_count": marked_read_count})


class LeadTransferView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Передать лид в другую воронку: обновляет funnel, stage и опционально owner у существующего лида.
    POST /api/consalting/leads/<uuid:pk>/transfer/
        { "target_funnel": "<uuid>", "target_stage": "<uuid|null>", "owner": "<uuid|null>" }
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        company = self._user_company()
        lead = self.get_object()

        # Права на исходную воронку
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав управлять лидами в исходной воронке.")

        target_funnel_id = request.data.get("target_funnel")
        if not target_funnel_id:
            return Response({"target_funnel": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target_funnel = FunnelConsalting.objects.get(id=target_funnel_id, company=company)
        except (FunnelConsalting.DoesNotExist, ValueError, TypeError):
            return Response({"target_funnel": "Воронка не найдена."}, status=status.HTTP_404_NOT_FOUND)

        new_owner_id = request.data.get("owner") or request.data.get("owner_id")
        if target_funnel.id == lead.funnel_id and not new_owner_id:
            return Response({"target_funnel": "Нельзя передать в ту же воронку."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Права на целевую воронку
        if not can_manage_leads(request.user, target_funnel):
            raise PermissionDenied("Нет прав управлять лидами в целевой воронке.")

        # Целевая стадия: переданная (должна быть из target_funnel) или intake / первая стадия по порядку
        target_stage = None
        target_stage_id = request.data.get("target_stage")
        if target_stage_id and str(target_stage_id).strip().lower() != "null":
            try:
                target_stage = FunnelStageConsalting.objects.get(id=target_stage_id)
            except (FunnelStageConsalting.DoesNotExist, ValueError, TypeError):
                return Response({"target_stage": "Стадия не найдена."}, status=status.HTTP_400_BAD_REQUEST)
            if target_stage.funnel_id != target_funnel.id:
                return Response({"target_stage": "Стадия относится к другой воронке."},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            target_stage = (
                FunnelStageConsalting.objects.filter(
                    funnel=target_funnel, system_key="intake"
                ).first()
                or FunnelStageConsalting.objects.filter(funnel=target_funnel).order_by("order").first()
            )

        target_rule = target_funnel.regional_rules.filter(is_active=True).first()
        target_region = target_rule.region_code if target_rule and target_rule.region_code else lead.region_code

        new_owner = None
        if new_owner_id and str(new_owner_id).strip().lower() != "null":
            try:
                new_owner = User.objects.get(id=new_owner_id, company=company)
                if is_consulting_supervisor(request.user):
                    owner_regions = new_owner.get_consulting_region_codes()
                    if target_region and target_region not in owner_regions:
                        raise PermissionDenied(
                            "Назначить можно только сотрудника этого региона."
                        )
            except (User.DoesNotExist, ValueError, TypeError):
                return Response({"owner": "Сотрудник не найден."}, status=status.HTTP_400_BAD_REQUEST)

        old_funnel = lead.funnel
        lead.funnel = target_funnel
        lead.stage = target_stage
        lead.region_code = target_region
        lead.stage_entered_at = timezone.now()
        if new_owner:
            lead.owner = new_owner
            if lead.queue_status in ("new", None, ""):
                lead.queue_status = "assigned"
        lead.save(update_fields=["funnel", "stage", "region_code", "owner", "queue_status", "stage_entered_at", "updated_at"])

        try:
            ActivityLogger.log(
                lead,
                activity_type=LeadActivityConsalting.Type.SYSTEM,
                actor=request.user,
                title=f"Лид передан в воронку «{target_funnel.name}»",
                body=f"Лид перенесён из воронки «{old_funnel.name if old_funnel else ''}» в «{target_funnel.name}»."
                     + (f" Ответственный: {lead.owner.email}." if lead.owner else ""),
                payload={
                    "type": "lead_transferred",
                    "from_funnel": str(old_funnel.id) if old_funnel else None,
                    "to_funnel": str(target_funnel.id),
                    "lead_id": str(lead.id),
                    "actor_id": str(request.user.id),
                },
                touch_last_activity=False,
            )
        except Exception as e:
            pass

        realtime.lead_updated(lead)
        return Response(
            LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data,
            status=status.HTTP_200_OK,
        )


class FunnelEmployeesView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Сотрудники, которым доступна воронка (для выбора участников лида).
    GET /api/consalting/funnels/<uuid:pk>/employees/
    """
    queryset = FunnelConsalting.objects.all()
    serializer_class = FunnelConsaltingSerializer

    def get(self, request, *args, **kwargs):
        funnel = self.get_object()
        company = self._user_company()
        users = User.objects.filter(company=company, is_active=True, deleted_at__isnull=True)
        data = []
        for u in users:
            if can_view_funnel(u, funnel):
                name = f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email
                data.append({
                    "id": str(u.id), "display": name, "email": u.email,
                    "role": getattr(u, "role", None),
                    "can_manage_leads": can_manage_leads(u, funnel),
                })
        return Response(data)


class LeadParticipantsView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Заменить список участников лида.
    POST /api/consalting/leads/<uuid:pk>/participants/  { "participant_ids": ["<uuid>"] }
    """
    queryset = LeadConsalting.objects.select_related("funnel").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав управлять лидами в этой воронке.")
        company = self._user_company()
        ids = request.data.get("participant_ids") or []
        users = list(User.objects.filter(id__in=ids, company=company))
        lead.participants.set(users)
        realtime.lead_updated(lead)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadArchiveView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Архивировать завершённый лид (исчезает с доски).
    POST /api/consalting/leads/<uuid:pk>/archive/
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав управлять лидами в этой воронке.")
        if not (lead.stage and lead.stage.system_key == "completed"):
            return Response(
                {"detail": "Архивировать можно только лид на стадии «Завершено»."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not lead.is_archived:
            lead.is_archived = True
            lead.archived_at = timezone.now()
            lead.save(update_fields=["is_archived", "archived_at", "updated_at"])
            # для досок — карточка должна исчезнуть
            realtime.lead_deleted(lead)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadArchivedListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    Архивные лиды (видимость по воронкам/владельцу).
    GET /api/consalting/leads/archived/
    """
    serializer_class = LeadConsaltingSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return LeadConsalting.objects.none()
        company = self._user_company()
        if not company:
            return LeadConsalting.objects.none()
        qs = LeadConsalting.objects.filter(
            company=company, is_archived=True
        ).select_related("funnel", "stage", "owner", "client")
        # видимость: сотрудник — только воронки visible(F) + свои/пул
        if not is_owner_like(self.request.user):
            visible = visible_funnels_qs(FunnelConsalting.objects.filter(company=company), self.request.user)
            qs = qs.filter(funnel__in=visible)
            qs = apply_lead_visibility(qs, self.request.user)
        return qs.order_by("-archived_at")


class LeadCreateClientView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Создать клиента из лида или найти существующего (§8.3, §8.5).
    POST /api/consalting/leads/<uuid:pk>/create-client/
        { "full_name"?, "phone"?, "email"?, "service"?, "force_merge"?: bool }
    """
    queryset = LeadConsalting.objects.select_related("funnel", "service", "client").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав управлять лидами в этой воронке.")
        company = self._user_company()

        had_client = bool(lead.client_id)
        fn = request.data.get("full_name")
        if fn:
            lead.full_name = fn
        ph = request.data.get("phone")
        if ph:
            lead.phone = ph
        em = request.data.get("email")
        if em:
            lead.email = em
        srv_id = request.data.get("service")
        if srv_id:
            lead.service_id = srv_id

        from .funnel.lead_conversion import resolve_client_from_lead
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            client, merged, warning = resolve_client_from_lead(
                lead, user=request.user, force_create=True, return_meta=True
            )
        except DjangoValidationError as e:
            return Response(
                getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        lead.refresh_from_db()
        realtime.lead_updated(lead)

        ctx = self.get_serializer_context()
        data = {
            "client_id": str(client.id),
            "merged": merged,
            "client_display": client.full_name or "Клиент",
            "duplicate_warning": warning,
            "client": ClientSerializer(client, context=ctx).data,
            "lead": LeadConsaltingSerializer(lead, context=ctx).data,
        }
        return Response(data, status=status.HTTP_200_OK if had_client else status.HTTP_201_CREATED)


class LeadRegisterPaymentView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Оформить оплату по лиду → создаёт сделку в main у привязанного клиента.
    POST /api/consalting/leads/<uuid:pk>/register-payment/
        { "payment_mode": "cash|transfer|debt|installment",
          "amount": "...", "debt_months": 6, "prepayment": "...", "note": "" }
    """
    queryset = LeadConsalting.objects.select_related("funnel", "client").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        from decimal import Decimal, InvalidOperation
        from django.core.exceptions import ValidationError as DjangoValidationError
        from apps.main.models import ClientDeal

        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав управлять лидами в этой воронке.")

        if not lead.client_id:
            from .funnel.lead_conversion import resolve_client_from_lead
            try:
                resolve_client_from_lead(lead, user=request.user)
            except DjangoValidationError as e:
                return Response(
                    getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}),
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if lead.tariff and getattr(lead.tariff, "provisions_crm_account", False):
            if not (lead.email or (lead.client and lead.client.email)):
                return Response(
                    {"detail": "Сначала создайте клиента из лида или укажите email для автосоздания аккаунта."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        mode = request.data.get("payment_mode")
        if mode not in ("cash", "transfer", "debt", "installment"):
            return Response({"payment_mode": "Допустимо: cash|transfer|debt|installment."},
                            status=status.HTTP_400_BAD_REQUEST)

        def _dec(v, default="0"):
            try:
                return Decimal(str(v)) if v not in (None, "") else Decimal(default)
            except (InvalidOperation, TypeError):
                return Decimal(default)

        amount = _dec(request.data.get("amount") if request.data.get("amount") not in (None, "") else lead.estimated_value)
        prepayment = _dec(request.data.get("prepayment"))
        debt_months = request.data.get("debt_months")
        note = request.data.get("note") or ""

        deal = ClientDeal(
            company=lead.company, branch=lead.branch, client=lead.client,
            title=lead.title or "Оплата по лиду",
        )
        if mode in ("cash", "transfer"):
            deal.kind = ClientDeal.Kind.SALE
            deal.amount = amount
            deal.prepayment = Decimal("0")
            label = "наличные" if mode == "cash" else "перевод"
            deal.note = (note + f"\nСпособ оплаты: {label}").strip()
        else:  # debt / installment → рассрочка с графиком
            deal.kind = ClientDeal.Kind.DEBT
            deal.amount = amount
            deal.prepayment = prepayment  # для installment это первый платёж
            try:
                deal.debt_days = int(debt_months) if debt_months else None
            except (TypeError, ValueError):
                deal.debt_days = None
            deal.auto_schedule = True
            deal.note = (note + ("\nРассрочка" if mode == "installment" else "\nДолг")).strip()

        try:
            deal.save()  # full_clean + авто-график для DEBT
        except DjangoValidationError as e:
            return Response(
                getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        lead.payment_registered = True
        lead.payment_mode = mode
        lead.payment_deal = deal
        lead.save(update_fields=["payment_registered", "payment_mode", "payment_deal", "updated_at"])

        # Абонентская подписка и график платежей (§5.3, §5.6)
        from .models import ServicesConsalting, TariffConsalting
        service_id = request.data.get("services")
        if service_id and not lead.service_id:
            lead.service = ServicesConsalting.objects.filter(id=service_id, company=lead.company).first()
        tariff_id = request.data.get("tariff")
        if tariff_id and not lead.tariff_id:
            lead.tariff = TariffConsalting.objects.filter(id=tariff_id, company=lead.company).first()

        paid_months_val = request.data.get("paid_months")
        try:
            paid_months = max(1, int(paid_months_val)) if paid_months_val is not None else 1
        except (ValueError, TypeError):
            paid_months = 1

        sub_enabled = request.data.get("subscription_enabled")
        sub_amount = request.data.get("subscription_amount")
        sub_period = request.data.get("subscription_period")
        sub_start = request.data.get("subscription_start")
        prepaid_periods = request.data.get("subscription_prepaid_periods")

        tariff = lead.tariff
        if sub_enabled is None:
            sub_enabled = True if ((tariff and (tariff.subscription_amount or 0) > 0) or (sub_amount and float(sub_amount) > 0)) else False

        service = lead.service or (tariff.service if tariff else None)
        if not service and lead.company:
            service = ServicesConsalting.objects.filter(company=lead.company).first()

        effective_sub_amount = sub_amount if sub_amount not in (None, "") else (tariff.subscription_amount if tariff else 0)
        effective_sub_period = sub_period if sub_period not in (None, "") else (tariff.subscription_period if tariff else "month")

        sale = SaleConsalting.objects.filter(lead=lead).first()
        if not sale:
            sale = SaleConsalting.objects.create(
                company=lead.company, branch=lead.branch, user=lead.owner or request.user,
                services=service, tariff=tariff, client=lead.client, lead=lead,
                total=amount,
                paid_months=paid_months,
                subscription_amount=effective_sub_amount or 0,
                subscription_period=effective_sub_period or "month",
                status=SaleConsalting.Status.COMPLETED,
            )
        else:
            sale.paid_months = paid_months
            sale.subscription_amount = effective_sub_amount or 0
            sale.subscription_period = effective_sub_period or "month"
            sale.save(update_fields=["paid_months", "subscription_amount", "subscription_period"])

        from .funnel.cash_confirmation import needs_confirmation
        from .models import CashRequestConsalting, CashOperationConsalting

        if needs_confirmation(lead.company, mode, request.user):
            sale.status = SaleConsalting.Status.PENDING_CONFIRMATION
            sale.save(update_fields=["status"])
            CashRequestConsalting.objects.create(
                company=lead.company,
                sale=sale,
                user=request.user,
                client=lead.client,
                kind=CashRequestConsalting.Kind.SALE,
                direction="income",
                amount=amount,
                payment_method=mode,
                status=CashRequestConsalting.Status.PENDING,
            )
            from .funnel.completion import create_sale_side_effects
            create_sale_side_effects(
                sale,
                subscription_enabled=sub_enabled,
                subscription_start=sub_start,
                subscription_amount=effective_sub_amount,
                subscription_period=effective_sub_period,
                subscription_prepaid_periods=prepaid_periods,
                payment_method=mode,
                actor=request.user,
            )
        else:
            CashOperationConsalting.objects.create(
                company=lead.company,
                user=request.user,
                sale=sale,
                kind=CashOperationConsalting.Kind.SALE,
                direction=CashOperationConsalting.Direction.INCOME,
                amount=amount,
                payment_method=mode,
                comment=f"Оплата по лиду: {lead.title or 'Лид'}",
            )
            from .funnel.completion import create_sale_side_effects, accrue_salary_for_sale
            create_sale_side_effects(
                sale,
                subscription_enabled=sub_enabled,
                subscription_start=sub_start,
                subscription_amount=effective_sub_amount,
                subscription_period=effective_sub_period,
                subscription_prepaid_periods=prepaid_periods,
                payment_method=mode,
                actor=request.user,
            )
            accrue_salary_for_sale(sale, seller=sale.user)
            if tariff and getattr(tariff, "provisions_crm_account", False):
                from .funnel.tenant_lifecycle import provision_tenant_account
                try:
                    provision_tenant_account(client=lead.client, sale=sale, lead=lead, tariff=tariff, actor=request.user)
                except Exception as ex:
                    import logging
                    logging.getLogger("nurcrm.consalting").warning("Auto-provision failed: %s", ex)

        # Цепочка «регион -> внедрение» (§5.1, §5.3)
        funnel = lead.funnel
        if funnel and funnel.next_funnel_id and not funnel.is_final:
            from .funnel.hierarchy import move_lead_to_next_funnel
            move_lead_to_next_funnel(lead, funnel, user=request.user, transition="payment")
            lead.refresh_from_db()

        return Response(
            {"deal_id": str(deal.id), "lead": LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data},
            status=status.HTTP_201_CREATED,
        )


class LeadAllowedTransitionsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Какие стадии доступны для перехода прямо сейчас (для подсветки колонок в UI).
    GET /api/consalting/leads/<uuid:pk>/allowed-transitions/
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage").all()
    serializer_class = LeadConsaltingSerializer

    def get(self, request, *args, **kwargs):
        lead = self.get_object()
        allowed = allowed_next_types(lead.stage)
        stages = FunnelStageConsalting.objects.filter(
            funnel=lead.funnel, stage_type__in=allowed
        ).order_by("order")
        data = [
            {"id": str(s.id), "name": s.name, "stage_type": s.stage_type,
             "order": s.order, "color": s.color}
            for s in stages
        ]
        return Response({"current_stage": str(lead.stage_id) if lead.stage_id else None,
                         "allowed": data})


class LeadTimelineView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    Лента активностей лида (audit trail).
    GET /api/consalting/leads/<uuid:pk>/timeline/
    """
    serializer_class = LeadActivityConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return LeadActivityConsalting.objects.none()
        return LeadActivityConsalting.objects.filter(
            company=company, lead_id=self.kwargs["pk"]
        ).select_related("actor").order_by("-created_at")


class LeadActivityCreateView(CompanyBranchQuerysetMixin, generics.CreateAPIView):
    """
    Добавить активность (note/call/message/meeting/email/file) — пишется через ActivityLogger.
    POST /api/consalting/leads/<uuid:pk>/activities/
    """
    serializer_class = LeadActivityConsaltingSerializer

    def get_queryset(self):
        return LeadActivityConsalting.objects.none()

    def create(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        lead = get_object_or_404(LeadConsalting, pk=self.kwargs["pk"], company=company)

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        activity = ActivityLogger.log(
            lead, v["type"], actor=request.user,
            title=v.get("title", ""), body=v.get("body", ""),
            payload=v.get("payload") or {}, file=v.get("file"),
        )
        # триггер автоматизации (фаза 6)
        emit_funnel_event("activity_added", lead, actor=request.user, activity_type=v["type"])
        return Response(
            LeadActivityConsaltingSerializer(activity, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class LeadRecalculateScoreView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Пересчитать скоринг лида.
    POST /api/consalting/leads/<uuid:pk>/recalculate-score/
    """
    queryset = LeadConsalting.objects.all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        value, grade, changed = ScoringService.recalculate(lead, save=True)
        if changed:
            ActivityLogger.log(
                lead, LeadActivityConsalting.Type.SCORE_CHANGE, actor=request.user,
                title=f"Скоринг: {grade} ({value})",
                payload={"score_value": value, "score_grade": grade},
                touch_last_activity=False,
            )
        lead.refresh_from_db()
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadWinView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """Закрыть лид как выигранный. POST /leads/<id>/win/  { "stage": "<won-stage>"? }"""
    queryset = LeadConsalting.objects.select_related("funnel", "stage").all()
    serializer_class = LeadWinSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        stage = ser.validated_data.get("stage") or FunnelStageConsalting.objects.filter(
            funnel=lead.funnel, stage_type=FunnelStageConsalting.StageType.WON
        ).order_by("order").first()
        if not stage:
            return Response({"detail": "В воронке нет WON-стадии."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        lead.budget_confirmed = True  # выигрыш подразумевает подтверждённый бюджет
        lead.queue_status = "converted"
        lead.converted_at = now
        lead.closed_at = now
        LeadConsalting.objects.filter(pk=lead.pk).update(
            budget_confirmed=True, queue_status="converted", converted_at=now, closed_at=now
        )
        InboundLeadConsalting.objects.filter(lead=lead).update(
            status="converted", converted_at=now, closed_at=now
        )
        try:
            lead = FunnelStateMachine.transition(lead, stage, actor=request.user)
        except StateTransitionError as e:
            return Response({"detail": "Переход запрещён", "errors": e.errors},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadLoseView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """Закрыть лид как проигранный (причина обязательна). POST /leads/<id>/lose/"""
    queryset = LeadConsalting.objects.select_related("funnel", "stage").all()
    serializer_class = LeadLoseSerializer

    def post(self, request, *args, **kwargs):
        company = self._user_company()
        lead = self.get_object()
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data

        loss_reason = v["loss_reason"]
        if company and loss_reason.company_id != company.id:
            return Response({"loss_reason": "Причина из другой компании."},
                            status=status.HTTP_400_BAD_REQUEST)

        stage = v.get("stage") or FunnelStageConsalting.objects.filter(
            funnel=lead.funnel, stage_type=FunnelStageConsalting.StageType.LOST
        ).order_by("order").first()
        if not stage:
            return Response({"detail": "В воронке нет LOST-стадии."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        lead.loss_reason = loss_reason
        lead.loss_comment = v.get("loss_comment", "")
        lead.queue_status = "rejected"
        lead.reject_reason = loss_reason.label if loss_reason else ""
        lead.reject_comment = v.get("loss_comment", "")
        lead.closed_at = now
        LeadConsalting.objects.filter(pk=lead.pk).update(
            loss_reason=loss_reason,
            loss_comment=lead.loss_comment,
            queue_status="rejected",
            reject_reason=lead.reject_reason,
            reject_comment=lead.reject_comment,
            closed_at=now,
        )
        InboundLeadConsalting.objects.filter(lead=lead).update(
            status="rejected",
            reject_reason=lead.reject_reason,
            reject_comment=lead.reject_comment,
            closed_at=now,
        )
        try:
            lead = FunnelStateMachine.transition(lead, stage, actor=request.user)
        except StateTransitionError as e:
            return Response({"detail": "Переход запрещён", "errors": e.errors},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(LeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class LeadFunnelHistoryView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """GET /api/consalting/leads/{id}/funnel-history/ — история движения лида по воронкам (§3.6)."""
    serializer_class = LeadFunnelHistoryConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return LeadFunnelHistoryConsalting.objects.none()

        lead_id = self.kwargs.get("pk") or self.kwargs.get("lead_id")
        lead = get_object_or_404(LeadConsalting, pk=lead_id, company=company)
        return LeadFunnelHistoryConsalting.objects.filter(lead=lead).select_related("funnel", "stage", "owner").order_by("entered_at")


# ==========================
# LeadTaskConsalting (задачи по лиду)
# ==========================
class LeadTaskListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = LeadTaskConsalting.objects.select_related("lead", "assignee").all()
    serializer_class = LeadTaskConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["lead", "assignee", "status", "type"]

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        lead = serializer.validated_data["lead"]
        if lead.company_id != company.id:
            raise PermissionDenied("Лид из другой компании.")
        task = serializer.save(
            company=company, branch=lead.branch, created_by=self.request.user
        )
        # задача = следующий шаг по лиду
        LeadConsalting.objects.filter(pk=lead.pk).update(
            next_action_type=task.type, next_action_date=task.due_date,
            next_action_note=task.title,
        )
        ActivityLogger.log(
            lead, LeadActivityConsalting.Type.TASK, actor=self.request.user,
            title=f"Задача: {task.title}", payload={"task_id": str(task.id)},
            touch_last_activity=False,
        )
        if task.assignee_id:
            realtime.notify_user(
                task.assignee_id,
                "consulting.lead.task.assigned",
                {
                    "title": f"Поручена задача: {task.title}",
                    "message": f"Срок: {task.due_date.strftime('%Y-%m-%d %H:%M') if task.due_date else 'Не указан'}",
                    "lead_id": str(lead.id),
                    "task_id": str(task.id),
                }
            )


class LeadTaskRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = LeadTaskConsalting.objects.select_related("lead", "assignee").all()
    serializer_class = LeadTaskConsaltingSerializer

    def perform_update(self, serializer):
        task = serializer.save()
        # при завершении задачи фиксируем время
        if task.status == LeadTaskConsalting.Status.DONE and task.completed_at is None:
            task.completed_at = timezone.now()
            task.save(update_fields=["completed_at"])


# ==========================
# LossReasonConsalting (справочник причин)
# ==========================
class LossReasonListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = LossReasonConsalting.objects.all()
    serializer_class = LossReasonConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["is_active"]


class LossReasonRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = LossReasonConsalting.objects.all()
    serializer_class = LossReasonConsaltingSerializer


# ==========================
# Аналитика воронки
# ==========================
class FunnelAnalyticsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Метрики воронки: конверсия по стадиям, время в стадии, drop-off, win-rate.
    GET /api/consalting/funnels/<uuid:pk>/analytics/?date_from=&date_to=&branch=&owner=
    """
    queryset = FunnelConsalting.objects.all()
    serializer_class = FunnelConsaltingSerializer

    def get(self, request, *args, **kwargs):
        funnel = self.get_object()
        params = request.query_params
        data = PipelineAnalytics.compute(
            funnel,
            date_from=params.get("date_from") or None,
            date_to=params.get("date_to") or None,
            branch=params.get("branch") or None,
            owner=params.get("owner") or None,
        )
        return Response(data)


# ==========================
# WhatsApp Integration (каркас)
# ==========================
from django.conf import settings
from .funnel.whatsapp import WhatsAppConsaltingService
from .serializers import WhatsAppMessageConsaltingSerializer, WhatsAppSendSerializer
from .models import WhatsAppMessageConsalting

class LeadWhatsAppSendView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Отправка сообщения клиенту через WhatsApp в контексте лида.
    POST /leads/<id>/whatsapp/send/
    """
    queryset = LeadConsalting.objects.all()
    serializer_class = WhatsAppSendSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        text = ser.validated_data["text"]

        try:
            wa_message = WhatsAppConsaltingService.send_message(
                lead=lead,
                text=text,
                user=request.user
            )
            return Response(
                WhatsAppMessageConsaltingSerializer(wa_message).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class LeadWhatsAppHistoryView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    Получение истории переписки по WhatsApp для конкретного лида.
    GET /leads/<id>/whatsapp/history/
    """
    serializer_class = WhatsAppMessageConsaltingSerializer

    def get_queryset(self):
        lead_id = self.kwargs.get("pk")
        company = self._user_company()
        return WhatsAppMessageConsalting.objects.filter(
            lead_id=lead_id,
            company=company
        ).order_by("created_at")


class WhatsAppConsaltingWebhookView(APIView):
    """
    Прием входящих сообщений и обновлений статуса от шлюза WhatsApp (Meta Cloud API или Node.js).
    GET /whatsapp/webhook/ -> Верификация Webhook Meta (hub.mode, hub.verify_token, hub.challenge)
    POST /whatsapp/webhook/ -> Обработка событий сообщения/статуса от Meta Cloud API или Node.js
    """
    permission_classes = []
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        """
        Верификация вебхука от Meta WhatsApp Cloud API.
        """
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        verify_token = (
            getattr(settings, "WHATSAPP_VERIFY_TOKEN", None)
            or getattr(settings, "META_WA_VERIFY_TOKEN", None)
            or getattr(settings, "WHATSAPP_NODE_TOKEN", "change-me")
        )

        if mode == "subscribe" and token == verify_token:
            return HttpResponse(challenge or "", content_type="text/plain", status=200)
        return Response({"detail": "Forbidden verification token"}, status=status.HTTP_403_FORBIDDEN)

    def post(self, request, *args, **kwargs):
        # 1. Проверяем payload Meta WhatsApp Cloud API (object/entry)
        if "object" in request.data and "entry" in request.data:
            entries = request.data.get("entry", [])
            processed_count = 0
            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    val = change.get("value", {})
                    # Сообщения
                    messages = val.get("messages", [])
                    for msg in messages:
                        phone = msg.get("from")
                        msg_id = msg.get("id")
                        msg_type = msg.get("type")
                        text_body = ""
                        if msg_type == "text":
                            text_body = msg.get("text", {}).get("body", "")
                        elif msg_type in ["image", "document", "audio", "video"]:
                            text_body = msg.get(msg_type, {}).get("caption", f"[{msg_type.upper()}]")

                        if phone and msg_id:
                            company_id = request.query_params.get("company_id")
                            if not company_id:
                                company = Company.objects.first()
                                company_id = company.id if company else None

                            if company_id:
                                WhatsAppConsaltingService.handle_incoming_message(
                                    company_id=company_id,
                                    phone=phone,
                                    text=text_body or "[Входящее сообщение]",
                                    message_id=msg_id
                                )
                                processed_count += 1

                    # Статусы
                    statuses = val.get("statuses", [])
                    for st in statuses:
                        st_id = st.get("id")
                        st_val = st.get("status")
                        if st_id and st_val:
                            WhatsAppConsaltingService.update_message_status(st_id, st_val)
                            processed_count += 1

            return Response({"status": "success", "processed": processed_count}, status=status.HTTP_200_OK)

        # 2. Формат Node.js / Кастомного шлюза
        token = request.headers.get("X-WA-TOKEN")
        expected_token = getattr(settings, "WHATSAPP_NODE_TOKEN", "change-me")
        if token and token != expected_token:
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        event_type = request.data.get("event")
        
        if event_type == "message":
            company_id = request.data.get("company_id")
            phone = request.data.get("phone")
            text = request.data.get("text")
            message_id = request.data.get("message_id")
            
            if not all([company_id, phone, text, message_id]):
                return Response(
                    {"detail": "Недостаточно данных для обработки сообщения"},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            try:
                lead = WhatsAppConsaltingService.handle_incoming_message(
                    company_id=company_id,
                    phone=phone,
                    text=text,
                    message_id=message_id
                )
                return Response(
                    {
                        "status": "success",
                        "lead_id": str(lead.id)
                    },
                    status=status.HTTP_200_OK
                )
            except Exception as e:
                return Response(
                    {"detail": str(e)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
                
        elif event_type == "status":
            message_id = request.data.get("message_id")
            status_str = request.data.get("status")
            
            if not message_id or not status_str:
                return Response(
                    {"detail": "Недостаточно данных для обновления статуса"},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            WhatsAppConsaltingService.update_message_status(
                message_id=message_id,
                status_str=status_str
            )
            return Response({"status": "success"}, status=status.HTTP_200_OK)
            
        return Response(
            {"detail": "Неподдерживаемый тип события"},
            status=status.HTTP_400_BAD_REQUEST
        )


# ==========================
# Salary Auto-Accrual Views (docs-consaltion/salary-auto-accrual.md)
# ==========================
# ==========================
# Salary System (02-salary.md)
# ==========================
class ServiceSalaryRateListView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """GET /api/consalting/salary/rates/  — список ставок по услугам."""
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        services = ServicesConsalting.objects.filter(company=company)
        search = request.query_params.get("search")
        if search:
            services = services.filter(name__icontains=search)

        rates_dict = {
            r.service_id: r for r in ServiceSalaryRateConsalting.objects.filter(company=company)
        }

        results = []
        for svc in services:
            rate = rates_dict.get(svc.id)
            results.append({
                "service": str(svc.id),
                "service_name": svc.name,
                "price": float(svc.price or 0),
                "percent": str(rate.percent) if rate else "0.00",
                "fixed_amount": str(rate.fixed_amount) if rate else "0.00",
                "updated_at": rate.updated_at.isoformat() if rate else None,
            })
        return Response({"results": results})


class ServiceSalaryRateUpdateView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """PUT /api/consalting/salary/rates/<service_id>/ — установить процент и фикс за сделку."""
    def put(self, request, service_id, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Управлять ставками может только руководитель.")

        svc = get_object_or_404(ServicesConsalting, pk=service_id, company=company)
        from decimal import Decimal, InvalidOperation

        percent = request.data.get("percent", 0)
        fixed_amount = request.data.get("fixed_amount", 0)
        try:
            percent_dec = Decimal(str(percent))
            if percent_dec < 0 or percent_dec > 100:
                raise ValueError()
        except (ValueError, TypeError, InvalidOperation):
            return Response({"percent": "Значение процента должно быть от 0 до 100."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            fixed_dec = Decimal(str(fixed_amount))
            if fixed_dec < 0:
                raise ValueError()
        except (ValueError, TypeError, InvalidOperation):
            return Response({"fixed_amount": "Значение фиксированной ставки должно быть >= 0."}, status=status.HTTP_400_BAD_REQUEST)

        rate, _ = ServiceSalaryRateConsalting.objects.get_or_create(
            company=company, service=svc, defaults={"percent": percent_dec, "fixed_amount": fixed_dec}
        )
        rate.percent = percent_dec
        rate.fixed_amount = fixed_dec
        rate.save(update_fields=["percent", "fixed_amount", "updated_at"])

        return Response({
            "service": str(svc.id),
            "service_name": svc.name,
            "price": float(svc.price or 0),
            "percent": str(rate.percent),
            "fixed_amount": str(rate.fixed_amount),
            "updated_at": rate.updated_at.isoformat()
        })


class SalarySchemesListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """GET /api/consalting/salary/schemes/ — список сотрудников со схемами."""
    serializer_class = SalarySchemeConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company or not is_owner_like(self.request.user):
            return SalarySchemeConsalting.objects.none()

        users = User.objects.filter(company=company)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None and is_active.lower() in ("true", "1", "false", "0"):
            users = users.filter(is_active=(is_active.lower() in ("true", "1")))

        search = self.request.query_params.get("search")
        if search:
            users = users.filter(
                Q(first_name__icontains=search) | Q(last_name__icontains=search) | Q(email__icontains=search)
            )

        for u in users:
            SalarySchemeConsalting.objects.get_or_create(company=company, user=u)

        return SalarySchemeConsalting.objects.filter(company=company, user__in=users).select_related("user").prefetch_related("service_overrides__service")


class SalarySchemeDetailView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """GET/PUT /api/consalting/salary/schemes/{user_id}/ — схема сотрудника."""
    serializer_class = SalarySchemeConsaltingSerializer

    def get(self, request, user_id, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Просматривать схемы может только руководитель.")

        target_user = get_object_or_404(User, pk=user_id, company=company)
        scheme, _ = SalarySchemeConsalting.objects.get_or_create(company=company, user=target_user)
        return Response(SalarySchemeConsaltingSerializer(scheme, context=self.get_serializer_context()).data)

    def put(self, request, user_id, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Настраивать схемы может только руководитель.")

        target_user = get_object_or_404(User, pk=user_id, company=company)
        scheme, _ = SalarySchemeConsalting.objects.get_or_create(company=company, user=target_user)

        from decimal import Decimal
        data = request.data
        base_salary_enabled = bool(data.get("base_salary_enabled", False))
        base_salary = Decimal(str(data.get("base_salary", 0) or 0))
        base_salary_period = str(data.get("base_salary_period", "month"))

        percent_enabled = bool(data.get("percent_enabled", False))
        percent = Decimal(str(data.get("percent", 0) or 0))

        fixed_enabled = bool(data.get("fixed_enabled", False))
        fixed_amount = Decimal(str(data.get("fixed_amount", 0) or 0))

        if percent < 0 or percent > 100:
            return Response({"percent": "Процент должен быть от 0 до 100."}, status=status.HTTP_400_BAD_REQUEST)
        if base_salary < 0 or fixed_amount < 0:
            return Response({"detail": "Суммы должны быть неотрицательными."}, status=status.HTTP_400_BAD_REQUEST)

        overrides_data = data.get("service_overrides", [])
        seen_services = set()
        validated_overrides = []

        for item in overrides_data:
            svc_id = item.get("service")
            if not svc_id or svc_id in seen_services:
                return Response({"service_overrides": "Дубликаты или некорректные ID услуг недопустимы."}, status=status.HTTP_400_BAD_REQUEST)
            svc = get_object_or_404(ServicesConsalting, pk=svc_id, company=company)
            ov_pct = Decimal(str(item.get("percent", 0) or 0))
            ov_fix = Decimal(str(item.get("fixed_amount", 0) or 0))
            if ov_pct < 0 or ov_pct > 100 or ov_fix < 0:
                return Response({"service_overrides": "Некорректные значения ставок по услуге."}, status=status.HTTP_400_BAD_REQUEST)
            seen_services.add(svc_id)
            validated_overrides.append((svc, ov_pct, ov_fix))

        with transaction.atomic():
            scheme.base_salary_enabled = base_salary_enabled
            scheme.base_salary = base_salary
            scheme.base_salary_period = base_salary_period
            scheme.percent_enabled = percent_enabled
            scheme.percent = percent
            scheme.fixed_enabled = fixed_enabled
            scheme.fixed_amount = fixed_amount
            scheme.save()

            scheme.service_overrides.all().delete()
            for svc, ov_pct, ov_fix in validated_overrides:
                SalarySchemeServiceOverrideConsalting.objects.create(
                    scheme=scheme, service=svc, percent=ov_pct, fixed_amount=ov_fix
                )

        return Response(SalarySchemeConsaltingSerializer(scheme, context=self.get_serializer_context()).data)


class SalaryDefaultsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """GET/PUT /api/consalting/salary/defaults/ — дефолтные ставки компании."""
    serializer_class = SalaryDefaultsConsaltingSerializer

    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        defaults, _ = SalaryDefaultsConsalting.objects.get_or_create(company=company)
        return Response(SalaryDefaultsConsaltingSerializer(defaults, context=self.get_serializer_context()).data)

    def put(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Управлять дефолтами компании может только руководитель.")

        defaults, _ = SalaryDefaultsConsalting.objects.get_or_create(company=company)
        ser = SalaryDefaultsConsaltingSerializer(defaults, data=request.data, partial=True, context=self.get_serializer_context())
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class BonusRuleListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """GET/POST /api/consalting/salary/bonus-rules/ — правила премий."""
    serializer_class = BonusRuleConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return BonusRuleConsalting.objects.none()
        return BonusRuleConsalting.objects.filter(company=company).prefetch_related("tiers")

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Управлять правилами премий может только руководитель.")
        serializer.save(company=company)


class BonusRuleDetailView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """PATCH/DELETE /api/consalting/salary/bonus-rules/<id>/ — правка/удаление правила."""
    serializer_class = BonusRuleConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return BonusRuleConsalting.objects.none()
        return BonusRuleConsalting.objects.filter(company=company).prefetch_related("tiers")

    def perform_update(self, serializer):
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Управлять правилами премий может только руководитель.")
        serializer.save()

    def perform_destroy(self, instance):
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Управлять правилами премий может только руководитель.")
        instance.delete()


class BonusProgressView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """GET /api/consalting/salary/bonus-progress/ — прогресс к премиям."""
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            return Response({"results": []})

        user = request.user
        user_param = request.query_params.get("user")
        if user_param and is_owner_like(user):
            target_user = get_object_or_404(User, pk=user_param, company=company)
        else:
            target_user = user

        from decimal import Decimal
        now = timezone.now()
        date_from_str = request.query_params.get("date_from")
        date_to_str = request.query_params.get("date_to")
        if date_from_str and date_to_str:
            period_from = parse_date(date_from_str)
            period_to = parse_date(date_to_str)
        else:
            period_from = now.replace(day=1).date()
            period_to = now.date()

        rules = BonusRuleConsalting.objects.filter(company=company, is_active=True).prefetch_related("tiers")
        results = []

        sales_base = SaleConsalting.objects.filter(
            company=company, user=target_user, created_at__date__gte=period_from, created_at__date__lte=period_to
        )

        for rule in rules:
            if rule.applies_to == "user" and rule.user_id != target_user.id:
                continue
            if rule.applies_to == "role" and target_user.custom_role_id != rule.role_id:
                continue

            sales = sales_base
            if rule.condition == BonusRuleConsalting.Condition.SERVICE_COUNT and rule.service_id:
                sales = sales.filter(services_id=rule.service_id)

            if rule.condition in (BonusRuleConsalting.Condition.SERVICE_COUNT, BonusRuleConsalting.Condition.DEALS_COUNT):
                current_val = Decimal(sales.count())
                unit = "count"
            else:
                current_val = Decimal(sales.aggregate(s=Sum("total"))["s"] or 0)
                unit = "money"

            target_val = rule.threshold or Decimal("0")
            reward_val = Decimal("0")
            achieved = False

            if rule.condition == BonusRuleConsalting.Condition.REVENUE_LADDER:
                highest_tier = rule.tiers.order_by("-from_amount").first()
                if highest_tier:
                    target_val = highest_tier.from_amount
                matched_tier = rule.tiers.filter(from_amount__lte=current_val).order_by("-from_amount").first()
                if matched_tier:
                    reward_val = (current_val * matched_tier.percent / Decimal("100")).quantize(Decimal("0.01"))
                    achieved = True
            else:
                if current_val >= target_val and target_val > 0:
                    achieved = True
                    if rule.reward_type == "fixed":
                        reward_val = rule.reward_value or Decimal("0")
                    else:
                        reward_val = (current_val * (rule.reward_value or Decimal("0")) / Decimal("100")).quantize(Decimal("0.01"))

            left_val = max(Decimal("0"), target_val - current_val)
            results.append({
                "rule": str(rule.id),
                "name": rule.name,
                "current": float(current_val),
                "target": float(target_val),
                "left": float(left_val),
                "unit": unit,
                "reward": float(reward_val),
                "achieved": achieved,
            })

        return Response({"results": results})


class SalaryAdjustmentListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """GET/POST /api/consalting/salary/adjustments/ — штрафы, удержания и разовые премии."""
    serializer_class = SalaryAdjustmentConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return SalaryAdjustmentConsalting.objects.none()
        qs = SalaryAdjustmentConsalting.objects.filter(company=company).select_related("user", "created_by")
        if not is_owner_like(self.request.user):
            qs = qs.filter(user=self.request.user)

        user_param = self.request.query_params.get("user")
        if user_param and is_owner_like(self.request.user):
            qs = qs.filter(user_id=user_param)

        kind_param = self.request.query_params.get("kind")
        if kind_param:
            qs = qs.filter(kind=kind_param)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Создавать штрафы и премии может только руководитель.")

        with transaction.atomic():
            adj = serializer.save(company=company, created_by=self.request.user)
            SalaryAccrualConsalting.objects.create(
                company=company,
                user=adj.user,
                kind=adj.kind,
                amount=adj.amount,
                base_amount=adj.amount,
                status=SalaryAccrualConsalting.Status.ACCRUED,
            )


class SalaryAdjustmentCancelView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """POST /api/consalting/salary/adjustments/{id}/cancel/ — отмена корректировки."""
    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Отменять штрафы и премии может только руководитель.")

        adj = get_object_or_404(SalaryAdjustmentConsalting, pk=pk, company=company)
        adj.status = "canceled"
        adj.save(update_fields=["status", "updated_at"])

        return Response(SalaryAdjustmentConsaltingSerializer(adj, context=self.get_serializer_context()).data)


class SalaryPayslipView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """GET /api/consalting/salary/payslip/?user=<uuid>&month=YYYY-MM — расчётный лист."""
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        user_param = request.query_params.get("user")
        if not is_owner_like(request.user) or not user_param:
            target_user = request.user
        else:
            target_user = get_object_or_404(User, pk=user_param, company=company)

        month_str = request.query_params.get("month")
        if not month_str:
            month_str = timezone.now().strftime("%Y-%m")

        try:
            year, m_val = map(int, month_str.split("-"))
        except Exception:
            return Response({"month": "Неверный формат месяца (ожидается YYYY-MM)."}, status=status.HTTP_400_BAD_REQUEST)

        user_display = f"{target_user.first_name or ''} {target_user.last_name or ''}".strip() or target_user.email

        accruals = SalaryAccrualConsalting.objects.filter(
            company=company, user=target_user,
            created_at__year=year, created_at__month=m_val
        ).exclude(status=SalaryAccrualConsalting.Status.CANCELED)

        label_map = {
            SalaryAccrualConsalting.Kind.SALARY: "Оклад",
            SalaryAccrualConsalting.Kind.PERCENT: "Процент со сделок",
            SalaryAccrualConsalting.Kind.FIXED: "Фикс за сделки",
            SalaryAccrualConsalting.Kind.BONUS: "Премии за планы",
            SalaryAccrualConsalting.Kind.MANUAL_BONUS: "Разовые премии",
            SalaryAccrualConsalting.Kind.FINE: "Штрафы",
            SalaryAccrualConsalting.Kind.DEDUCTION: "Удержания (отмены продаж)",
        }

        from decimal import Decimal
        kind_stats = {}
        for k_choice in SalaryAccrualConsalting.Kind.values:
            kind_stats[k_choice] = {"amount": Decimal("0"), "count": 0}

        for acc in accruals:
            k = acc.kind
            if k in kind_stats:
                kind_stats[k]["amount"] += acc.amount
                kind_stats[k]["count"] += 1

        lines = []
        positive_sum = Decimal("0")
        negative_sum = Decimal("0")

        negative_kinds = {SalaryAccrualConsalting.Kind.FINE, SalaryAccrualConsalting.Kind.DEDUCTION}

        for k_choice, stats in kind_stats.items():
            amt = stats["amount"]
            cnt = stats["count"]
            lines.append({
                "kind": k_choice,
                "label": label_map.get(k_choice, k_choice),
                "amount": float(amt),
                "count": cnt
            })
            if k_choice in negative_kinds:
                negative_sum += amt
            else:
                positive_sum += amt

        total_accrued = positive_sum - negative_sum

        payouts = SalaryPayoutConsalting.objects.filter(
            company=company, user=target_user,
            created_at__year=year, created_at__month=m_val
        )
        total_paid = payouts.aggregate(s=Sum("amount"))["s"] or Decimal("0")
        to_pay = total_accrued - total_paid

        return Response({
            "user": str(target_user.id),
            "user_display": user_display,
            "period": month_str,
            "lines": lines,
            "accrued": float(total_accrued),
            "paid": float(total_paid),
            "to_pay": float(to_pay),
        })


class SalaryAccrualListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """GET /api/consalting/salary/accruals/ — список начислений зарплаты."""
    serializer_class = SalaryAccrualConsaltingSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["service", "user", "kind"]
    search_fields = ["user__first_name", "user__last_name", "user__email", "service__name"]
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return SalaryAccrualConsalting.objects.none()
        qs = SalaryAccrualConsalting.objects.filter(company=company).select_related("user", "service", "sale", "lead", "rule")
        if not is_owner_like(self.request.user):
            qs = qs.filter(user=self.request.user)

        user_param = self.request.query_params.get("user")
        if user_param and is_owner_like(self.request.user):
            qs = qs.filter(user_id=user_param)

        kind_param = self.request.query_params.get("kind")
        if kind_param:
            qs = qs.filter(kind=kind_param)

        date_from = self.request.query_params.get("date_from") or self.request.query_params.get("period_start")
        date_to = self.request.query_params.get("date_to") or self.request.query_params.get("period_end")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        status = self.request.query_params.get("status")
        if status:
            valid_statuses = {s.value for s in SalaryAccrualConsalting.Status}
            if status == SalaryAccrualConsalting.Status.PENDING:
                qs = qs.filter(status__in=[
                    SalaryAccrualConsalting.Status.PENDING,
                    SalaryAccrualConsalting.Status.ACCRUED,
                ])
            elif status in valid_statuses:
                qs = qs.filter(status=status)

        return qs



class SalarySummaryView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """GET /api/consalting/salary/summary/ — сводка по зарплате за период."""
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        qs = SalaryAccrualConsalting.objects.filter(company=company)
        payouts_qs = SalaryPayoutConsalting.objects.filter(company=company)

        if not is_owner_like(request.user):
            qs = qs.filter(user=request.user)
            payouts_qs = payouts_qs.filter(user=request.user)

        date_from = request.query_params.get("date_from") or request.query_params.get("period_start")
        date_to = request.query_params.get("date_to") or request.query_params.get("period_end")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
            payouts_qs = payouts_qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
            payouts_qs = payouts_qs.filter(created_at__date__lte=date_to)

        accrued = float(qs.filter(status__in=["accrued", "paid"]).aggregate(s=Sum("amount"))["s"] or 0)
        paid = float(payouts_qs.aggregate(s=Sum("amount"))["s"] or 0)
        remaining = max(0.0, accrued - paid)

        by_user_dict = {}
        for r in qs.filter(status__in=["accrued", "paid"]).values("user_id", "user__first_name", "user__last_name", "user__email").annotate(a=Sum("amount")):
            uid = str(r["user_id"])
            uname = f"{r['user__first_name'] or ''} {r['user__last_name'] or ''}".strip() or r["user__email"]
            by_user_dict[uid] = {"user": uid, "name": uname, "accrued": float(r["a"] or 0), "paid": 0.0, "remaining": float(r["a"] or 0)}

        for r in payouts_qs.values("user_id", "user__first_name", "user__last_name", "user__email").annotate(p=Sum("amount")):
            uid = str(r["user_id"])
            uname = f"{r['user__first_name'] or ''} {r['user__last_name'] or ''}".strip() or r["user__email"]
            if uid not in by_user_dict:
                by_user_dict[uid] = {"user": uid, "name": uname, "accrued": 0.0, "paid": 0.0, "remaining": 0.0}
            by_user_dict[uid]["paid"] = float(r["p"] or 0)
            by_user_dict[uid]["remaining"] = max(0.0, by_user_dict[uid]["accrued"] - by_user_dict[uid]["paid"])

        return Response({
            "totals": {"accrued": accrued, "paid": paid, "remaining": remaining},
            "by_user": list(by_user_dict.values())
        })


class SalaryPayoutListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    GET /api/consalting/salary/payouts/ — список выплат.
    POST /api/consalting/salary/payouts/ — создание выплаты (закрывает начисления по FIFO).
    """
    serializer_class = SalaryPayoutConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return SalaryPayoutConsalting.objects.none()
        qs = SalaryPayoutConsalting.objects.filter(company=company).select_related("user")
        if not is_owner_like(self.request.user):
            qs = qs.filter(user=self.request.user)
        return qs

    def create(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Выплачивать зарплату может только руководитель.")

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.validated_data["user"]
        amount = ser.validated_data["amount"]

        if amount <= 0:
            return Response({"amount": "Сумма выплаты должна быть больше 0."}, status=status.HTTP_400_BAD_REQUEST)

        accrued_qs = SalaryAccrualConsalting.objects.filter(
            company=company, user=user, status=SalaryAccrualConsalting.Status.ACCRUED
        ).order_by("created_at")

        total_accrued = accrued_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
        if amount > total_accrued:
            return Response(
                {"detail": f"Сумма выплаты ({amount}) превышает доступные начисления ({total_accrued})."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            payout = ser.save(company=company)
            remaining_to_pay = amount
            for accrual in accrued_qs:
                if remaining_to_pay <= 0:
                    break
                if accrual.amount <= remaining_to_pay:
                    remaining_to_pay -= accrual.amount
                    accrual.status = SalaryAccrualConsalting.Status.PAID
                    accrual.payout = payout
                    accrual.save(update_fields=["status", "payout", "updated_at"])

        return Response(
            SalaryPayoutConsaltingSerializer(payout, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED
        )


# ==========================
# Inbound Leads & Lead Distribution (docs-consaltion/leads-whatsapp.md)
# ==========================
def distribute_inbound_lead(inbound_lead):
    """Автоматическое распределение лида сотрудникам по правилам компании (§5)."""
    try:
        company = inbound_lead.company
        settings, _ = LeadDistributionSettingsConsalting.objects.get_or_create(company=company)

        if not settings.enabled or settings.strategy == LeadDistributionSettingsConsalting.Strategy.MANUAL:
            return inbound_lead

        with transaction.atomic():
            settings = LeadDistributionSettingsConsalting.objects.select_for_update().get(pk=settings.pk)

            target_roles = list(settings.roles.values_list("id", flat=True))
            if not target_roles:
                return inbound_lead

            pool_qs = User.objects.filter(
                company=company, is_active=True, custom_role_id__in=target_roles
            ).order_by("id")
            pool = list(pool_qs)

            if not pool:
                return inbound_lead

            chosen_owner = None
            if settings.strategy == LeadDistributionSettingsConsalting.Strategy.ROUND_ROBIN:
                cursor = settings._rr_cursor
                chosen_owner = pool[cursor % len(pool)]
                settings._rr_cursor = cursor + 1
                settings.save(update_fields=["_rr_cursor"])

            elif settings.strategy == LeadDistributionSettingsConsalting.Strategy.LEAST_LOADED:
                active_statuses = [
                    InboundLeadConsalting.Status.NEW,
                    InboundLeadConsalting.Status.ASSIGNED,
                    InboundLeadConsalting.Status.IN_WORK,
                ]
                counts = (
                    InboundLeadConsalting.objects.filter(
                        company=company, owner__in=pool, status__in=active_statuses
                    )
                    .values("owner_id")
                    .annotate(c=Count("id"))
                )
                counts_dict = {r["owner_id"]: r["c"] for r in counts}

                min_cnt = min(counts_dict.get(u.id, 0) for u in pool)
                candidates = [u for u in pool if counts_dict.get(u.id, 0) == min_cnt]

                cursor = settings._rr_cursor
                chosen_owner = candidates[cursor % len(candidates)]
                settings._rr_cursor = cursor + 1
                settings.save(update_fields=["_rr_cursor"])

            if chosen_owner:
                inbound_lead.owner = chosen_owner
                inbound_lead.status = InboundLeadConsalting.Status.ASSIGNED
                inbound_lead.save(update_fields=["owner", "status", "updated_at"])

                realtime.notify_user(
                    chosen_owner.id,
                    "lead.assigned",
                    {
                        "id": str(inbound_lead.id),
                        "full_name": inbound_lead.full_name,
                        "phone": inbound_lead.phone,
                        "source": inbound_lead.source,
                        "message": inbound_lead.message,
                        "status": inbound_lead.status,
                        "created_at": inbound_lead.created_at.isoformat(),
                    }
                )

        return inbound_lead
    except Exception as e:
        logger.exception("Inbound lead distribution failed: %s", e)
        return inbound_lead


class InboundLeadPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500


class InboundLeadListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    GET /api/consalting/inbound-leads/ — список входящих лидов.
    POST /api/consalting/inbound-leads/ — ручное создание лида.
    """
    serializer_class = InboundLeadConsaltingSerializer
    pagination_class = InboundLeadPagination

    def get_queryset(self):
        import uuid
        company = self._user_company()
        if not company:
            return InboundLeadConsalting.objects.none()

        qs = InboundLeadConsalting.objects.filter(company=company).select_related("owner", "sale", "lead")

        user = self.request.user
        is_mgr = is_owner_like(user)
        is_sup = is_consulting_supervisor(user)

        if is_sup:
            my_regions = user.get_consulting_region_codes()
            qs = qs.filter(region_code__in=my_regions)
            region_param = self.request.query_params.get("region")
            if region_param and region_param in my_regions:
                qs = qs.filter(region_code=region_param)
            owner_param = self.request.query_params.get("owner")
            if owner_param:
                op_lower = owner_param.lower().strip()
                if op_lower in ("none", "null", "unassigned"):
                    qs = qs.filter(owner__isnull=True)
                elif op_lower in ("mine", "my"):
                    qs = qs.filter(owner=user)
                elif op_lower not in ("all", ""):
                    try:
                        uuid_val = uuid.UUID(owner_param)
                        qs = qs.filter(owner_id=uuid_val)
                    except ValueError:
                        pass
        elif not is_mgr:
            if not getattr(user, "can_view_leads_inbox", False):
                raise PermissionDenied("У вас нет доступа к входящим лидам.")
            qs = qs.filter(owner=user)
        else:
            region_param = self.request.query_params.get("region")
            if region_param:
                qs = qs.filter(region_code=region_param)
            owner_param = self.request.query_params.get("owner")
            if owner_param:
                op_lower = owner_param.lower().strip()
                if op_lower in ("none", "null", "unassigned"):
                    qs = qs.filter(owner__isnull=True)
                elif op_lower in ("mine", "my"):
                    qs = qs.filter(owner=user)
                elif op_lower not in ("all", ""):
                    try:
                        uuid_val = uuid.UUID(owner_param)
                        qs = qs.filter(owner_id=uuid_val)
                    except ValueError:
                        pass

        status_param = self.request.query_params.get("status")
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            if statuses:
                qs = qs.filter(status__in=statuses)

        source_param = self.request.query_params.get("source")
        if source_param:
            qs = qs.filter(source=source_param)

        search_param = self.request.query_params.get("search")
        if search_param:
            qs = qs.filter(
                Q(full_name__icontains=search_param) |
                Q(phone__icontains=search_param) |
                Q(message__icontains=search_param)
            )

        date_from = self.request.query_params.get("date_from")
        if date_from:
            try:
                from datetime import datetime
                df = datetime.strptime(date_from.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=df)
            except ValueError:
                pass

        date_to = self.request.query_params.get("date_to")
        if date_to:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_to.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=dt)
            except ValueError:
                pass

        overdue_param = self.request.query_params.get("overdue")
        if overdue_param and overdue_param.lower() in ("true", "1"):
            qs = qs.filter(
                status=InboundLeadConsalting.Status.DEFERRED,
                remind_at__lte=timezone.now()
            )

        ordering = self.request.query_params.get("ordering", "-created_at")
        allowed_orderings = {
            "created_at": "created_at",
            "-created_at": "-created_at",
            "updated_at": "updated_at",
            "-updated_at": "-updated_at",
            "remind_at": "remind_at",
            "-remind_at": "-remind_at",
            "full_name": "full_name",
            "-full_name": "-full_name",
            "status": "status",
            "-status": "-status",
        }
        ord_field = allowed_orderings.get(ordering, "-created_at")
        qs = qs.order_by(ord_field)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        inbound_lead = serializer.save(company=company)
        if is_consulting_supervisor(self.request.user):
            my_regions = self.request.user.get_consulting_region_codes()
            if my_regions and not inbound_lead.region_code:
                inbound_lead.region_code = my_regions[0]
                inbound_lead.save(update_fields=["region_code"])
        distribute_inbound_lead(inbound_lead)


class InboundLeadRetrieveUpdateView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateAPIView):
    """
    GET/PATCH /api/consalting/inbound-leads/<id>/ — детализация и смена статуса/полей.
    """
    serializer_class = InboundLeadConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return InboundLeadConsalting.objects.none()
        qs = InboundLeadConsalting.objects.filter(company=company).select_related("owner")
        if is_consulting_supervisor(self.request.user):
            qs = qs.filter(region_code__in=self.request.user.get_consulting_region_codes())
        elif not is_owner_like(self.request.user):
            qs = qs.filter(owner=self.request.user)
        return qs


class InboundLeadAssignView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/inbound-leads/<id>/assign/ — ручное назначение владельца лида.
    """
    serializer_class = InboundLeadConsaltingSerializer

    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        is_mgr = is_owner_like(request.user)
        is_sup = is_consulting_supervisor(request.user)
        if not is_mgr and not is_sup:
            raise PermissionDenied("Назначать лиды может только руководитель.")

        inbound_lead = get_object_or_404(InboundLeadConsalting, pk=pk, company=company)

        if is_sup:
            my_regions = request.user.get_consulting_region_codes()
            if inbound_lead.region_code not in my_regions:
                raise PermissionDenied("Нет доступа к лидам вне вашего региона.")

        owner_id = request.data.get("owner")
        if not owner_id:
            return Response({"owner": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)

        new_owner = get_object_or_404(User, pk=owner_id, company=company)

        if is_sup:
            new_owner_regions = new_owner.get_consulting_region_codes()
            if inbound_lead.region_code and inbound_lead.region_code not in new_owner_regions:
                raise PermissionDenied("Назначить можно только сотрудника этого региона.")

        inbound_lead.owner = new_owner
        inbound_lead.status = InboundLeadConsalting.Status.ASSIGNED
        inbound_lead.save(update_fields=["owner", "status", "updated_at"])

        realtime.notify_user(
            new_owner.id,
            "consulting.lead.assigned",
            {
                "id": str(inbound_lead.id),
                "full_name": inbound_lead.full_name,
                "phone": inbound_lead.phone,
                "source": inbound_lead.source,
                "message": inbound_lead.message,
                "status": inbound_lead.status,
                "created_at": inbound_lead.created_at.isoformat(),
            }
        )
        return Response(InboundLeadConsaltingSerializer(inbound_lead, context=self.get_serializer_context()).data)


class InboundLeadDeferView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/inbound-leads/<id>/defer/ — отложить лид.
    """
    serializer_class = InboundLeadConsaltingSerializer

    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        qs = InboundLeadConsalting.objects.filter(company=company)
        if not is_owner_like(request.user):
            qs = qs.filter(owner=request.user)

        lead = get_object_or_404(qs, pk=pk)

        if lead.status in (InboundLeadConsalting.Status.CONVERTED, InboundLeadConsalting.Status.REJECTED):
            return Response({"detail": "Лид уже закрыт."}, status=status.HTTP_400_BAD_REQUEST)

        remind_at_raw = request.data.get("remind_at")
        reason = request.data.get("reason")
        comment = request.data.get("comment", "")

        if not remind_at_raw:
            return Response({"remind_at": "Поле remind_at обязательно."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if isinstance(remind_at_raw, datetime):
                remind_at = remind_at_raw
            else:
                remind_at = parse_datetime(str(remind_at_raw))
                if not remind_at:
                    remind_at = datetime.fromisoformat(str(remind_at_raw))
            if timezone.is_naive(remind_at):
                remind_at = timezone.make_aware(remind_at)
        except Exception:
            return Response({"remind_at": "Неверный формат даты/времени."}, status=status.HTTP_400_BAD_REQUEST)

        if remind_at < timezone.now() - timedelta(minutes=1):
            return Response({"remind_at": "Дата напоминания должна быть в будущем."}, status=status.HTTP_400_BAD_REQUEST)

        if not reason or reason not in InboundLeadConsalting.DeferReason.values:
            return Response({"reason": "Укажите корректную причину откладывания."}, status=status.HTTP_400_BAD_REQUEST)

        if reason == InboundLeadConsalting.DeferReason.OTHER and not str(comment).strip():
            return Response({"comment": "При причине 'Другое' комментарий обязателен."}, status=status.HTTP_400_BAD_REQUEST)

        lead.status = InboundLeadConsalting.Status.DEFERRED
        lead.remind_at = remind_at
        lead.defer_reason = reason
        lead.defer_comment = str(comment).strip()
        lead.defer_count += 1
        lead.deferred_at = timezone.now()
        lead.reminded_at = None
        lead.save()

        if lead.owner_id:
            realtime.notify_user(
                lead.owner_id,
                "consulting.lead.deferred",
                {
                    "id": str(lead.id),
                    "full_name": lead.full_name,
                    "phone": lead.phone,
                    "status": lead.status,
                    "remind_at": lead.remind_at.isoformat() if lead.remind_at else None,
                    "defer_reason": lead.defer_reason,
                    "defer_comment": lead.defer_comment,
                }
            )

        return Response(InboundLeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class InboundLeadResumeView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/inbound-leads/<id>/resume/ — вернуть лид в работу.
    """
    serializer_class = InboundLeadConsaltingSerializer

    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        qs = InboundLeadConsalting.objects.filter(company=company)
        if not is_owner_like(request.user):
            qs = qs.filter(owner=request.user)

        lead = get_object_or_404(qs, pk=pk)
        lead.status = InboundLeadConsalting.Status.IN_WORK
        lead.remind_at = None
        lead.save(update_fields=["status", "remind_at", "updated_at"])

        return Response(InboundLeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class InboundLeadWonView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/inbound-leads/<id>/won/ — пометка «Купил».
    """
    serializer_class = InboundLeadConsaltingSerializer

    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        qs = InboundLeadConsalting.objects.filter(company=company)
        if not is_owner_like(request.user):
            qs = qs.filter(owner=request.user)

        lead = get_object_or_404(qs, pk=pk)
        sale_id = request.data.get("sale")
        if sale_id:
            sale = get_object_or_404(SaleConsalting, pk=sale_id, company=company)
            lead.sale = sale

        now = timezone.now()
        lead.status = InboundLeadConsalting.Status.CONVERTED
        lead.converted_at = now
        lead.closed_at = now
        lead.save()

        if lead.owner_id:
            realtime.notify_user(
                lead.owner_id,
                "consulting.lead.closed",
                {
                    "id": str(lead.id),
                    "full_name": lead.full_name,
                    "phone": lead.phone,
                    "status": lead.status,
                    "converted_at": lead.converted_at.isoformat(),
                }
            )

        return Response(InboundLeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class InboundLeadLostView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    POST /api/consalting/inbound-leads/<id>/lost/ — пометка «Отказ».
    """
    serializer_class = InboundLeadConsaltingSerializer

    def post(self, request, pk, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        qs = InboundLeadConsalting.objects.filter(company=company)
        if not is_owner_like(request.user):
            qs = qs.filter(owner=request.user)

        lead = get_object_or_404(qs, pk=pk)
        reason = request.data.get("reason")
        comment = request.data.get("comment", "")

        if not reason or reason not in InboundLeadConsalting.RejectReason.values:
            return Response({"reason": "Укажите корректную причину отказа."}, status=status.HTTP_400_BAD_REQUEST)

        if reason == InboundLeadConsalting.RejectReason.OTHER and not str(comment).strip():
            return Response({"comment": "При причине 'Другое' комментарий обязателен."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        lead.status = InboundLeadConsalting.Status.REJECTED
        lead.reject_reason = reason
        lead.reject_comment = str(comment).strip()
        lead.closed_at = now
        lead.save()

        if lead.owner_id:
            realtime.notify_user(
                lead.owner_id,
                "consulting.lead.closed",
                {
                    "id": str(lead.id),
                    "full_name": lead.full_name,
                    "phone": lead.phone,
                    "status": lead.status,
                    "reject_reason": lead.reject_reason,
                }
            )

        return Response(InboundLeadConsaltingSerializer(lead, context=self.get_serializer_context()).data)


class InboundLeadCountersView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    GET /api/consalting/inbound-leads/counters/ — счётчики табов лидов.
    """
    def get(self, request, *args, **kwargs):
        import uuid
        from datetime import datetime
        company = self._user_company()
        if not company:
            return Response({"all": 0, "new": 0, "in_work": 0, "deferred": 0, "converted": 0, "rejected": 0, "overdue": 0})

        qs = InboundLeadConsalting.objects.filter(company=company)

        user = request.user
        if not is_owner_like(user):
            qs = qs.filter(owner=user)
        else:
            owner_param = request.query_params.get("owner")
            if owner_param:
                op_lower = owner_param.lower().strip()
                if op_lower in ("none", "null", "unassigned"):
                    qs = qs.filter(owner__isnull=True)
                elif op_lower in ("mine", "my"):
                    qs = qs.filter(owner=user)
                elif op_lower not in ("all", ""):
                    try:
                        uuid_val = uuid.UUID(owner_param)
                        qs = qs.filter(owner_id=uuid_val)
                    except ValueError:
                        pass

        source_param = request.query_params.get("source")
        if source_param:
            qs = qs.filter(source=source_param)

        search_param = request.query_params.get("search")
        if search_param:
            qs = qs.filter(
                Q(full_name__icontains=search_param) |
                Q(phone__icontains=search_param) |
                Q(message__icontains=search_param)
            )

        date_from = request.query_params.get("date_from")
        if date_from:
            try:
                df = datetime.strptime(date_from.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=df)
            except ValueError:
                pass

        date_to = request.query_params.get("date_to")
        if date_to:
            try:
                dt = datetime.strptime(date_to.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=dt)
            except ValueError:
                pass

        now = timezone.now()
        counts = qs.aggregate(
            all=Count("id"),
            new=Count("id", filter=Q(status__in=[InboundLeadConsalting.Status.NEW, InboundLeadConsalting.Status.ASSIGNED])),
            in_work=Count("id", filter=Q(status=InboundLeadConsalting.Status.IN_WORK)),
            deferred=Count("id", filter=Q(status=InboundLeadConsalting.Status.DEFERRED)),
            converted=Count("id", filter=Q(status=InboundLeadConsalting.Status.CONVERTED)),
            rejected=Count("id", filter=Q(status=InboundLeadConsalting.Status.REJECTED)),
            overdue=Count("id", filter=Q(status=InboundLeadConsalting.Status.DEFERRED, remind_at__lte=now)),
        )

        return Response(counts)


class InboundLeadAnalyticsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    GET /api/consalting/inbound-leads/analytics/ — аналитика по лидам.
    Когортный принцип по created_at.
    """
    def get(self, request, *args, **kwargs):
        import uuid
        from datetime import datetime
        company = self._user_company()
        if not company:
            return Response({})

        qs = InboundLeadConsalting.objects.filter(company=company)

        user = request.user
        is_owner = is_owner_like(user)

        if not is_owner:
            qs = qs.filter(owner=user)
        else:
            owner_param = request.query_params.get("owner")
            if owner_param:
                op_lower = owner_param.lower().strip()
                if op_lower in ("none", "null", "unassigned"):
                    qs = qs.filter(owner__isnull=True)
                elif op_lower in ("mine", "my"):
                    qs = qs.filter(owner=user)
                elif op_lower not in ("all", ""):
                    try:
                        uuid_val = uuid.UUID(owner_param)
                        qs = qs.filter(owner_id=uuid_val)
                    except ValueError:
                        pass

        source_param = request.query_params.get("source")
        if source_param:
            qs = qs.filter(source=source_param)

        date_from_str = request.query_params.get("date_from")
        date_to_str = request.query_params.get("date_to")

        if date_from_str:
            try:
                df = datetime.strptime(date_from_str.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=df)
            except ValueError:
                pass

        if date_to_str:
            try:
                dt = datetime.strptime(date_to_str.strip(), "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=dt)
            except ValueError:
                pass

        now = timezone.now()

        totals_agg = qs.aggregate(
            leads=Count("id"),
            new=Count("id", filter=Q(status__in=[InboundLeadConsalting.Status.NEW, InboundLeadConsalting.Status.ASSIGNED])),
            in_work=Count("id", filter=Q(status=InboundLeadConsalting.Status.IN_WORK)),
            deferred=Count("id", filter=Q(status=InboundLeadConsalting.Status.DEFERRED)),
            overdue=Count("id", filter=Q(status=InboundLeadConsalting.Status.DEFERRED, remind_at__lte=now)),
            converted=Count("id", filter=Q(status=InboundLeadConsalting.Status.CONVERTED)),
            rejected=Count("id", filter=Q(status=InboundLeadConsalting.Status.REJECTED)),
            revenue=Sum("sale__total", filter=Q(status=InboundLeadConsalting.Status.CONVERTED, sale__isnull=False)),
        )

        leads_count = totals_agg["leads"] or 0
        converted_count = totals_agg["converted"] or 0
        revenue_val = float(totals_agg["revenue"] or 0)
        conversion_pct = round((converted_count / leads_count * 100), 2) if leads_count > 0 else 0.0
        avg_check = round(revenue_val / converted_count, 2) if converted_count > 0 else 0.0

        first_reply_qs = qs.filter(first_reply_at__isnull=False)
        first_reply_minutes = None
        if first_reply_qs.exists():
            diffs = [(l.first_reply_at - l.created_at).total_seconds() / 60.0 for l in first_reply_qs]
            if diffs:
                first_reply_minutes = round(sum(diffs) / len(diffs), 1)

        time_to_sale_qs = qs.filter(status=InboundLeadConsalting.Status.CONVERTED, converted_at__isnull=False)
        time_to_sale_minutes = None
        if time_to_sale_qs.exists():
            diffs = [(l.converted_at - l.created_at).total_seconds() / 60.0 for l in time_to_sale_qs]
            if diffs:
                time_to_sale_minutes = round(sum(diffs) / len(diffs), 1)

        totals = {
            "leads": leads_count,
            "new": totals_agg["new"] or 0,
            "in_work": totals_agg["in_work"] or 0,
            "deferred": totals_agg["deferred"] or 0,
            "overdue": totals_agg["overdue"] or 0,
            "converted": converted_count,
            "rejected": totals_agg["rejected"] or 0,
            "conversion": conversion_pct,
            "revenue": revenue_val,
            "avg_check": avg_check,
            "first_reply_avg_minutes": first_reply_minutes,
            "time_to_sale_avg_minutes": time_to_sale_minutes,
        }

        by_source_list = []
        source_groups = (
            qs.values("source")
            .annotate(
                leads=Count("id"),
                converted=Count("id", filter=Q(status=InboundLeadConsalting.Status.CONVERTED)),
                revenue=Sum("sale__total", filter=Q(status=InboundLeadConsalting.Status.CONVERTED, sale__isnull=False))
            )
            .order_by("-leads")
        )
        for s in source_groups:
            s_leads = s["leads"]
            s_conv = s["converted"]
            s_rev = float(s["revenue"] or 0)
            by_source_list.append({
                "source": s["source"] or "unknown",
                "leads": s_leads,
                "converted": s_conv,
                "conversion": round(s_conv / s_leads * 100, 2) if s_leads > 0 else 0.0,
                "revenue": s_rev
            })

        by_user_list = []
        if not is_owner:
            emp_leads = totals["leads"]
            emp_conv = totals["converted"]
            emp_rev = totals["revenue"]
            user_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.email
            by_user_list.append({
                "user": str(user.id),
                "name": user_name,
                "leads": emp_leads,
                "in_work": totals["in_work"],
                "deferred": totals["deferred"],
                "overdue": totals["overdue"],
                "converted": emp_conv,
                "conversion": round(emp_conv / emp_leads * 100, 2) if emp_leads > 0 else 0.0,
                "revenue": emp_rev
            })
        else:
            user_groups = (
                qs.values("owner_id", "owner__first_name", "owner__last_name", "owner__email")
                .annotate(
                    leads=Count("id"),
                    in_work=Count("id", filter=Q(status=InboundLeadConsalting.Status.IN_WORK)),
                    deferred=Count("id", filter=Q(status=InboundLeadConsalting.Status.DEFERRED)),
                    overdue=Count("id", filter=Q(status=InboundLeadConsalting.Status.DEFERRED, remind_at__lte=now)),
                    converted=Count("id", filter=Q(status=InboundLeadConsalting.Status.CONVERTED)),
                    revenue=Sum("sale__total", filter=Q(status=InboundLeadConsalting.Status.CONVERTED, sale__isnull=False))
                )
                .order_by("-leads")
            )
            for u in user_groups:
                u_leads = u["leads"]
                u_conv = u["converted"]
                u_rev = float(u["revenue"] or 0)
                name = f"{u['owner__first_name'] or ''} {u['owner__last_name'] or ''}".strip()
                if not name:
                    name = u["owner__email"] or "Не назначен"
                by_user_list.append({
                    "user": str(u["owner_id"]) if u["owner_id"] else None,
                    "name": name,
                    "leads": u_leads,
                    "in_work": u["in_work"],
                    "deferred": u["deferred"],
                    "overdue": u["overdue"],
                    "converted": u_conv,
                    "conversion": round(u_conv / u_leads * 100, 2) if u_leads > 0 else 0.0,
                    "revenue": u_rev
                })

        by_day_dict = {}
        day_groups = (
            qs.extra(select={'created_day': "DATE(created_at)"})
            .values('created_day')
            .annotate(
                leads=Count('id'),
                converted=Count('id', filter=Q(status=InboundLeadConsalting.Status.CONVERTED))
            )
        )
        for d in day_groups:
            day_str = str(d['created_day'])
            by_day_dict[day_str] = {
                "leads": d['leads'],
                "converted": d['converted']
            }

        by_day_list = []
        if date_from_str and date_to_str:
            try:
                start_date = parse_date(date_from_str)
                end_date = parse_date(date_to_str)
                curr = start_date
                while curr and curr <= end_date:
                    d_str = curr.isoformat()
                    d_data = by_day_dict.get(d_str, {"leads": 0, "converted": 0})
                    by_day_list.append({
                        "date": d_str,
                        "leads": d_data["leads"],
                        "converted": d_data["converted"]
                    })
                    curr += timedelta(days=1)
            except Exception:
                for d_str, d_data in sorted(by_day_dict.items()):
                    by_day_list.append({
                        "date": d_str,
                        "leads": d_data["leads"],
                        "converted": d_data["converted"]
                    })
        else:
            for d_str, d_data in sorted(by_day_dict.items()):
                by_day_list.append({
                    "date": d_str,
                    "leads": d_data["leads"],
                    "converted": d_data["converted"]
                })

        defer_reasons_qs = (
            qs.filter(status=InboundLeadConsalting.Status.DEFERRED)
            .exclude(defer_reason="")
            .values("defer_reason")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        defer_reasons = [{"reason": item["defer_reason"], "count": item["count"]} for item in defer_reasons_qs]

        reject_reasons_qs = (
            qs.filter(status=InboundLeadConsalting.Status.REJECTED)
            .exclude(reject_reason="")
            .values("reject_reason")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        reject_reasons = [{"reason": item["reject_reason"], "count": item["count"]} for item in reject_reasons_qs]

        # §8.7 Рекламные затраты (LeadAdSpend)
        ad_qs = LeadAdSpend.objects.filter(company=company)
        if date_from_str:
            try:
                ad_df = datetime.strptime(date_from_str.strip(), "%Y-%m-%d").date()
                ad_qs = ad_qs.filter(date__gte=ad_df)
            except ValueError:
                pass
        if date_to_str:
            try:
                ad_dt = datetime.strptime(date_to_str.strip(), "%Y-%m-%d").date()
                ad_qs = ad_qs.filter(date__lte=ad_dt)
            except ValueError:
                pass
        ad_agg = ad_qs.aggregate(
            total_spend=Sum("spend"),
            total_impressions=Sum("impressions"),
            reported_leads=Sum("leads"),
        )
        total_spend = Decimal(str(ad_agg["total_spend"] or "0.00"))
        total_impressions = int(ad_agg["total_impressions"] or 0)
        reported_leads = int(ad_agg["reported_leads"] or 0)
        actual_leads = leads_count
        cost_per_lead = (total_spend / Decimal(str(reported_leads))).quantize(Decimal("0.01")) if reported_leads > 0 else Decimal("0.00")
        cost_per_actual_lead = (total_spend / Decimal(str(actual_leads))).quantize(Decimal("0.01")) if actual_leads > 0 else Decimal("0.00")

        ad_spend_data = {
            "total_spend": f"{total_spend:.2f}",
            "total_impressions": total_impressions,
            "reported_leads": reported_leads,
            "actual_leads": actual_leads,
            "cost_per_lead": f"{cost_per_lead:.2f}",
            "cost_per_actual_lead": f"{cost_per_actual_lead:.2f}",
        }

        return Response({
            "totals": totals,
            "by_source": by_source_list,
            "by_user": by_user_list,
            "by_day": by_day_list,
            "defer_reasons": defer_reasons,
            "reject_reasons": reject_reasons,
            "ad_spend": ad_spend_data,
        })


class LeadDistributionSettingsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    GET/PUT /api/consalting/lead-distribution/ — настройки авто-распределения лидов.
    """
    serializer_class = LeadDistributionSettingsConsaltingSerializer

    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        settings, _ = LeadDistributionSettingsConsalting.objects.get_or_create(company=company)
        return Response(LeadDistributionSettingsConsaltingSerializer(settings).data)

    def put(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Настройки распределения может менять только руководитель.")

        settings, _ = LeadDistributionSettingsConsalting.objects.get_or_create(company=company)
        ser = LeadDistributionSettingsConsaltingSerializer(settings, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class WhatsAppInboundWebhookView(APIView):
    """
    POST /api/consalting/integrations/whatsapp/webhook/
    Webhook от WhatsApp провайдера: регистрация сообщения, защита от дублей, авто-распределение.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        data = request.data or {}
        external_id = str(data.get("external_id") or data.get("id") or data.get("message_id") or "").strip()
        phone = str(data.get("phone") or data.get("from") or "").strip()
        full_name = str(data.get("full_name") or data.get("name") or data.get("contact_name") or "").strip()
        message = str(data.get("message") or data.get("text") or data.get("body") or "").strip()
        company_id = data.get("company_id")

        if not phone and not message:
            return Response({"detail": "Пустые данные webhook."}, status=status.HTTP_400_BAD_REQUEST)

        company = Company.objects.filter(id=company_id).first() if company_id else Company.objects.first()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_400_BAD_REQUEST)

        if external_id:
            existing = InboundLeadConsalting.objects.filter(
                company=company, source="whatsapp", external_id=external_id
            ).first()
            if existing:
                return Response({"status": "duplicate_ignored", "id": str(existing.id)}, status=status.HTTP_200_OK)

        inbound_lead = InboundLeadConsalting.objects.create(
            company=company,
            full_name=full_name or phone or "WhatsApp Лид",
            phone=phone,
            source="whatsapp",
            external_id=external_id,
            message=message,
            status=InboundLeadConsalting.Status.NEW
        )

        from .funnel.regional_routing import resolve_funnel_and_assignee
        reg_funnel, reg_stage, reg_rule, reg_user = resolve_funnel_and_assignee(
            company=company,
            phone=phone,
            source="whatsapp"
        )
        if reg_user:
            inbound_lead.owner = reg_user
            inbound_lead.status = InboundLeadConsalting.Status.ASSIGNED
            inbound_lead.save(update_fields=["owner", "status", "updated_at"])
        else:
            distribute_inbound_lead(inbound_lead)
        return Response({"status": "success", "id": str(inbound_lead.id)}, status=status.HTTP_200_OK)


class SubscriptionPaymentPayView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Приём оплаты по графику абонентской подписки (§5.4).
    POST /api/consalting/subscription-payments/<uuid:pk>/pay/
    """
    queryset = SubscriptionPaymentConsalting.objects.select_related("subscription", "subscription__company").all()
    serializer_class = SubscriptionPaymentConsaltingSerializer

    def post(self, request, *args, **kwargs):
        payment = self.get_object()
        company = self._user_company()
        if company and payment.subscription.company_id != company.id:
            raise PermissionDenied("Нет доступа к платежу данной компании.")

        if payment.status == SubscriptionPaymentConsalting.Status.PAID:
            return Response({"detail": "Период уже оплачен."}, status=status.HTTP_400_BAD_REQUEST)

        payment_method = request.data.get("payment_method") or "cash"
        cashbox = request.data.get("cashbox")
        pay_amount = request.data.get("amount")
        if pay_amount is not None:
            try:
                pay_amount = Decimal(str(pay_amount))
                if pay_amount < payment.amount:
                    return Response({"detail": "Частичная оплата не поддерживается."}, status=status.HTTP_400_BAD_REQUEST)
            except Exception:
                pass

        from .funnel.cash_confirmation import needs_confirmation
        from .models import CashRequestConsalting, CashOperationConsalting

        if needs_confirmation(company, payment_method, request.user):
            CashRequestConsalting.objects.create(
                company=company,
                user=request.user,
                client=payment.subscription.client if hasattr(payment, "subscription") and payment.subscription else None,
                subscription_payment=payment,
                kind=CashRequestConsalting.Kind.SUBSCRIPTION,
                direction="income",
                amount=payment.amount,
                payment_method=payment_method,
                status=CashRequestConsalting.Status.PENDING,
            )
            return Response(SubscriptionPaymentConsaltingSerializer(payment).data)

        payment.status = SubscriptionPaymentConsalting.Status.PAID
        payment.paid_at = timezone.now()
        if cashbox:
            import uuid
            try:
                payment.cashbox_id = uuid.UUID(str(cashbox))
            except (ValueError, TypeError):
                pass
        payment.payment_method = payment_method
        payment.save()

        CashOperationConsalting.objects.create(
            company=company,
            user=request.user,
            kind=CashOperationConsalting.Kind.SUBSCRIPTION,
            direction=CashOperationConsalting.Direction.INCOME,
            amount=payment.amount,
            payment_method=payment_method,
            comment=f"Оплата абонентской платы ({payment.period_month})",
        )

        if hasattr(payment, "subscription") and payment.subscription and payment.subscription.client:
            from .funnel.tenant_lifecycle import extend_tenant_subscription
            extend_tenant_subscription(
                client=payment.subscription.client,
                subscription_payment=payment,
                actor=request.user,
            )

        return Response(SubscriptionPaymentConsaltingSerializer(payment).data)


class SubscriptionPayPeriodsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Оплата нескольких периодов абонентской платы сразу (§5.4a).
    POST /api/consalting/subscriptions/<uuid:pk>/pay-periods/
    { "count": 3, "cashbox": "uuid|null", "payment_method": "cash|transfer", "note": "" }
    """
    queryset = SubscriptionConsalting.objects.select_related("company", "client", "service").all()
    serializer_class = SubscriptionConsaltingSerializer

    def post(self, request, *args, **kwargs):
        sub = self.get_object()
        company = self._user_company()
        if company and sub.company_id != company.id:
            raise PermissionDenied("Нет доступа к подписке данной компании.")

        try:
            count = int(request.data.get("count") or 1)
        except (ValueError, TypeError):
            count = 1
        if count < 1:
            count = 1

        payment_method = request.data.get("payment_method") or "cash"
        cashbox = request.data.get("cashbox")
        note = request.data.get("note") or ""

        # Find unpaid periods
        unpaid_qs = sub.payments.filter(
            status__in=[SubscriptionPaymentConsalting.Status.PLANNED, SubscriptionPaymentConsalting.Status.OVERDUE]
        ).order_by("due_date")

        # If needed, extend schedule
        if unpaid_qs.count() < count:
            needed_months = (count - unpaid_qs.count()) + 12
            generate_schedule(sub, horizon_months=needed_months)
            unpaid_qs = sub.payments.filter(
                status__in=[SubscriptionPaymentConsalting.Status.PLANNED, SubscriptionPaymentConsalting.Status.OVERDUE]
            ).order_by("due_date")

        selected_payments = list(unpaid_qs[:count])
        if not selected_payments:
            return Response({"detail": "Нет доступных периодов для оплаты."}, status=status.HTTP_400_BAD_REQUEST)

        first_period = selected_payments[0].period_month
        last_period = selected_payments[-1].period_month
        period_range = first_period if len(selected_payments) == 1 else f"{first_period}..{last_period}"
        actual_count = len(selected_payments)
        total_amount = sum(p.amount for p in selected_payments)

        # Idempotency: within 24h
        from datetime import timedelta
        from .funnel.cash_confirmation import needs_confirmation
        cutoff = timezone.now() - timedelta(hours=24)
        existing_req = CashRequestConsalting.objects.filter(
            company=sub.company,
            subscription=sub,
            period_month=period_range,
            prepaid_count=actual_count,
            status=CashRequestConsalting.Status.PENDING,
            created_at__gte=cutoff,
        ).first()
        if existing_req:
            return Response({
                "detail": "Заявка на оплату уже создана.",
                "cash_request_id": str(existing_req.id),
                "periods_count": actual_count,
                "amount": float(existing_req.amount),
                "period_range": period_range,
            }, status=status.HTTP_200_OK)

        cashbox_uuid = None
        if cashbox:
            import uuid
            try:
                cashbox_uuid = uuid.UUID(str(cashbox))
            except (ValueError, TypeError):
                pass

        if needs_confirmation(sub.company, payment_method, request.user):
            req = CashRequestConsalting.objects.create(
                company=sub.company,
                user=request.user,
                client=sub.client,
                subscription=sub,
                subscription_payment=selected_payments[0],
                kind=CashRequestConsalting.Kind.SUBSCRIPTION,
                direction="income",
                amount=total_amount,
                payment_method=payment_method,
                comment=note or f"Оплата {actual_count} периодов абонентской платы ({period_range})",
                cashbox_id=cashbox_uuid,
                period_month=period_range,
                prepaid_count=actual_count,
                status=CashRequestConsalting.Status.PENDING,
            )
            return Response({
                "detail": "Заявка на оплату создана.",
                "cash_request_id": str(req.id),
                "periods_count": actual_count,
                "amount": float(total_amount),
                "period_range": period_range,
            }, status=status.HTTP_201_CREATED)

        now = timezone.now()
        for p in selected_payments:
            p.status = SubscriptionPaymentConsalting.Status.PAID
            p.paid_at = now
            p.paid_via = "batch_pay"
            p.payment_method = payment_method
            p.cashbox_id = cashbox_uuid
            p.save(update_fields=["status", "paid_at", "paid_via", "payment_method", "cashbox_id"])

        sub.paid_through = selected_payments[-1].due_date
        sub.save(update_fields=["paid_through"])

        CashOperationConsalting.objects.create(
            company=sub.company,
            user=request.user,
            subscription=sub,
            kind=CashOperationConsalting.Kind.SUBSCRIPTION,
            direction=CashOperationConsalting.Direction.INCOME,
            amount=total_amount,
            payment_method=payment_method,
            cashbox_id=cashbox_uuid,
            comment=note or f"Оплата {actual_count} периодов абонентской платы ({period_range})",
        )

        if sub.client:
            from .funnel.tenant_lifecycle import extend_tenant_subscription
            extend_tenant_subscription(client=sub.client, subscription_payment=selected_payments[-1], actor=request.user)

        return Response({
            "detail": f"Оплачено {actual_count} периодов.",
            "periods_count": actual_count,
            "amount": float(total_amount),
            "period_range": period_range,
            "paid_through": str(sub.paid_through),
        }, status=status.HTTP_200_OK)


class SubscriptionExtendView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """Manually append periods to a subscription schedule (§5.8.2)."""
    queryset = SubscriptionConsalting.objects.select_related("company", "client", "service", "tariff").all()
    serializer_class = SubscriptionConsaltingSerializer

    def post(self, request, *args, **kwargs):
        if not can_manage_lead_ad_spend(request.user):
            raise PermissionDenied("Нет прав управлять графиком абонентской платы.")
        sub = self.get_object()
        company = self._user_company()
        if company and sub.company_id != company.id:
            raise PermissionDenied("Нет доступа к подписке данной компании.")
        if sub.status in (SubscriptionConsalting.Status.CANCELED, SubscriptionConsalting.Status.FINISHED):
            raise PermissionDenied("Нельзя продлить отменённую или завершённую подписку.")

        try:
            periods = int(request.data.get("periods"))
        except (TypeError, ValueError):
            periods = 0
        if periods < 1:
            return Response({"periods": "Укажите целое число не меньше 1."}, status=status.HTTP_400_BAD_REQUEST)

        new_amount = request.data.get("amount")
        if new_amount not in (None, ""):
            try:
                new_amount = Decimal(str(new_amount))
            except Exception:
                return Response({"amount": "Укажите корректную сумму."}, status=status.HTTP_400_BAD_REQUEST)
            if new_amount <= 0:
                return Response({"amount": "Сумма должна быть больше нуля."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if new_amount is not None and new_amount != "":
                sub.amount = new_amount
                sub.save(update_fields=["amount"])
            # generate_schedule starts immediately after the actual tail of the
            # schedule, so it cannot create gaps or duplicate period months.
            generate_schedule(sub, horizon_months=periods)

        return Response(SubscriptionConsaltingSerializer(sub).data)


class SubscriptionAmountUpdateView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """Change the price of unpaid subscription periods without rewriting history (§5.8.3)."""
    queryset = SubscriptionConsalting.objects.select_related("company", "client", "service", "tariff").all()
    serializer_class = SubscriptionConsaltingSerializer

    def patch(self, request, *args, **kwargs):
        if not can_manage_lead_ad_spend(request.user):
            raise PermissionDenied("Нет прав управлять графиком абонентской платы.")
        sub = self.get_object()
        company = self._user_company()
        if company and sub.company_id != company.id:
            raise PermissionDenied("Нет доступа к подписке данной компании.")

        try:
            amount = Decimal(str(request.data.get("amount")))
        except Exception:
            return Response({"amount": "Укажите корректную сумму."}, status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return Response({"amount": "Сумма должна быть больше нуля."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            sub.amount = amount
            sub.save(update_fields=["amount"])
            sub.payments.filter(
                status__in=[
                    SubscriptionPaymentConsalting.Status.PLANNED,
                    SubscriptionPaymentConsalting.Status.OVERDUE,
                ]
            ).update(amount=amount)

        return Response(SubscriptionConsaltingSerializer(sub).data)


class ClientSubscriptionsListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    Список абонентских подписок клиента (§5.5).
    GET /api/consalting/clients/<uuid:client_id>/subscriptions/
    """
    serializer_class = SubscriptionConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return SubscriptionConsalting.objects.none()

        client_id = self.kwargs.get("client_id") or self.kwargs.get("pk")
        return SubscriptionConsalting.objects.filter(
            company=company, client_id=client_id
        ).select_related("service", "tariff").prefetch_related("payments").order_by("-created_at")


class SubscriptionMatrixView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    GET /api/consalting/subscription-matrix/
    Абонентская матрица клиентов «клиент × услуга × месяцы» (§5.5).
    """
    def get(self, request, *args, **kwargs):
        from datetime import date
        from django.db.models import Q

        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        month_from = request.query_params.get("month_from")
        month_to = request.query_params.get("month_to")
        search = request.query_params.get("search")

        today = timezone.localdate()
        if not month_from or not month_to:
            d_end = today.replace(day=1)
            d_start = _add_months(d_end, -5)
            month_from = f"{d_start.year:04d}-{d_start.month:02d}"
            month_to = f"{d_end.year:04d}-{d_end.month:02d}"

        try:
            y1, m1 = map(int, month_from.split("-"))
            y2, m2 = map(int, month_to.split("-"))
            d_from = date(y1, m1, 1)
            d_to = date(y2, m2, 1)
        except Exception:
            return Response({"detail": "Некорректный формат month_from/month_to (YYYY-MM)."}, status=status.HTTP_400_BAD_REQUEST)

        months_list = []
        d_curr = d_from
        while d_curr <= d_to:
            months_list.append(f"{d_curr.year:04d}-{d_curr.month:02d}")
            d_curr = _add_months(d_curr, 1)

        subs_qs = SubscriptionConsalting.objects.filter(
            company=company, status=SubscriptionConsalting.Status.ACTIVE
        ).select_related("client", "service", "tariff").prefetch_related("payments")

        if search:
            subs_qs = subs_qs.filter(
                Q(client__full_name__icontains=search) | Q(service__name__icontains=search)
            )

        page_str = request.query_params.get("page")
        page_size_str = request.query_params.get("page_size", 20)
        total_count = subs_qs.count()

        if page_str:
            try:
                p = int(page_str)
                ps = int(page_size_str)
                start_idx = max(0, (p - 1) * ps)
                end_idx = start_idx + ps
                subs_qs = subs_qs[start_idx:end_idx]
            except ValueError:
                pass

        rows = []
        for sub in subs_qs:
            cells = {}
            for pm in sub.payments.all():
                m_str = pm.period_month
                if m_str not in months_list:
                    continue

                cells[m_str] = {
                    "payment_id": str(pm.id),
                    "amount": float(pm.amount),
                    "status": pm.status,
                }

            rows.append({
                "subscription_id": str(sub.id),
                "client_id": str(sub.client_id),
                "client_name": sub.client.full_name or "Без имени",
                "service_id": str(sub.service_id),
                "service_name": sub.service.name,
                "subscription_amount": float(sub.amount),
                "subscription_period": sub.period,
                "cells": cells,
            })

        return Response({
            "months": months_list,
            "count": total_count,
            "rows": rows,
        })


# ==========================
# Карточка сотрудника и показатели (§6.2, §6.5, §6.6)
# ==========================
class EmployeeStatsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Показатели сотрудника: лиды, продажи, скорость, КПД, зарплата (§6.2).
    GET /api/consalting/employees/<uuid:pk>/stats/?date_from=&date_to=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        emp_id = str(self.kwargs.get("pk"))
        target_user = get_object_or_404(User, pk=emp_id, company=company)

        # Права: рядовой сотрудник видит только свою карточку, руководитель — все (§6.7)
        if not is_owner_like(request.user) and str(request.user.id) != emp_id:
            raise PermissionDenied("Нет доступа к показателям других сотрудников.")

        from .funnel.employee_stats import parse_date_range, calculate_employee_stats
        d_from, d_to, dt_start, dt_end = parse_date_range(request)

        stats = calculate_employee_stats(company, target_user, dt_start, dt_end, d_from, d_to)
        return Response(stats)


class EmployeesRatingView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Рейтинг сотрудников компании (§6.5).
    GET /api/consalting/employees/rating/?date_from=&date_to=&ordering=-kpi&search=&page=&page_size=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        # Права: только руководитель (§6.7)
        if not is_owner_like(request.user):
            raise PermissionDenied("Доступ к рейтингу сотрудников разрешён только руководителям.")

        from .funnel.employee_stats import parse_date_range, calculate_employee_stats
        d_from, d_to, dt_start, dt_end = parse_date_range(request)

        users_qs = User.objects.filter(company=company, is_active=True)
        search = request.query_params.get("search")
        if search:
            users_qs = users_qs.filter(
                Q(first_name__icontains=search) | Q(last_name__icontains=search) | Q(email__icontains=search)
            )

        ordering = request.query_params.get("ordering") or "-kpi"
        reverse = ordering.startswith("-")
        sort_key = ordering.lstrip("-")

        results = []
        for emp in users_qs:
            st = calculate_employee_stats(company, emp, dt_start, dt_end, d_from, d_to)
            name = f"{emp.first_name or ''} {emp.last_name or ''}".strip() or emp.email
            results.append({
                "user": str(emp.id),
                "name": name,
                "leads": st["leads"]["received"],
                "deferred": st["leads"]["deferred"],
                "overdue": st["leads"]["overdue"],
                "deals": st["sales"]["deals"],
                "conversion": st["sales"]["conversion"],
                "revenue": st["sales"]["revenue"],
                "kpi": st["kpi"]["score"],
            })

        if sort_key in ("kpi", "revenue", "deals", "conversion", "leads", "deferred", "overdue"):
            results.sort(key=lambda x: x.get(sort_key, 0) or 0, reverse=reverse)

        page_str = request.query_params.get("page")
        page_size_str = request.query_params.get("page_size", 20)
        total_cnt = len(results)

        if page_str:
            try:
                p = int(page_str)
                ps = int(page_size_str)
                start_idx = max(0, (p - 1) * ps)
                end_idx = start_idx + ps
                results = results[start_idx:end_idx]
            except ValueError:
                pass

        return Response({
            "count": total_cnt,
            "results": results
        })


class EmployeeActivityView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Лента последних событий сотрудника (§6.6).
    GET /api/consalting/employees/<uuid:pk>/activity/?page=&page_size=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        emp_id = str(self.kwargs.get("pk"))
        target_user = get_object_or_404(User, pk=emp_id, company=company)

        if not is_owner_like(request.user) and str(request.user.id) != emp_id:
            raise PermissionDenied("Нет доступа к активности других сотрудников.")

        activities = (
            LeadActivityConsalting.objects.filter(lead__company=company, actor=target_user)
            .select_related("lead")
            .order_by("-created_at")[:50]
        )

        results = [
            {
                "id": str(act.id),
                "type": act.activity_type,
                "title": act.title or "Событие по лиду",
                "subtitle": act.lead.title if act.lead else "",
                "at": act.created_at.isoformat(),
                "url": f"/consalting/leads/{act.lead_id}" if act.lead_id else None,
            }
            for act in activities
        ]

        return Response({"count": len(results), "results": results})


class SalesPlanListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    Личные планы продаж сотрудников (§6.4).
    GET/POST /api/consalting/sales-plans/
    """
    serializer_class = SalesPlanConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return SalesPlanConsalting.objects.none()
        qs = SalesPlanConsalting.objects.filter(company=company).select_related("user")
        if not is_owner_like(self.request.user):
            qs = qs.filter(user=self.request.user)
        user_id = self.request.query_params.get("user")
        if user_id:
            qs = qs.filter(user_id=user_id)
        month = self.request.query_params.get("period_month")
        if month:
            qs = qs.filter(period_month=month)
        return qs.order_by("-period_month")

    def perform_create(self, serializer):
        company = self._user_company()
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Назначать личные планы продаж может только руководитель.")
        serializer.save(company=company)


# ==========================
# Финансы сотрудника и касса (§7.3, §7.4, §7.5)
# ==========================
class EmployeeFinanceView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Финансовая сводка сотрудника (§7.3).
    GET /api/consalting/employees/<uuid:pk>/finance/?date_from=&date_to=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        emp_id = str(self.kwargs.get("pk"))
        target_user = get_object_or_404(User, pk=emp_id, company=company)

        # Права: сотрудник видит только свои финансы (§7.6)
        if not is_owner_like(request.user) and str(request.user.id) != emp_id:
            raise PermissionDenied("Нет доступа к финансам других сотрудников.")

        from .funnel.employee_stats import parse_date_range
        from .funnel.employee_finance import calculate_employee_finance

        d_from, d_to, dt_start, dt_end = parse_date_range(request)
        data = calculate_employee_finance(target_user, dt_start, dt_end)
        return Response(data)


class EmployeeSalesView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    История продаж сотрудника (§7.4).
    GET /api/consalting/employees/<uuid:pk>/sales/?date_from=&date_to=&status=&search=&page=&page_size=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        emp_id = str(self.kwargs.get("pk"))
        target_user = get_object_or_404(User, pk=emp_id, company=company)

        if not is_owner_like(request.user) and str(request.user.id) != emp_id:
            raise PermissionDenied("Нет доступа к продажам других сотрудников.")

        from .funnel.employee_stats import parse_date_range
        d_from, d_to, dt_start, dt_end = parse_date_range(request)

        sales_qs = (
            SaleConsalting.objects.filter(company=company, user=target_user, created_at__range=(dt_start, dt_end))
            .select_related("client", "services", "tariff")
            .order_by("-created_at")
        )

        search = request.query_params.get("search")
        if search:
            sales_qs = sales_qs.filter(
                Q(client__full_name__icontains=search) | Q(services__name__icontains=search)
            )

        page_str = request.query_params.get("page")
        page_size_str = request.query_params.get("page_size", 20)
        total_cnt = sales_qs.count()

        if page_str:
            try:
                p = int(page_str)
                ps = int(page_size_str)
                start_idx = max(0, (p - 1) * ps)
                end_idx = start_idx + ps
                sales_qs = sales_qs[start_idx:end_idx]
            except ValueError:
                pass

        results = []
        for sale in sales_qs:
            is_canceled = "отмена" in (sale.description or "").lower() or "отменён" in (sale.description or "").lower()
            accrual_agg = SalaryAccrualConsalting.objects.filter(
                company=company, user=target_user, sale=sale
            ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

            pm = sale.lead.payment_mode if (sale.lead and sale.lead.payment_mode) else "cash"
            results.append({
                "id": str(sale.id),
                "created_at": sale.created_at.isoformat(),
                "client_display": sale.client.full_name if sale.client else "—",
                "service_display": sale.services.name if sale.services else "—",
                "tariff_display": sale.tariff.name if sale.tariff else "",
                "total": float(sale.total),
                "payment_mode": pm,
                "payment_display": "Наличные" if (pm == "cash") else "Перевод",
                "status": "canceled" if is_canceled else "completed",
                "status_display": "Отменена" if is_canceled else "Проведена",
                "accrual_amount": float(accrual_agg),
            })

        return Response({"count": total_cnt, "results": results})


class EmployeeHandoversView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    История сдачи наличных сотрудника (§7.4).
    GET /api/consalting/employees/<uuid:pk>/handovers/?status=&date_from=&date_to=&page=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        emp_id = str(self.kwargs.get("pk"))
        target_user = get_object_or_404(User, pk=emp_id, company=company)

        if not is_owner_like(request.user) and str(request.user.id) != emp_id:
            raise PermissionDenied("Нет доступа к кассовым операциям другого сотрудника.")

        qs = CashRequestConsalting.objects.filter(company=company, user=target_user, kind="handover").select_related("confirmed_by")

        st_filter = request.query_params.get("status")
        if st_filter:
            qs = qs.filter(status=st_filter)

        page_str = request.query_params.get("page")
        page_size_str = request.query_params.get("page_size", 20)
        total_cnt = qs.count()

        if page_str:
            try:
                p = int(page_str)
                ps = int(page_size_str)
                start_idx = max(0, (p - 1) * ps)
                end_idx = start_idx + ps
                qs = qs[start_idx:end_idx]
            except ValueError:
                pass

        results = [
            {
                "id": str(req.id),
                "created_at": req.created_at.isoformat(),
                "amount": float(req.amount),
                "status": req.status,
                "status_display": req.get_status_display(),
                "confirmed_by_display": req.confirmed_by.email if req.confirmed_by else None,
                "comment": req.comment,
            }
            for req in qs
        ]

        return Response({"count": total_cnt, "results": results})


class EmployeeDebtsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Долги клиентов, оформленные сотрудником (§7.4).
    GET /api/consalting/employees/<uuid:pk>/debts/?overdue=true&search=&page=
    """
    def get(self, request, *args, **kwargs):
        from apps.main.models import DealInstallment

        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        emp_id = str(self.kwargs.get("pk"))
        target_user = get_object_or_404(User, pk=emp_id, company=company)

        if not is_owner_like(request.user) and str(request.user.id) != emp_id:
            raise PermissionDenied("Нет доступа к долгам клиентов других сотрудников.")

        installments = DealInstallment.objects.filter(
            deal__client__company=company
        ).select_related("deal", "deal__client")

        overdue_only = request.query_params.get("overdue") == "true"
        today = timezone.localdate()

        results = []
        for inst in installments:
            rem = float(inst.amount - (inst.paid_amount or Decimal("0")))
            if rem <= 0:
                continue
            is_overdue = inst.due_date < today
            if overdue_only and not is_overdue:
                continue

            results.append({
                "id": str(inst.id),
                "client_display": inst.deal.client.full_name if (inst.deal and inst.deal.client) else "Клиент",
                "service_display": inst.deal.title if inst.deal else "Долг",
                "total": float(inst.amount),
                "remaining": round(rem, 2),
                "next_payment_date": inst.due_date.isoformat() if inst.due_date else None,
                "is_overdue": is_overdue,
            })

        return Response({"count": len(results), "results": results})


class CashboxHandoverCreateView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Сдача наличных сотрудником (§7.4).
    POST /api/consalting/cashbox/handovers/  { "amount": 12000, "comment": "" }
    """
    def post(self, request, *args, **kwargs):
        from decimal import Decimal, InvalidOperation
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        try:
            amount = Decimal(str(request.data.get("amount", "0")))
        except (InvalidOperation, TypeError):
            return Response({"amount": "Некорректная сумма."}, status=status.HTTP_400_BAD_REQUEST)

        if amount <= Decimal("0"):
            return Response({"amount": "Сумма должна быть больше нуля."}, status=status.HTTP_400_BAD_REQUEST)

        from .funnel.employee_finance import calculate_employee_finance
        today = timezone.localdate()
        from datetime import datetime, time
        tz = timezone.get_current_timezone()
        dt_start = timezone.make_aware(datetime.combine(today.replace(day=1), time.min), tz)
        dt_end = timezone.make_aware(datetime.combine(today, time.max), tz)

        fin = calculate_employee_finance(request.user, dt_start, dt_end)
        on_hands = fin["on_hands"]

        if float(amount) > on_hands:
            return Response(
                {"detail": "Сумма больше, чем числится на руках."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        comment = request.data.get("comment") or ""
        req_obj = CashRequestConsalting.objects.create(
            company=company,
            user=request.user,
            kind="handover",
            direction="income",
            amount=amount,
            status=CashRequestConsalting.Status.PENDING,
            comment=comment,
        )

        return Response(CashRequestConsaltingSerializer(req_obj).data, status=status.HTTP_201_CREATED)


class CashboxReconciliationView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Сверка по сотрудникам (§7.4).
    GET /api/consalting/cashbox/reconciliation/?date_from=&date_to=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        if not is_owner_like(request.user):
            raise PermissionDenied("Доступ к сверке разрешён только руководителям.")

        from .funnel.employee_stats import parse_date_range
        from .funnel.employee_finance import calculate_employee_finance

        d_from, d_to, dt_start, dt_end = parse_date_range(request)

        users = User.objects.filter(company=company, is_active=True)
        results = []
        for emp in users:
            fin = calculate_employee_finance(emp, dt_start, dt_end)
            name = f"{emp.first_name or ''} {emp.last_name or ''}".strip() or emp.email
            discrepancy = round(fin["cash_received"] - fin["handed_over"] - fin["on_hands"], 2)
            results.append({
                "user": str(emp.id),
                "user_display": name,
                "sold": fin["sold"],
                "cash_received": fin["cash_received"],
                "handed_over": fin["handed_over"],
                "on_hands": fin["on_hands"],
                "discrepancy": discrepancy,
            })

        return Response({"count": len(results), "results": results})


class EmployeeShortageDeductView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Удержание недостачи из зарплаты (§7.5).
    POST /api/consalting/employees/<uuid:pk>/deduct-shortage/
    """
    def post(self, request, *args, **kwargs):
        from decimal import Decimal
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        if not is_owner_like(request.user):
            raise PermissionDenied("Удерживать недостачу может только руководитель.")

        emp_id = str(self.kwargs.get("pk"))
        target_user = get_object_or_404(User, pk=emp_id, company=company)

        from .funnel.employee_stats import parse_date_range
        from .funnel.employee_finance import calculate_employee_finance
        d_from, d_to, dt_start, dt_end = parse_date_range(request)

        fin = calculate_employee_finance(target_user, dt_start, dt_end)
        on_hands = fin["on_hands"]

        if on_hands <= 0:
            return Response({"detail": "У сотрудника нет суммы на руках для удержания."}, status=status.HTTP_400_BAD_REQUEST)

        adj = SalaryAdjustmentConsalting.objects.create(
            company=company,
            user=target_user,
            kind=SalaryAdjustmentConsalting.Kind.DEDUCTION,
            amount=Decimal(str(on_hands)),
            reason=SalaryAdjustmentConsalting.Reason.SHORTAGE,
            comment="Удержание недостачи (подотчёт)",
            date=timezone.localdate(),
            status="active",
        )

        return Response({
            "status": "success",
            "adjustment_id": str(adj.id),
            "deducted_amount": on_hands
        }, status=status.HTTP_201_CREATED)


# ==========================
# Отмена и возврат продажи (§8.3, §8.4, §8.6)
# ==========================
class SaleCancelView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Полная отмена продажи (§8.3).
    POST /api/consalting/sales/<uuid:pk>/cancel/
    """
    queryset = SaleConsalting.objects.all()
    serializer_class = SaleConsaltingSerializer

    def post(self, request, *args, **kwargs):
        sale = self.get_object()
        company = self._user_company()
        if company and sale.company_id != company.id:
            raise PermissionDenied("Нет доступа к продаже данной компании.")

        # Права: owner/admin или автор в течение 30 минут (§8.6)
        is_mgr = is_owner_like(request.user)
        is_creator = (sale.user_id == request.user.id)
        is_fresh = (timezone.now() - sale.created_at) <= timedelta(minutes=30)

        if not (is_mgr or (is_creator and is_fresh)):
            raise PermissionDenied("Отмену подтверждает руководитель.")

        reason = request.data.get("reason")
        if not reason:
            return Response({"reason": "Причина отмены обязательна."}, status=status.HTTP_400_BAD_REQUEST)

        comment = request.data.get("comment") or ""
        if reason == "other" and not comment.strip():
            return Response({"comment": "При выборе 'Другое' комментарий обязателен."}, status=status.HTTP_400_BAD_REQUEST)

        refund_mode = request.data.get("refund_mode") or "none"
        lead_action = request.data.get("lead_action")

        from django.core.exceptions import ValidationError as DjangoValidationError
        from .funnel.sale_cancel import cancel_sale

        try:
            sale = cancel_sale(
                sale,
                user=request.user,
                reason=reason,
                comment=comment,
                refund_mode=refund_mode,
                lead_action=lead_action,
            )
        except DjangoValidationError as e:
            return Response(
                getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(SaleConsaltingSerializer(sale, context=self.get_serializer_context()).data)


class SaleRefundView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Частичный возврат продажи (§8.3).
    POST /api/consalting/sales/<uuid:pk>/refund/
    """
    queryset = SaleConsalting.objects.all()
    serializer_class = SaleConsaltingSerializer

    def post(self, request, *args, **kwargs):
        from decimal import Decimal, InvalidOperation
        sale = self.get_object()
        company = self._user_company()
        if company and sale.company_id != company.id:
            raise PermissionDenied("Нет доступа к продаже данной компании.")

        if not is_owner_like(request.user):
            raise PermissionDenied("Оформлять возврат может только руководитель.")

        try:
            amount = Decimal(str(request.data.get("amount", "0")))
        except (InvalidOperation, TypeError):
            return Response({"amount": "Некорректная сумма."}, status=status.HTTP_400_BAD_REQUEST)

        rem = sale.total - (sale.refunded_amount or Decimal("0"))
        if amount <= Decimal("0") or amount > rem:
            return Response({"detail": "Сумма возврата больше остатка по продаже."}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get("reason") or "warranty"
        comment = request.data.get("comment") or ""
        refund_mode = request.data.get("refund_mode") or "cash"

        from django.core.exceptions import ValidationError as DjangoValidationError
        from .funnel.sale_cancel import cancel_sale

        try:
            sale = cancel_sale(
                sale,
                user=request.user,
                reason=reason,
                comment=comment,
                refund_mode=refund_mode,
                partial_amount=amount,
            )
        except DjangoValidationError as e:
            return Response(
                getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(SaleConsaltingSerializer(sale, context=self.get_serializer_context()).data)


class SaleCancellationsReportView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Отчёт «Отмены и возвраты» (§8.3).
    GET /api/consalting/sales/cancellations/?date_from=&date_to=&user=&reason=&page=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        from .funnel.employee_stats import parse_date_range
        d_from, d_to, dt_start, dt_end = parse_date_range(request)

        qs = SaleConsalting.objects.filter(
            company=company,
            status__in=[SaleConsalting.Status.CANCELED, SaleConsalting.Status.REFUNDED],
            canceled_at__range=(dt_start, dt_end),
        ).select_related("client", "user", "canceled_by")

        user_filter = request.query_params.get("user")
        if user_filter:
            qs = qs.filter(user_id=user_filter)

        reason_filter = request.query_params.get("reason")
        if reason_filter:
            qs = qs.filter(cancel_reason=reason_filter)

        page_str = request.query_params.get("page")
        page_size_str = request.query_params.get("page_size", 20)
        total_cnt = qs.count()

        if page_str:
            try:
                p = int(page_str)
                ps = int(page_size_str)
                start_idx = max(0, (p - 1) * ps)
                end_idx = start_idx + ps
                qs = qs[start_idx:end_idx]
            except ValueError:
                pass

        results = [
            {
                "id": str(s.id),
                "canceled_at": s.canceled_at.isoformat() if s.canceled_at else None,
                "client_display": s.client.full_name if s.client else "—",
                "total": float(s.total),
                "refunded_amount": float(s.refunded_amount),
                "status": s.status,
                "reason": s.cancel_reason,
                "comment": s.cancel_comment,
                "user_display": s.user.email if s.user else "—",
                "canceled_by_display": s.canceled_by.email if s.canceled_by else "—",
            }
            for s in qs
        ]

        return Response({"count": total_cnt, "results": results})


# ==========================
# Подтверждение поступлений в кассе (§9.4, §9.5, §9.6)
# ==========================
def _can_manage_cash(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if is_owner_like(user):
        return True
    role = str(getattr(user, "role", "")).lower()
    return role in ("cashier", "кассир") or getattr(user, "is_cashier", False)


class CashRequestsListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    Список заявок на кассовые операции (§9.4).
    GET /api/consalting/cashbox/requests/?status=&kind=&user=&cashbox=&date_from=&date_to=&search=&page=
    """
    serializer_class = CashRequestConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CashRequestConsalting.objects.none()

        qs = CashRequestConsalting.objects.filter(company=company).select_related(
            "user", "client", "sale", "subscription_payment", "confirmed_by"
        )

        # Обычный сотрудник видит только свои заявки (§9.6)
        if not _can_manage_cash(self.request.user):
            qs = qs.filter(user=self.request.user)

        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        kind_param = self.request.query_params.get("kind")
        if kind_param:
            qs = qs.filter(kind=kind_param)

        user_param = self.request.query_params.get("user")
        if user_param and _can_manage_cash(self.request.user):
            qs = qs.filter(user_id=user_param)

        cashbox_param = self.request.query_params.get("cashbox")
        if cashbox_param:
            qs = qs.filter(cashbox_id=cashbox_param)

        search_param = self.request.query_params.get("search")
        if search_param:
            qs = qs.filter(
                Q(client__full_name__icontains=search_param) |
                Q(user__email__icontains=search_param) |
                Q(comment__icontains=search_param)
            )

        from .funnel.employee_stats import parse_date_range
        d_from, d_to, dt_start, dt_end = parse_date_range(self.request)
        if self.request.query_params.get("date_from") or self.request.query_params.get("date_to"):
            qs = qs.filter(created_at__range=(dt_start, dt_end))

        return qs


class CashRequestsCountersView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Счётчики заявок кассы (§9.4).
    GET /api/consalting/cashbox/requests/counters/
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        qs = CashRequestConsalting.objects.filter(company=company)
        if not _can_manage_cash(request.user):
            qs = qs.filter(user=request.user)

        pending_qs = qs.filter(status=CashRequestConsalting.Status.PENDING)
        pending_cnt = pending_qs.count()
        confirmed_cnt = qs.filter(status=CashRequestConsalting.Status.CONFIRMED).count()
        rejected_cnt = qs.filter(status=CashRequestConsalting.Status.REJECTED).count()
        canceled_cnt = qs.filter(status=CashRequestConsalting.Status.CANCELED).count()
        all_cnt = qs.count()

        pending_amount = pending_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")

        return Response({
            "pending": pending_cnt,
            "confirmed": confirmed_cnt,
            "rejected": rejected_cnt,
            "canceled": canceled_cnt,
            "all": all_cnt,
            "pending_amount": float(pending_amount),
        })


class CashRequestConfirmView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Подтверждение заявки кассиром/руководителем (§9.5, §9.6).
    POST /api/consalting/cashbox/requests/<uuid:pk>/confirm/
    """
    queryset = CashRequestConsalting.objects.all()

    def post(self, request, *args, **kwargs):
        req_obj = self.get_object()
        company = self._user_company()
        if company and req_obj.company_id != company.id:
            raise PermissionDenied("Нет доступа к заявке данной компании.")

        if not _can_manage_cash(request.user):
            raise PermissionDenied("Подтверждать заявки может только руководитель или кассир.")

        # Проверка skip_for_cashier: если false, сам автор не может подтвердить себя (§9.6)
        settings = getattr(company, "consalting_cash_confirmation", None)
        if settings and not settings.skip_for_cashier and req_obj.user_id == request.user.id:
            raise PermissionDenied("Автор заявки не может подтвердить её самостоятельно.")

        cashbox_id = request.data.get("cashbox")
        comment = request.data.get("comment", "")

        from django.core.exceptions import ValidationError as DjangoValidationError
        from .funnel.cash_confirmation import confirm_request

        try:
            req_obj, op = confirm_request(req_obj, user=request.user, cashbox_id=cashbox_id, comment=comment)
        except DjangoValidationError as e:
            return Response(
                getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(CashRequestConsaltingSerializer(req_obj).data)


class CashRequestRejectView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Отклонение заявки кассовой операции (§9.5).
    POST /api/consalting/cashbox/requests/<uuid:pk>/reject/
    """
    queryset = CashRequestConsalting.objects.all()

    def post(self, request, *args, **kwargs):
        req_obj = self.get_object()
        company = self._user_company()
        if company and req_obj.company_id != company.id:
            raise PermissionDenied("Нет доступа к заявке данной компании.")

        if not _can_manage_cash(request.user):
            raise PermissionDenied("Отклонять заявки может только руководитель или кассир.")

        reason = request.data.get("reason")
        comment = request.data.get("comment", "")

        from django.core.exceptions import ValidationError as DjangoValidationError
        from .funnel.cash_confirmation import reject_request

        try:
            req_obj = reject_request(req_obj, user=request.user, reason=reason, comment=comment)
        except DjangoValidationError as e:
            return Response(
                getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(CashRequestConsaltingSerializer(req_obj).data)


class CashOperationsListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    Список подтверждённых кассовых операций (§9.4).
    GET /api/consalting/cashbox/operations/?user=&date_from=&date_to=&page=
    """
    serializer_class = CashOperationConsaltingSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CashOperationConsalting.objects.none()

        qs = CashOperationConsalting.objects.filter(company=company).select_related("user", "confirmed_by")

        if not _can_manage_cash(self.request.user):
            qs = qs.filter(user=self.request.user)

        user_param = self.request.query_params.get("user")
        if user_param and _can_manage_cash(self.request.user):
            qs = qs.filter(user_id=user_param)

        from .funnel.employee_stats import parse_date_range
        d_from, d_to, dt_start, dt_end = parse_date_range(self.request)
        if self.request.query_params.get("date_from") or self.request.query_params.get("date_to"):
            qs = qs.filter(created_at__range=(dt_start, dt_end))

        return qs


class CashConfirmationSettingsView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Настройки подтверждения кассы (§9.4, §9.6).
    GET /PUT /api/consalting/cashbox/confirmation-settings/
    """
    serializer_class = CashConfirmationSettingsConsaltingSerializer

    def get_object(self):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        settings, _ = CashConfirmationSettingsConsalting.objects.get_or_create(company=company)
        return settings

    def get(self, request, *args, **kwargs):
        settings = self.get_object()
        return Response(CashConfirmationSettingsConsaltingSerializer(settings).data)

    def put(self, request, *args, **kwargs):
        if not is_owner_like(request.user):
            raise PermissionDenied("Изменять настройки кассы может только руководитель.")
        settings = self.get_object()
        serializer = CashConfirmationSettingsConsaltingSerializer(settings, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def post(self, request, *args, **kwargs):
        """Фронт шлет POST для сохранения настроек кассы (§9.0 п.2)."""
        return self.put(request, *args, **kwargs)


class RegionalFunnelRoutingView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Настройки региональной маршрутизации входящих лидов (§4.3).
    GET /PUT /api/consalting/regional-funnel-routing/
    """
    serializer_class = RegionalFunnelRoutingConsaltingSerializer

    def get_object(self):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        routing, _ = RegionalFunnelRoutingConsalting.objects.get_or_create(company=company)
        return routing

    def get(self, request, *args, **kwargs):
        from .access import is_owner_like, is_consulting_supervisor, is_consulting_salesperson
        if is_consulting_salesperson(request.user):
            raise PermissionDenied("У вас нет доступа к настройкам маршрутизации.")
        routing = self.get_object()
        return Response(RegionalFunnelRoutingConsaltingSerializer(routing).data)

    def put(self, request, *args, **kwargs):
        from .access import is_owner_like, is_consulting_supervisor, is_consulting_salesperson
        if not is_owner_like(request.user):
            raise PermissionDenied("Изменять маршрутизацию воронок может только руководитель.")

        routing = self.get_object()
        data = request.data or {}

        enabled = data.get("enabled")
        if enabled is not None:
            routing.enabled = bool(enabled)

        fallback_strategy = data.get("fallback_strategy")
        if fallback_strategy in ("round_robin", "default_funnel"):
            routing.fallback_strategy = fallback_strategy

        balance_strategy = data.get("balance_strategy")
        if balance_strategy in ("least_loaded", "round_robin"):
            routing.balance_strategy = balance_strategy

        if "default_funnel_id" in data:
            df_id = data.get("default_funnel_id")
            if df_id:
                df = FunnelConsalting.objects.filter(company=routing.company, id=df_id).first()
                if not df:
                    return Response({"default_funnel_id": "Указанная воронка не найдена в компании."}, status=status.HTTP_400_BAD_REQUEST)
                routing.default_funnel = df
            else:
                routing.default_funnel = None

        routing.save()

        # Правила
        rules_data = data.get("rules")
        if rules_data is not None and isinstance(rules_data, list):
            with transaction.atomic():
                existing_rules = {str(r.id): r for r in routing.rules.all()}
                kept_rule_ids = set()

                for order, r_item in enumerate(rules_data):
                    funnel_id = r_item.get("funnel_id")
                    if not funnel_id:
                        continue
                    funnel = FunnelConsalting.objects.filter(company=routing.company, id=funnel_id).first()
                    if not funnel:
                        continue

                    rule_id = str(r_item.get("id") or "")
                    rule_obj = existing_rules.get(rule_id)
                    if not rule_obj:
                        rule_obj = RegionalFunnelRuleConsalting(routing=routing, funnel=funnel)

                    rule_obj.funnel = funnel
                    rule_obj.region_code = r_item.get("region_code") or "other"
                    rule_obj.label = r_item.get("label") or ""
                    rule_obj.is_active = bool(r_item.get("is_active", True))
                    rule_obj.phone_prefixes = r_item.get("phone_prefixes") or []
                    rule_obj.wazzup_account_ids = r_item.get("wazzup_account_ids") or []
                    rule_obj.source_channels = r_item.get("source_channels") or []
                    rule_obj.assign_role_ids = r_item.get("assign_role_ids") or []
                    rule_obj.assign_strategy = r_item.get("assign_strategy") or "round_robin"
                    rule_obj.order = r_item.get("order", order)
                    rule_obj.save()
                    kept_rule_ids.add(str(rule_obj.id))

                # Удаляем правила, которых нет в новом списке
                for r_id, r_obj in existing_rules.items():
                    if r_id not in kept_rule_ids:
                        r_obj.delete()

        routing.refresh_from_db()
        return Response(RegionalFunnelRoutingConsaltingSerializer(routing).data)


class RegionalFunnelRegionsListView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Список активных регионов с числом открытых лидов и сотрудников (§7.2).
    GET /api/consalting/regions/
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        user = request.user
        routing = getattr(company, "consalting_regional_routing", None)
        if not routing:
            routing = RegionalFunnelRoutingConsalting.objects.filter(company=company).first()

        if not routing:
            return Response([])

        rules_qs = routing.rules.filter(is_active=True).select_related("funnel").order_by("order", "created_at")

        if is_consulting_supervisor(user) or is_consulting_salesperson(user):
            my_regions = user.get_consulting_region_codes()
            rules_qs = rules_qs.filter(region_code__in=my_regions)

        from .funnel.regional_routing import REGION_LABELS
        from apps.users.models import User
        active_employees = list(User.objects.filter(company=company, is_active=True, deleted_at__isnull=True))

        result = []
        for r in rules_qs:
            open_leads = LeadConsalting.objects.filter(
                company=company
            ).filter(
                Q(region_code=r.region_code) | Q(funnel=r.funnel)
            ).exclude(
                status__in=[LeadConsalting.Status.WON, LeadConsalting.Status.LOST]
            ).distinct().count()

            emp_cnt = sum(1 for u in active_employees if r.region_code in u.get_consulting_region_codes())

            result.append({
                "code": r.region_code,
                "label": r.label or REGION_LABELS.get(r.region_code, r.region_code),
                "funnel_id": str(r.funnel_id) if r.funnel_id else None,
                "is_active": r.is_active,
                "open_leads": open_leads,
                "employees_count": emp_cnt,
            })

        return Response(result)


class RegionalFunnelRedistributeView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Разовое выравнивание базы лидов по регионам (§4.2).
    POST /api/consalting/regional-funnel-routing/redistribute/
    """
    def post(self, request, *args, **kwargs):
        if not is_owner_like(request.user):
            raise PermissionDenied("Разделять лиды по регионам может только руководитель.")

        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        data = request.data or {}
        scope = data.get("scope", "main_unassigned")
        regions = data.get("regions", None)
        dry_run = bool(data.get("dry_run", False))

        from .funnel.regional_routing import redistribute_leads
        try:
            res = redistribute_leads(company, request.user, scope=scope, regions=regions, dry_run=dry_run)
            return Response(res, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ConsultingCashboxListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    GET /api/consalting/cashbox/cashboxes/ — список касс компании с аналитикой
    POST /api/consalting/cashbox/cashboxes/ — создание кассы
    """
    def get_queryset(self):
        from apps.construction.models import Cashbox
        company = self._user_company()
        if not company:
            return Cashbox.objects.none()
        return Cashbox.objects.filter(company=company).order_by("created_at")

    def list(self, request, *args, **kwargs):
        from apps.construction.models import Cashbox
        from apps.consalting.models import CashOperationConsalting, CashRequestConsalting
        qs = self.get_queryset()
        data = []
        for cb in qs:
            ops = CashOperationConsalting.objects.filter(company=cb.company, cashbox_id=cb.id)
            income_total = ops.filter(direction=CashOperationConsalting.Direction.INCOME).aggregate(s=Sum("amount"))["s"] or 0
            expense_total = ops.filter(direction=CashOperationConsalting.Direction.OUTCOME).aggregate(s=Sum("amount"))["s"] or 0
            pending_amt = CashRequestConsalting.objects.filter(
                company=cb.company, cashbox_id=cb.id, status=CashRequestConsalting.Status.PENDING
            ).aggregate(s=Sum("amount"))["s"] or 0

            data.append({
                "id": str(cb.id),
                "name": cb.name or "Касса",
                "role": getattr(cb, "role", None),
                "is_consumption": getattr(cb, "is_consumption", False),
                "is_active": getattr(cb, "is_active", True),
                "income_total": float(income_total),
                "expense_total": float(expense_total),
                "balance": float(income_total - expense_total),
                "pending_amount": float(pending_amt),
                "created_at": cb.created_at.isoformat() if hasattr(cb, "created_at") and cb.created_at else None,
            })
        return Response(data)

    def create(self, request, *args, **kwargs):
        from apps.construction.models import Cashbox
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        name = str(request.data.get("name") or "").strip()
        if not name:
            return Response({"name": "Название кассы обязательно."}, status=status.HTTP_400_BAD_REQUEST)
        cb = Cashbox.objects.create(company=company, name=name, is_active=True)
        return Response({
            "id": str(cb.id),
            "name": cb.name,
            "role": getattr(cb, "role", None),
            "is_consumption": getattr(cb, "is_consumption", False),
            "is_active": cb.is_active,
            "income_total": 0.0,
            "expense_total": 0.0,
            "balance": 0.0,
            "pending_amount": 0.0,
        }, status=status.HTTP_201_CREATED)


class ConsultingCashboxDetailView(CompanyBranchQuerysetMixin, generics.RetrieveAPIView):
    """
    GET /api/consalting/cashbox/cashboxes/<uuid:pk>/ — касса компании с аналитикой
    """
    def get_queryset(self):
        from apps.construction.models import Cashbox
        company = self._user_company()
        if not company:
            return Cashbox.objects.none()
        return Cashbox.objects.filter(company=company)

    def retrieve(self, request, *args, **kwargs):
        from apps.consalting.models import CashOperationConsalting, CashRequestConsalting
        cb = self.get_object()
        ops = CashOperationConsalting.objects.filter(company=cb.company, cashbox_id=cb.id)
        income_total = ops.filter(direction=CashOperationConsalting.Direction.INCOME).aggregate(s=Sum("amount"))["s"] or 0
        expense_total = ops.filter(direction=CashOperationConsalting.Direction.OUTCOME).aggregate(s=Sum("amount"))["s"] or 0
        pending_amt = CashRequestConsalting.objects.filter(
            company=cb.company, cashbox_id=cb.id, status=CashRequestConsalting.Status.PENDING
        ).aggregate(s=Sum("amount"))["s"] or 0

        return Response({
            "id": str(cb.id),
            "name": cb.name or "Касса",
            "role": getattr(cb, "role", None),
            "is_consumption": getattr(cb, "is_consumption", False),
            "income_total": float(income_total),
            "expense_total": float(expense_total),
            "balance": float(income_total - expense_total),
            "pending_amount": float(pending_amt),
        })


class ClientLookupView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Поиск дублей клиентов по телефону и/или email (§8.5).
    GET /api/consalting/clients/lookup/?phone=&email=
    """
    def get(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        phone = request.query_params.get("phone")
        email = request.query_params.get("email")

        from .funnel.lead_conversion import find_client_duplicates
        matches = find_client_duplicates(company, phone=phone, email=email)
        return Response({"matches": matches})


# =====================================================================
# Финансы лидов: рекламный отчёт (§8)
# =====================================================================

class LeadAdSpendPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 1000


class LeadAdSpendListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/consalting/lead-ad-spend/ — список строк рекламного отчёта компании
    POST /api/consalting/lead-ad-spend/ — создать одну строку
    """
    permission_classes = [permissions.IsAuthenticated, CanManageLeadAdSpend]
    serializer_class = LeadAdSpendSerializer
    pagination_class = LeadAdSpendPagination

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return LeadAdSpend.objects.none()

        qs = LeadAdSpend.objects.filter(company=company)

        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_from:
            try:
                df = datetime.strptime(date_from.strip(), "%Y-%m-%d").date()
                qs = qs.filter(date__gte=df)
            except ValueError:
                pass
        if date_to:
            try:
                dt = datetime.strptime(date_to.strip(), "%Y-%m-%d").date()
                qs = qs.filter(date__lte=dt)
            except ValueError:
                pass

        ordering = self.request.query_params.get("ordering")
        allowed_orderings = {
            "date", "-date",
            "impressions", "-impressions",
            "leads", "-leads",
            "spend", "-spend",
            "created_at", "-created_at",
            "updated_at", "-updated_at",
        }
        if ordering and ordering in allowed_orderings:
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by("-date")

        return qs

    def create(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        raw_date = request.data.get("date")
        if not raw_date:
            return Response({"detail": "Укажите дату строки.", "date": ["Укажите дату строки."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            if isinstance(raw_date, str):
                d = datetime.strptime(raw_date.strip(), "%Y-%m-%d").date()
            else:
                d = raw_date
        except ValueError:
            return Response({"detail": "Укажите дату строки.", "date": ["Укажите дату строки."]}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.localdate()
        if d > today:
            return Response({"detail": "Дата не может быть в будущем.", "date": ["Дата не может быть в будущем."]}, status=status.HTTP_400_BAD_REQUEST)

        if LeadAdSpend.objects.filter(company=company, date=d).exists():
            d_fmt = d.strftime("%d.%m.%Y")
            return Response({"detail": f"За {d_fmt} отчёт уже заведён — измените существующую строку."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            raw_imp = request.data.get("impressions", 0)
            raw_leads = request.data.get("leads", 0)
            impressions = int(raw_imp if raw_imp is not None else 0)
            leads = int(raw_leads if raw_leads is not None else 0)
        except (ValueError, TypeError):
            return Response({"detail": "Показы и лиды не могут быть отрицательными."}, status=status.HTTP_400_BAD_REQUEST)

        if impressions < 0 or leads < 0:
            return Response({"detail": "Показы и лиды не могут быть отрицательными."}, status=status.HTTP_400_BAD_REQUEST)

        if impressions > 0 and leads > 0 and leads > impressions:
            return Response({"detail": "Лидов больше, чем показов — проверьте цифры."}, status=status.HTTP_400_BAD_REQUEST)

        raw_spend = request.data.get("spend", 0)
        try:
            spend = Decimal(str(raw_spend if raw_spend is not None else 0))
        except (InvalidOperation, TypeError):
            return Response({"detail": "Сумма затрат указана неверно."}, status=status.HTTP_400_BAD_REQUEST)

        if spend < 0 or spend > Decimal("999999999.99"):
            return Response({"detail": "Сумма затрат указана неверно."}, status=status.HTTP_400_BAD_REQUEST)

        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        serializer.save(company=company, created_by=self.request.user)


class LeadAdSpendDetailView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/consalting/lead-ad-spend/<uuid:pk>/ — одна строка
    PUT    /api/consalting/lead-ad-spend/<uuid:pk>/ — заменить строку
    PATCH  /api/consalting/lead-ad-spend/<uuid:pk>/ — частично изменить
    DELETE /api/consalting/lead-ad-spend/<uuid:pk>/ — удалить строку
    """
    permission_classes = [permissions.IsAuthenticated, CanManageLeadAdSpend]
    serializer_class = LeadAdSpendSerializer
    queryset = LeadAdSpend.objects.all()

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return LeadAdSpend.objects.none()
        return LeadAdSpend.objects.filter(company=company)


class LeadAdSpendBulkView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    PUT /api/consalting/lead-ad-spend/bulk/
    Полная синхронизация набора строк компании: upsert по (company, date) + удаление отсутствующих (§8.4).
    """
    permission_classes = [permissions.IsAuthenticated, CanManageLeadAdSpend]
    serializer_class = LeadAdSpendSerializer

    def put(self, request, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        data = request.data
        if not isinstance(data, dict) or "items" not in data:
            return Response(
                {"detail": "Ожидается объект с полем 'items'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            return Response(
                {"detail": "Поле 'items' должно быть списком."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_df = data.get("date_from")
        raw_dt = data.get("date_to")
        date_from = None
        date_to = None
        if raw_df:
            try:
                date_from = datetime.strptime(str(raw_df).strip(), "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"detail": "Укажите верный формат date_from (YYYY-MM-DD)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if raw_dt:
            try:
                date_to = datetime.strptime(str(raw_dt).strip(), "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"detail": "Укажите верный формат date_to (YYYY-MM-DD)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        import uuid
        parsed_items = []
        seen_dates = set()
        today = timezone.localdate()

        for item in raw_items:
            if not isinstance(item, dict):
                return Response(
                    {"detail": "Элемент списка должен быть объектом."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 1. Валидация даты
            raw_date = item.get("date")
            if not raw_date:
                return Response(
                    {"detail": "Укажите дату строки.", "date": ["Укажите дату строки."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if isinstance(raw_date, str):
                try:
                    d = datetime.strptime(raw_date.strip(), "%Y-%m-%d").date()
                except ValueError:
                    return Response(
                        {"detail": "Укажите дату строки.", "date": ["Укажите дату строки."]},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            elif isinstance(raw_date, date):
                d = raw_date
            else:
                return Response(
                    {"detail": "Укажите дату строки.", "date": ["Укажите дату строки."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if d > today:
                return Response(
                    {"detail": "Дата не может быть в будущем.", "date": ["Дата не может быть в будущем."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if date_from and date_to:
                if d < date_from or d > date_to:
                    return Response(
                        {"detail": f"Дата {d.isoformat()} вне диапазона {date_from.isoformat()} — {date_to.isoformat()}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            d_str = d.isoformat()
            if d_str in seen_dates:
                return Response(
                    {"detail": f"Дата {d_str} встречается дважды."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            seen_dates.add(d_str)

            # 2. Валидация показов и лидов
            try:
                raw_imp = item.get("impressions", 0)
                raw_leads = item.get("leads", 0)
                impressions = int(raw_imp if raw_imp is not None else 0)
                leads = int(raw_leads if raw_leads is not None else 0)
            except (ValueError, TypeError):
                return Response(
                    {"detail": "Показы и лиды не могут быть отрицательными."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if impressions < 0 or leads < 0:
                return Response(
                    {"detail": "Показы и лиды не могут быть отрицательными."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if impressions > 0 and leads > 0 and leads > impressions:
                return Response(
                    {"detail": "Лидов больше, чем показов — проверьте цифры."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 3. Валидация spend
            raw_spend = item.get("spend", 0)
            try:
                spend = Decimal(str(raw_spend if raw_spend is not None else 0))
            except (InvalidOperation, TypeError):
                return Response(
                    {"detail": "Сумма затрат указана неверно."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if spend < 0 or spend > Decimal("999999999.99"):
                return Response(
                    {"detail": "Сумма затрат указана неверно."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 4. Валидация note
            note = str(item.get("note") or "")
            if len(note) > 255:
                return Response(
                    {"detail": "Комментарий слишком длинный."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 5. Валидация id если передан
            item_id = item.get("id")
            uuid_id = None
            if item_id:
                try:
                    uuid_id = uuid.UUID(str(item_id))
                except (ValueError, TypeError):
                    return Response(
                        {"detail": f"Запись {item_id} не найдена или принадлежит другой компании."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not LeadAdSpend.objects.filter(id=uuid_id, company=company).exists():
                    return Response(
                        {"detail": f"Запись {item_id} не найдена или принадлежит другой компании."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            parsed_items.append({
                "id": uuid_id,
                "date": d,
                "impressions": impressions,
                "leads": leads,
                "spend": spend,
                "note": note,
            })

        # Применяем изменения в единой транзакции
        with transaction.atomic():
            kept_dates = [pi["date"] for pi in parsed_items]
            if date_from and date_to:
                LeadAdSpend.objects.filter(company=company, date__gte=date_from, date__lte=date_to).exclude(date__in=kept_dates).delete()
            else:
                LeadAdSpend.objects.filter(company=company).exclude(date__in=kept_dates).delete()

            for pi in parsed_items:
                item_id = pi["id"]
                if item_id:
                    rec = LeadAdSpend.objects.filter(id=item_id, company=company).first()
                    if rec:
                        rec.date = pi["date"]
                        rec.impressions = pi["impressions"]
                        rec.leads = pi["leads"]
                        rec.spend = pi["spend"]
                        rec.note = pi["note"]
                        rec.save()
                    else:
                        LeadAdSpend.objects.create(
                            company=company,
                            date=pi["date"],
                            impressions=pi["impressions"],
                            leads=pi["leads"],
                            spend=pi["spend"],
                            note=pi["note"],
                            created_by=request.user,
                        )
                else:
                    existing = LeadAdSpend.objects.filter(company=company, date=pi["date"]).first()
                    if existing:
                        existing.impressions = pi["impressions"]
                        existing.leads = pi["leads"]
                        existing.spend = pi["spend"]
                        existing.note = pi["note"]
                        existing.save()
                    else:
                        LeadAdSpend.objects.create(
                            company=company,
                            date=pi["date"],
                            impressions=pi["impressions"],
                            leads=pi["leads"],
                            spend=pi["spend"],
                            note=pi["note"],
                            created_by=request.user,
                        )

        final_qs = LeadAdSpend.objects.filter(company=company)
        if date_from and date_to:
            final_qs = final_qs.filter(date__gte=date_from, date__lte=date_to)
        final_qs = final_qs.order_by("-date")
        serializer = self.get_serializer(final_qs, many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)
