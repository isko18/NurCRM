from rest_framework import generics, permissions, status, filters
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from datetime import timedelta, datetime
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
    apply_completion_side_effects, ensure_subscription_deal, _add_months, accrue_salary_for_sale
)
from .access import (
    is_owner_like, apply_lead_visibility, apply_client_visibility,
    visible_funnels_qs, can_view_funnel, can_manage_leads, can_manage_stages,
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
        accrue_salary_for_sale(sale, seller=self.request.user)


class SaleConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SaleConsalting.objects.select_related(
        "services", "tariff", "client", "user", "company"
    ).prefetch_related("items").all()
    serializer_class = SaleConsaltingSerializer


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


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company").all()
    serializer_class = RequestsConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in RequestsConsalting._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class RequestsConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = RequestsConsalting.objects.select_related("client", "company").all()
    serializer_class = RequestsConsaltingSerializer


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
        return apply_client_visibility(qs, getattr(self.request, "user", None))


class ClientConsaltingListCreateView(ClientVisibilityMixin, CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    Клиенты консалтинга (общая модель main.Client, scope по компании/филиалу).
    GET/POST /api/consalting/clients/
    """
    queryset = Client.objects.select_related("company", "branch", "salesperson", "service").all()
    serializer_class = ClientSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "type", "date", "salesperson", "service", "branch"]
    search_fields = ["full_name", "phone", "email", "llc", "inn"]
    ordering_fields = ["created_at", "updated_at", "date", "full_name"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        kwargs = {"company": company}
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
        # пользовательские воронки создаёт только owner/admin (раздел 1.5)
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Создавать воронки может только владелец или администратор.")
        super().perform_create(serializer)


class FunnelConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = FunnelConsalting.objects.prefetch_related("stages").all()
    serializer_class = FunnelConsaltingSerializer

    def perform_update(self, serializer):
        if serializer.instance.is_protected:
            raise PermissionDenied("Эту воронку нельзя изменить или удалить.")
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Изменять воронки может только владелец или администратор.")
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        if instance.is_protected:
            raise PermissionDenied("Эту воронку нельзя изменить или удалить.")
        if not is_owner_like(self.request.user):
            raise PermissionDenied("Удалять воронки может только владелец или администратор.")
        instance.delete()


def _serialize_board(funnel, request, context):
    """Собирает payload доски воронки со счётчиками и суммами (§4.2)."""
    from django.db.models import Q, Count, Sum
    from datetime import timedelta
    from apps.consalting.models import WhatsAppMessageConsalting

    user = request.user
    is_mgr = is_owner_like(user)

    base_qs = LeadConsalting.objects.filter(funnel=funnel, is_archived=False)

    # 1. Защита доступа на уровне строки
    if not is_mgr:
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
    if specific_owner and is_mgr:
        base_qs = base_qs.filter(owner_id=specific_owner)

    # 3. Счётчики по всем скоупам (mine, pool, all) с учётом фильтров
    scope_agg = base_qs.aggregate(
        all_cnt=Count("id"),
        mine_cnt=Count("id", filter=Q(owner=user)),
        pool_cnt=Count("id", filter=Q(owner__isnull=True)),
    )
    scope_counts = {
        "mine": scope_agg["mine_cnt"] or 0,
        "pool": scope_agg["pool_cnt"] or 0,
        "all": (scope_agg["all_cnt"] or 0) if is_mgr else None,
    }

    # 4. Применение запрошенного скоупа (owner_scope)
    owner_scope = request.GET.get("owner_scope")
    if not owner_scope:
        owner_scope = "all" if is_mgr else "mine"
    elif not is_mgr and owner_scope == "all":
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

    for stage in funnel.stages.all():
        stage_qs = scoped_qs.filter(stage=stage).select_related("stage", "owner", "client")
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

    return {
        "funnel": FunnelConsaltingSerializer(funnel, context=context).data,
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
        ser = self.get_serializer(data=request.data)
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


class LeadConsaltingListCreateView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner", "client", "company").all()
    serializer_class = LeadConsaltingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["funnel", "stage", "owner", "client", "status", "branch",
                        "is_archived", "service", "tariff"]

    def perform_create(self, serializer):
        funnel = serializer.validated_data.get("funnel")
        if funnel and not can_manage_leads(self.request.user, funnel):
            raise PermissionDenied("Нет прав создавать лиды в этой воронке.")
        super().perform_create(serializer)
        realtime.lead_created(serializer.instance)


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


class LeadClaimView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    «Взять» лид себе: ставит owner=текущий пользователь.
    POST /api/consalting/leads/<uuid:pk>/claim/

    Доступны только лиды из общего пула (owner=None) или уже свои (видимость
    миксина). После взятия карточка пропадает у остальных сотрудников.
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage", "owner").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав брать лиды в этой воронке.")
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
        if not is_owner_like(request.user):
            raise PermissionDenied("Назначать ответственного может только руководитель.")

        company = self._user_company()
        lead = self.get_object()
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        owner = ser.validated_data["owner"]

        if company and getattr(owner, "company_id", None) not in (None, company.id):
            return Response({"owner": "Сотрудник из другой компании."},
                            status=status.HTTP_400_BAD_REQUEST)

        if lead.owner_id != owner.id:
            lead.owner = owner
            lead.save(update_fields=["owner", "updated_at"])
            realtime.lead_claimed(lead)
            # персональное уведомление назначенному сотруднику
            realtime.notify_user(owner.id, "lead.assigned", realtime.serialize_lead(lead))
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
        from .models import WhatsAppMessageConsalting, WazzupAccountConsalting
        from .funnel.wazzup import WazzupConsaltingService

        updated = WhatsAppMessageConsalting.objects.filter(
            lead=lead, direction=WhatsAppMessageConsalting.Direction.INBOUND
        ).exclude(status=WhatsAppMessageConsalting.Status.READ).update(
            status=WhatsAppMessageConsalting.Status.READ
        )

        account = WazzupAccountConsalting.objects.filter(company=lead.company, is_active=True).first()
        if account and lead.phone:
            WazzupConsaltingService.mark_chat_read(account, lead.phone)

        realtime.lead_updated(lead)
        return Response({"status": "ok", "marked_read_count": updated})


class LeadTransferView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Передать лид в другую воронку: создаёт НОВЫЙ лид в целевой воронке,
    копируя ключевые поля; исходный лид остаётся без изменений.
    POST /api/consalting/leads/<uuid:pk>/transfer/
        { "target_funnel": "<uuid>", "target_stage": "<uuid|null>" }
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage").all()
    serializer_class = LeadConsaltingSerializer

    _COPY_FIELDS = (
        "title", "full_name", "phone", "email", "source", "description",
        "estimated_value", "probability", "urgency",
    )

    def post(self, request, *args, **kwargs):
        company = self._user_company()
        lead = self.get_object()

        # права на исходную воронку
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав управлять лидами в исходной воронке.")

        target_funnel_id = request.data.get("target_funnel")
        if not target_funnel_id:
            return Response({"target_funnel": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            target_funnel = FunnelConsalting.objects.get(id=target_funnel_id, company=company)
        except (FunnelConsalting.DoesNotExist, ValueError, TypeError):
            return Response({"target_funnel": "Воронка не найдена."}, status=status.HTTP_404_NOT_FOUND)

        if target_funnel.id == lead.funnel_id:
            return Response({"target_funnel": "Нельзя передать в ту же воронку."},
                            status=status.HTTP_400_BAD_REQUEST)

        # права на целевую воронку
        if not can_manage_leads(request.user, target_funnel):
            raise PermissionDenied("Нет прав управлять лидами в целевой воронке.")

        # целевая стадия: переданная (должна быть из target_funnel) или intake
        target_stage = None
        target_stage_id = request.data.get("target_stage")
        if target_stage_id:
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

        # создаём новый лид в целевой воронке
        data = {f: getattr(lead, f) for f in self._COPY_FIELDS}
        new_lead = LeadConsalting.objects.create(
            company=target_funnel.company,
            branch=target_funnel.branch,
            funnel=target_funnel,
            stage=target_stage,
            owner=None,
            status=LeadConsalting.Status.NEW,
            source_lead=lead,
            stage_entered_at=timezone.now(),
            **data,
        )

        # аудит на исходном лиде (best-effort)
        try:
            ActivityLogger.log(
                lead, LeadActivityConsalting.Type.SYSTEM, actor=request.user,
                title=f"Лид передан в воронку «{target_funnel.name}»",
                payload={
                    "type": "lead_transferred",
                    "from_funnel": str(lead.funnel_id),
                    "to_funnel": str(target_funnel.id),
                    "source_lead_id": str(lead.id),
                    "new_lead_id": str(new_lead.id),
                    "actor_id": str(request.user.id),
                },
                touch_last_activity=False,
            )
        except Exception:
            pass

        realtime.lead_created(new_lead)
        return Response(
            LeadConsaltingSerializer(new_lead, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
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
    Создать клиента из лида и привязать к лиду.
    POST /api/consalting/leads/<uuid:pk>/create-client/
        { "full_name", "phone", "email", "service"?, "note"? }
    """
    queryset = LeadConsalting.objects.select_related("funnel", "service", "client").all()
    serializer_class = LeadConsaltingSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав управлять лидами в этой воронке.")
        company = self._user_company()

        if lead.client_id:
            client = lead.client
        else:
            service = lead.service
            service_id = request.data.get("service")
            if service_id:
                service = ServicesConsalting.objects.filter(id=service_id, company=company).first() or service
            client = Client.objects.create(
                company=company,
                branch=lead.branch,
                full_name=request.data.get("full_name") or lead.full_name or lead.title,
                phone=request.data.get("phone") or lead.phone or "",
                email=request.data.get("email") or lead.email or "",
                salesperson=request.user,
                service=service,
            )
            lead.client = client
            lead.save(update_fields=["client", "updated_at"])
            realtime.lead_updated(lead)

        ctx = self.get_serializer_context()
        return Response(
            {
                "client": ClientSerializer(client, context=ctx).data,
                "lead": LeadConsaltingSerializer(lead, context=ctx).data,
            },
            status=status.HTTP_201_CREATED,
        )


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
            return Response({"detail": "У лида должен быть указан или создан клиент перед регистрацией оплаты."},
                            status=status.HTTP_400_BAD_REQUEST)

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
        sub_enabled = request.data.get("subscription_enabled")
        if sub_enabled is None:
            sub_enabled = True if (lead.tariff and (lead.tariff.subscription_amount or 0) > 0) else False

        sub_amount = request.data.get("subscription_amount")
        sub_period = request.data.get("subscription_period")
        sub_start = request.data.get("subscription_start")

        sale = SaleConsalting.objects.filter(lead=lead).first()
        if not sale and (sub_enabled or (lead.tariff and (lead.tariff.subscription_amount or 0) > 0)):
            tariff = lead.tariff
            sale = SaleConsalting.objects.create(
                company=lead.company, branch=lead.branch, user=lead.owner or request.user,
                services=lead.service, tariff=tariff, client=lead.client, lead=lead,
                total=amount,
                subscription_amount=sub_amount or (tariff.subscription_amount if tariff else 0),
                subscription_period=sub_period or (tariff.subscription_period if tariff else "month"),
            )

        if sale:
            from .funnel.completion import create_sale_side_effects
            create_sale_side_effects(
                sale,
                subscription_enabled=sub_enabled,
                subscription_start=sub_start,
                subscription_amount=sub_amount,
                subscription_period=sub_period,
            )

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

        lead.budget_confirmed = True  # выигрыш подразумевает подтверждённый бюджет
        LeadConsalting.objects.filter(pk=lead.pk).update(budget_confirmed=True)
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

        lead.loss_reason = loss_reason
        lead.loss_comment = v.get("loss_comment", "")
        LeadConsalting.objects.filter(pk=lead.pk).update(
            loss_reason=loss_reason, loss_comment=lead.loss_comment
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
        company = self._user_company()
        if not company:
            return InboundLeadConsalting.objects.none()

        qs = InboundLeadConsalting.objects.filter(company=company).select_related("owner", "sale", "lead")

        user = self.request.user
        if not is_owner_like(user):
            qs = qs.filter(owner=user)
        else:
            owner_param = self.request.query_params.get("owner")
            if owner_param:
                if owner_param.lower() in ("none", "null"):
                    qs = qs.filter(owner__isnull=True)
                else:
                    qs = qs.filter(owner_id=owner_param)

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
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = self.request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        overdue_param = self.request.query_params.get("overdue")
        if overdue_param and overdue_param.lower() in ("true", "1"):
            qs = qs.filter(
                status=InboundLeadConsalting.Status.DEFERRED,
                remind_at__lte=timezone.now()
            )

        ordering = self.request.query_params.get("ordering", "-created_at")
        if ordering:
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by("-created_at")

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        inbound_lead = serializer.save(company=company)
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
        if not is_owner_like(self.request.user):
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
        if not is_owner_like(request.user):
            raise PermissionDenied("Назначать лиды может только руководитель.")

        inbound_lead = get_object_or_404(InboundLeadConsalting, pk=pk, company=company)
        owner_id = request.data.get("owner")
        if not owner_id:
            return Response({"owner": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)

        new_owner = get_object_or_404(User, pk=owner_id, company=company)
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
                if owner_param.lower() in ("none", "null"):
                    qs = qs.filter(owner__isnull=True)
                else:
                    qs = qs.filter(owner_id=owner_param)

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
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

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
                if owner_param.lower() in ("none", "null"):
                    qs = qs.filter(owner__isnull=True)
                else:
                    qs = qs.filter(owner_id=owner_param)

        source_param = request.query_params.get("source")
        if source_param:
            qs = qs.filter(source=source_param)

        date_from_str = request.query_params.get("date_from")
        date_to_str = request.query_params.get("date_to")

        if date_from_str:
            qs = qs.filter(created_at__date__gte=date_from_str)
        if date_to_str:
            qs = qs.filter(created_at__date__lte=date_to_str)

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

        return Response({
            "totals": totals,
            "by_source": by_source_list,
            "by_user": by_user_list,
            "by_day": by_day_list,
            "defer_reasons": defer_reasons,
            "reject_reasons": reject_reasons,
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
            return Response({"detail": "Платёж уже оплачен."}, status=status.HTTP_400_BAD_REQUEST)

        cashbox = request.data.get("cashbox")
        payment_method = request.data.get("payment_method") or "cash"

        payment.status = SubscriptionPaymentConsalting.Status.PAID
        payment.paid_at = timezone.now()
        if cashbox:
            try:
                payment.cashbox_id = uuid.UUID(str(cashbox))
            except ValueError:
                pass
        payment.payment_method = payment_method
        payment.save()

        return Response(SubscriptionPaymentConsaltingSerializer(payment).data)


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
        if not is_owner_like(self.request.user):
            qs = qs.filter(user=self.request.user)

        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        kind_param = self.request.query_params.get("kind")
        if kind_param:
            qs = qs.filter(kind=kind_param)

        user_param = self.request.query_params.get("user")
        if user_param and is_owner_like(self.request.user):
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
        if not is_owner_like(request.user):
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

        if not is_owner_like(request.user):
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

        if not is_owner_like(request.user):
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

        if not is_owner_like(self.request.user):
            qs = qs.filter(user=self.request.user)

        user_param = self.request.query_params.get("user")
        if user_param and is_owner_like(self.request.user):
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



