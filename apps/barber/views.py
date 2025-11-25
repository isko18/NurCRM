from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db.models.deletion import ProtectedError
from django.db import IntegrityError
from rest_framework.exceptions import ValidationError

from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend

from apps.users.models import Branch
from .models import Service, Client, Appointment, Document, Folder, ServiceCategory, Payout
from .serializers import (
    ServiceSerializer,
    ClientSerializer,
    AppointmentSerializer,
    FolderSerializer,
    DocumentSerializer,
    ServiceCategorySerializer,
    PayoutSerializer
)


# ---- DocumentFilter ----
class DocumentFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")
    folder = filters.UUIDFilter(field_name="folder__id")
    file_name = filters.CharFilter(field_name="file", lookup_expr="icontains")
    created_at = filters.DateTimeFromToRangeFilter()
    updated_at = filters.DateTimeFromToRangeFilter()

    class Meta:
        model = Document
        fields = ["name", "folder", "file_name", "created_at", "updated_at"]


# ==== Company + Branch scoped mixin ====
class CompanyQuerysetMixin:
    """
    Видимость данных:

      - company берётся из request.user.company/owned_company
      - branch определяется:
          1) «жёсткий» филиал пользователя:
               - user.branch (если есть и принадлежит компании)
               - единственный id в user.branch_ids (если список есть и в нём ровно 1 элемент)
          2) если жёсткого филиала нет — пробуем ?branch=<uuid>, если филиал принадлежит компании
          3) иначе branch = None (режим по всей компании)

      Логика выборки:
        - если branch определён → показываем только записи этого филиала;
        - если branch = None → показываем все записи компании (без ограничения по branch).

    Создание:
      - company берётся из request.user.company/owned_company
      - если есть активный филиал → он проставляется в branch;
      - если активного филиала нет → branch оставляем как есть (решает сериализатор/валидатор).

    Обновление:
      - company фиксируем;
      - branch НЕ трогаем (не переносим запись между филиалами случайно).
    """

    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None

        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        if company:
            return company

        # fallback: если вдруг company хранится через user.branch
        branch = getattr(user, "branch", None)
        if branch is not None:
            return getattr(branch, "company", None)

        return None

    def _model_has_field(self, field_name: str) -> bool:
        qs = getattr(self, "queryset", None)
        model = getattr(qs, "model", None)
        if not model:
            return False
        return field_name in {f.name for f in model._meta.get_fields()}

    def _fixed_branch_from_user(self, company):
        """
        «Жёстко» назначенный филиал сотрудника:
          - user.branch, если принадлежит компании
          - единственный id в user.branch_ids, если список есть и в нём ровно 1 элемент

        Такой филиал пользователь поменять не может через ?branch.
        """
        user = self._user()
        if not user or not company:
            return None

        company_id = getattr(company, "id", None)

        # 1) user.branch как объект
        if hasattr(user, "branch"):
            b = getattr(user, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 2) branch_ids: если ровно один филиал — считаем его фиксированным
        branch_ids = getattr(user, "branch_ids", None)
        if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
            try:
                br = Branch.objects.get(id=branch_ids[0], company_id=company_id)
                return br
            except Branch.DoesNotExist:
                pass

        return None

    def _active_branch(self):
        """
        Определяем активный филиал:

          1) «жёсткий» филиал пользователя (branch или один branch_id);
          2) если жёсткого нет — пробуем ?branch=<uuid>, если филиал принадлежит компании;
          3) иначе None (режим по всей компании).
        """
        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            return None

        company_id = getattr(company, "id", None)

        # 1) жёстко назначенный филиал
        fixed = self._fixed_branch_from_user(company)
        if fixed is not None:
            setattr(request, "branch", fixed)
            return fixed

        # 2) если жёсткого филиала нет — позволяем выбирать через ?branch
        branch_id = None
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
                # чужой/битый UUID — игнорируем
                pass

        # 3) никакого филиала → None (работаем по всей компании)
        setattr(request, "branch", None)
        return None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return qs.none()

        qs = qs.filter(company=company)

        if self._model_has_field("branch"):
            active_branch = self._active_branch()  # None или Branch
            if active_branch is not None:
                qs = qs.filter(branch=active_branch)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не задана компания.")

        kwargs = {"company": company}

        if self._model_has_field("branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                kwargs["branch"] = active_branch

        serializer.save(**kwargs)

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не задана компания.")
        serializer.save(company=company)


# ==== ServiceCategory ====
class ServiceCategoryListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = ServiceCategory.objects.all()
    serializer_class = ServiceCategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in ServiceCategory._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["name"]
    ordering_fields = ["name", "is_active"]
    ordering = ["name"]


class ServiceCategoryRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = ServiceCategory.objects.all()
    serializer_class = ServiceCategorySerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Service ====
class ServiceListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in Service._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    # category → теперь FK → ищем по названию категории
    search_fields = ["name", "category__name"]
    ordering_fields = ["name", "price", "is_active"]
    ordering = ["name"]

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError as e:
            msg = "Услуга с таким названием уже существует."
            s = str(e)
            if "uniq_service_name_global_per_company" in s:
                msg = "Глобальная услуга с таким названием уже существует в компании."
            elif "uniq_service_name_per_branch" in s:
                msg = "Услуга с таким названием уже существует в этом филиале."
            raise ValidationError({"name": msg})


class ServiceRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Client ====
class ClientListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in Client._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["full_name", "phone", "email", "notes"]
    ordering_fields = ["full_name", "created_at", "status"]
    ordering = ["-created_at"]


class ClientRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            qs = (
                Appointment.objects
                .filter(client=instance)
                .select_related("barber")
                .prefetch_related("services")
                .order_by("-start_at")
            )
            examples = []
            for a in qs[:3]:
                barber_name = None
                if a.barber:
                    if a.barber.first_name or a.barber.last_name:
                        barber_name = f"{a.barber.first_name} {a.barber.last_name}".strip()
                    else:
                        barber_name = a.barber.email
                service_names = list(a.services.values_list("name", flat=True))
                examples.append(
                    {
                        "start_at": a.start_at,
                        "services": service_names,
                        "barber": barber_name,
                        "status": a.status,
                    }
                )
            return Response(
                {
                    "detail": "Нельзя удалить клиента: есть связанные записи (appointments).",
                    "appointments_count": qs.count(),
                    "examples": examples,
                    "solutions": [
                        "Измените статус клиента на 'inactive' или 'blacklist' вместо удаления.",
                        "Либо удалите/переназначьте связанные записи.",
                    ],
                },
                status=status.HTTP_409_CONFLICT,
            )


# ==== Appointment ====
class AppointmentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = (
        Appointment.objects
        .select_related("client", "barber")
        .prefetch_related("services")
        .all()
    )
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in Appointment._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = [
        "client__full_name",
        "barber__first_name",
        "barber__last_name",
        "comment",
    ]
    ordering_fields = ["start_at", "end_at", "status", "created_at"]
    ordering = ["-start_at"]


class AppointmentRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = (
        Appointment.objects
        .select_related("client", "barber")
        .prefetch_related("services")
        .all()
    )
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Folder ====
class FolderListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related("parent").all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in Folder._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["name", "parent__name"]
    ordering_fields = ["name"]
    ordering = ["name"]


class FolderRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = Folder.objects.select_related("parent").all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Document ====
class DocumentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Document.objects.select_related("folder").all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = DocumentFilter
    search_fields = ["name", "folder__name", "file"]
    ordering_fields = ["name", "created_at", "updated_at"]
    ordering = ["name"]


class DocumentRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = Document.objects.select_related("folder").all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]



class PayoutListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/barber/payouts/        – список выплат (по компании/филиалу, с фильтрами)
    POST /api/barber/payouts/        – создать выплату + авторасчёт суммы
    """

    queryset = (
        Payout.objects
        .select_related("company", "branch", "barber")
        .all()
    )
    serializer_class = PayoutSerializer
    permission_classes = [permissions.IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in Payout._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = [
        "period",
        "comment",
        "barber__first_name",
        "barber__last_name",
        "barber__email",
    ]
    ordering_fields = ["created_at", "period", "payout_amount"]
    ordering = ["-created_at"]


class PayoutRetrieveUpdateDestroyView(
    CompanyQuerysetMixin,
    generics.RetrieveUpdateDestroyAPIView,
):
    """
    GET    /api/barber/payouts/<uuid:pk>/   – одна выплата
    PATCH  /api/barber/payouts/<uuid:pk>/   – изменить (например, комментарий/ставку)
    DELETE /api/barber/payouts/<uuid:pk>/   – удалить
    """

    queryset = (
        Payout.objects
        .select_related("company", "branch", "barber")
        .all()
    )
    serializer_class = PayoutSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    

class ServiceCategoryListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/barber/service-categories/      – список категорий (по компании/филиалу)
    POST /api/barber/service-categories/      – создать категорию услуги
    """
    queryset = ServiceCategory.objects.all()
    serializer_class = ServiceCategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in ServiceCategory._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["name"]
    ordering_fields = ["name", "is_active", "created_at"] if hasattr(ServiceCategory, "created_at") else ["name", "is_active"]
    ordering = ["name"]


class ServiceCategoryRetrieveUpdateDestroyView(
    CompanyQuerysetMixin,
    generics.RetrieveUpdateDestroyAPIView,
):
    """
    GET    /api/barber/service-categories/<uuid:pk>/   – одна категория
    PATCH  /api/barber/service-categories/<uuid:pk>/   – обновить
    DELETE /api/barber/service-categories/<uuid:pk>/   – удалить
    """
    queryset = ServiceCategory.objects.all()
    serializer_class = ServiceCategorySerializer
    permission_classes = [permissions.IsAuthenticated]