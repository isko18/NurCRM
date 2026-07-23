from rest_framework import generics, permissions, status, filters
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from django.http import HttpResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.db.models import Sum, Count, Q
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
    InboundLeadConsalting,
    LeadDistributionSettingsConsalting,
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
    InboundLeadConsaltingSerializer,
    LeadDistributionSettingsConsaltingSerializer,
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
    """Собирает payload доски воронки (идентичен для одиночного и bulk-эндпоинтов)."""
    leads_qs = (
        LeadConsalting.objects.filter(funnel=funnel, is_archived=False)
        .select_related("stage", "owner", "client")
    )
    # видимость: сотрудник видит общий пул + свои лиды, руководитель — все
    leads = list(apply_lead_visibility(leads_qs, request.user))

    columns = []
    for stage in funnel.stages.all():
        stage_leads = [l for l in leads if l.stage_id == stage.id]
        columns.append({
            "stage": FunnelStageConsaltingSerializer(stage, context=context).data,
            "leads": LeadConsaltingSerializer(stage_leads, many=True, context=context).data,
        })

    no_stage = [l for l in leads if l.stage_id is None]
    return {
        "funnel": FunnelConsaltingSerializer(funnel, context=context).data,
        "columns": columns,
        "unassigned": LeadConsaltingSerializer(no_stage, many=True, context=context).data,
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
        realtime.lead_deleted(instance)
        instance.delete()


class LeadMoveStageView(LeadVisibilityMixin, CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    Перемещение лида в другую стадию его воронки — через машину состояний.
    POST /api/consalting/leads/<uuid:pk>/move-stage/  { "stage": "<uuid>" }

    В «мягком» режиме (CONSALTING_FUNNEL_STRICT=False) недопустимые переходы
    выполняются, но фиксируются как нарушения в timeline. В «строгом» — 400.

    Сотрудник может двигать только лиды из общего пула или свои; руководитель —
    любые. Real-time о перемещении уходит на доску через сигнал stage_changed.
    """
    queryset = LeadConsalting.objects.select_related("funnel", "stage").all()
    serializer_class = LeadMoveStageSerializer

    def post(self, request, *args, **kwargs):
        lead = self.get_object()
        if not can_manage_leads(request.user, lead.funnel):
            raise PermissionDenied("Нет прав двигать лиды в этой воронке.")
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
        # выигрыш = закрытая продажа: создаём продажу-аналитику + абонентку (идемпотентно)
        apply_completion_side_effects(lead, actor=request.user)
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
                "updated_at": rate.updated_at.isoformat() if rate else None,
            })
        return Response({"results": results})


class ServiceSalaryRateUpdateView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """PUT /api/consalting/salary/rates/<service_id>/ — установить процент ставки."""
    def put(self, request, service_id, *args, **kwargs):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")
        if not is_owner_like(request.user):
            raise PermissionDenied("Управлять ставками может только руководитель.")

        svc = get_object_or_404(ServicesConsalting, pk=service_id, company=company)
        percent = request.data.get("percent", 0)
        try:
            percent_dec = Decimal(str(percent))
            if percent_dec < 0 or percent_dec > 100:
                raise ValueError()
        except (ValueError, TypeError, InvalidOperation):
            return Response({"percent": "Значение процента должно быть от 0 до 100."}, status=status.HTTP_400_BAD_REQUEST)

        rate, _ = ServiceSalaryRateConsalting.objects.get_or_create(
            company=company, service=svc, defaults={"percent": percent_dec}
        )
        if rate.percent != percent_dec:
            rate.percent = percent_dec
            rate.save(update_fields=["percent", "updated_at"])

        return Response({
            "service": str(svc.id),
            "service_name": svc.name,
            "price": float(svc.price or 0),
            "percent": str(rate.percent),
            "updated_at": rate.updated_at.isoformat()
        })


class SalaryAccrualListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """GET /api/consalting/salary/accruals/ — список начислений зарплаты."""
    serializer_class = SalaryAccrualConsaltingSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["service", "status", "user"]
    search_fields = ["user__first_name", "user__last_name", "user__email", "service__name"]
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return SalaryAccrualConsalting.objects.none()
        qs = SalaryAccrualConsalting.objects.filter(company=company).select_related("user", "service", "sale", "lead")
        if not is_owner_like(self.request.user):
            qs = qs.filter(user=self.request.user)

        date_from = self.request.query_params.get("date_from") or self.request.query_params.get("period_start")
        date_to = self.request.query_params.get("date_to") or self.request.query_params.get("period_end")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
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


class InboundLeadListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    """
    GET /api/consalting/inbound-leads/ — список входящих лидов.
    POST /api/consalting/inbound-leads/ — ручное создание лида.
    """
    serializer_class = InboundLeadConsaltingSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "owner", "source"]
    search_fields = ["full_name", "phone", "message"]
    ordering_fields = ["created_at", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return InboundLeadConsalting.objects.none()
        qs = InboundLeadConsalting.objects.filter(company=company).select_related("owner")
        if not is_owner_like(self.request.user):
            qs = qs.filter(owner=self.request.user)
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
        return Response(InboundLeadConsaltingSerializer(inbound_lead, context=self.get_serializer_context()).data)


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


class SubscriptionMatrixView(CompanyBranchQuerysetMixin, generics.GenericAPIView):
    """
    GET /api/consalting/clients/subscription-matrix/
    Абонентская матрица клиентов «ФИО × услуга × месяцы».
    """
    def get(self, request, *args, **kwargs):
        from datetime import date
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

        sales_qs = SaleConsalting.objects.filter(
            company=company, subscription_amount__gt=0, client__isnull=False
        ).select_related("client", "services", "subscription_deal")

        if not is_owner_like(request.user):
            sales_qs = sales_qs.filter(user=request.user)

        if search:
            sales_qs = sales_qs.filter(
                Q(client__full_name__icontains=search) | Q(services__name__icontains=search)
            )

        rows = []
        for sale in sales_qs:
            deal = ensure_subscription_deal(sale)
            if not deal:
                continue

            cells = {}
            for inst in deal.installments.order_by("number"):
                d = inst.due_date
                m_str = f"{d.year:04d}-{d.month:02d}"
                if m_str not in months_list:
                    continue

                paid = (inst.paid_amount or Decimal("0")) >= inst.amount
                if paid:
                    st_val = "paid"
                elif d < today:
                    st_val = "overdue"
                else:
                    st_val = "planned"

                cells[m_str] = {
                    "amount": float(inst.amount),
                    "status": st_val
                }

            rows.append({
                "client_id": str(sale.client_id),
                "client_name": sale.client.full_name or "Без имени",
                "service_id": str(sale.services_id) if sale.services_id else None,
                "service_name": sale.services.name if sale.services else "Услуга",
                "subscription_amount": float(sale.subscription_amount or 0),
                "subscription_period": sale.subscription_period or "month",
                "cells": cells,
            })

        return Response({
            "months": months_list,
            "rows": rows
        })



