from rest_framework import generics, permissions, status, filters
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from django.utils import timezone
from django.shortcuts import get_object_or_404

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
)
from .funnel.state_machine import (
    FunnelStateMachine, StateTransitionError, allowed_next_types,
)
from .funnel.activity import ActivityLogger
from .funnel.scoring import ScoringService
from .funnel.analytics import PipelineAnalytics
from .funnel.events import emit as emit_funnel_event
from .funnel import realtime
from .funnel.provisioning import provision_funnel_for_role
from .access import (
    is_owner_like, apply_lead_visibility, apply_client_visibility,
    visible_funnels_qs, can_view_funnel, can_manage_leads, can_manage_stages,
)
from apps.users.models import Branch, CustomRole
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
            serializer.save(company=company, branch=self._active_branch(), user=self.request.user)
        else:
            serializer.save(company=company, user=self.request.user)


class SaleConsaltingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = SaleConsalting.objects.select_related(
        "services", "tariff", "client", "user", "company"
    ).prefetch_related("items").all()
    serializer_class = SaleConsaltingSerializer


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
    leads_qs = LeadConsalting.objects.filter(funnel=funnel).select_related("stage", "owner", "client")
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
    filterset_fields = ["funnel", "stage", "owner", "client", "status", "branch"]

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
        if not can_manage_leads(self.request.user, serializer.instance.funnel):
            raise PermissionDenied("Нет прав изменять лиды в этой воронке.")
        prev_owner_id = serializer.instance.owner_id
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
