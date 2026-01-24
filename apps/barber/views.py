from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import SearchFilter, OrderingFilter
from django.utils import timezone
from django.utils.dateparse import parse_date
from decimal import Decimal
from django.db.models.deletion import ProtectedError
from django.db import IntegrityError, models
from django.db.models import Q, Prefetch, Count, Sum, Avg, F, Value, DecimalField, ExpressionWrapper

from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend

from apps.users.models import Branch, Company
from .models import Service, Client, Appointment, Document, Folder, ServiceCategory, Payout, PayoutSale, ProductSalePayout, OnlineBooking

from .serializers import (
    ServiceSerializer,
    ClientSerializer,
    AppointmentSerializer,
    AppointmentHistoryRowSerializer,
    FolderSerializer,
    DocumentSerializer,
    ServiceCategorySerializer,
    PayoutSerializer,
    PayoutSaleSerializer,
    ProductSalePayoutSerializer,
    OnlineBookingCreateSerializer,
    OnlineBookingSerializer,
    OnlineBookingStatusUpdateSerializer,
    PublicServiceSerializer,
    PublicServiceCategorySerializer,
    PublicMasterSerializer,
    PublicMasterScheduleSerializer,
    PublicMasterAvailabilitySerializer,
    BarberAnalyticsResponseSerializer
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


# ---- Appointment / visits history filters ----
class _CharInFilter(filters.BaseInFilter, filters.CharFilter):
    """Поддержка query param вида: ?status__in=completed,canceled,no_show"""


class ClientVisitHistoryFilter(filters.FilterSet):
    status__in = _CharInFilter(field_name="status", lookup_expr="in")
    start_at_from = filters.DateTimeFilter(field_name="start_at", lookup_expr="gte")
    start_at_to = filters.DateTimeFilter(field_name="start_at", lookup_expr="lte")

    class Meta:
        model = Appointment
        fields = ["status", "barber", "start_at"]


class VisitHistoryFilter(filters.FilterSet):
    """
    Фильтры общего списка истории визитов.
    """

    status__in = _CharInFilter(field_name="status", lookup_expr="in")
    start_at_from = filters.DateTimeFilter(field_name="start_at", lookup_expr="gte")
    start_at_to = filters.DateTimeFilter(field_name="start_at", lookup_expr="lte")
    client = filters.UUIDFilter(field_name="client_id")
    barber = filters.UUIDFilter(field_name="barber_id")

    class Meta:
        model = Appointment
        fields = ["status", "client", "barber", "start_at"]


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


class ClientVisitHistoryListView(CompanyQuerysetMixin, generics.ListAPIView):
    """
    История визитов конкретного клиента.

    GET /api/barbershop/clients/<uuid:pk>/visits/history/
      - по умолчанию возвращает только "исторические" статусы: completed/canceled/no_show
      - можно переопределить через ?status=... или ?status__in=...
      - можно ограничить по дате: ?start_at_from=...&start_at_to=...
    """

    queryset = (
        Appointment.objects
        .select_related("client", "barber")
        .prefetch_related(Prefetch("services", queryset=Service.objects.select_related("category")))
        .all()
    )
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = ClientVisitHistoryFilter
    ordering_fields = ["start_at", "end_at", "status", "created_at"]
    ordering = ["-start_at"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return self.queryset.none()

        client = generics.get_object_or_404(Client, pk=self.kwargs["pk"], company=company)

        qs = self.queryset.filter(company=company, client=client)

        # Если выбран активный филиал — показываем и глобальные записи (branch=NULL)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))

        # Дефолт: история = завершённые/отменённые/не пришёл
        qp = getattr(self.request, "query_params", {})
        if "status" not in qp and "status__in" not in qp:
            qs = qs.filter(status__in=[
                Appointment.Status.COMPLETED,
                Appointment.Status.CANCELED,
                Appointment.Status.NO_SHOW,
            ])

        return qs


class VisitHistoryListView(CompanyQuerysetMixin, generics.ListAPIView):
    """
    Общая история визитов (по всем клиентам).

    GET /api/barbershop/visits/history/
      - по умолчанию возвращает только "исторические" статусы: completed/canceled/no_show
      - можно переопределить через ?status=... или ?status__in=...
      - можно ограничить по дате: ?start_at_from=...&start_at_to=...
      - можно фильтровать по мастеру/клиенту: ?barber=<uuid>&client=<uuid>
    """

    queryset = (
        Appointment.objects
        .select_related("client", "barber")
        .all()
    )
    serializer_class = AppointmentHistoryRowSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = VisitHistoryFilter
    ordering_fields = ["start_at", "end_at", "status", "created_at"]
    ordering = ["-start_at"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return self.queryset.none()

        qs = self.queryset.filter(company=company)

        # Если выбран активный филиал — показываем и глобальные записи (branch=NULL)
        active_branch = self._active_branch()
        if active_branch is not None:
            qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))

        # Дефолт: история = завершённые/отменённые/не пришёл
        qp = getattr(self.request, "query_params", {})
        if "status" not in qp and "status__in" not in qp:
            qs = qs.filter(status__in=[
                Appointment.Status.COMPLETED,
                Appointment.Status.CANCELED,
                Appointment.Status.NO_SHOW,
            ])

        return qs


# ==== Appointment ====
class AppointmentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = (
        Appointment.objects
        .select_related("client", "barber")
        .prefetch_related(Prefetch("services", queryset=Service.objects.select_related("category")))
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
        .prefetch_related(Prefetch("services", queryset=Service.objects.select_related("category")))
        .all()
    )
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]


class MyAppointmentListView(CompanyQuerysetMixin, generics.ListAPIView):
    """
    Эндпоинт "мои записи" — возвращает только записи, где текущий пользователь = barber.
    """

    queryset = (
        Appointment.objects
        .select_related("client", "barber")
        .prefetch_related(Prefetch("services", queryset=Service.objects.select_related("category")))
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
        "comment",
    ]
    ordering_fields = ["start_at", "end_at", "status", "created_at"]
    ordering = ["-start_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = getattr(self.request, "user", None)
        return qs.filter(barber=user)


class MyAppointmentDetailView(CompanyQuerysetMixin, generics.RetrieveAPIView):
    """
    Эндпоинт "моя запись" — детальный просмотр только своих записей.
    """

    queryset = (
        Appointment.objects
        .select_related("client", "barber")
        .prefetch_related(Prefetch("services", queryset=Service.objects.select_related("category")))
        .all()
    )
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        user = getattr(self.request, "user", None)
        return qs.filter(barber=user)


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


def _analytics_date_range(request):
    """
    date_from/date_to в формате YYYY-MM-DD.
    По умолчанию: текущий месяц (с 1-го числа по сегодня).
    """
    today = timezone.localdate()

    raw_from = request.query_params.get("date_from") if hasattr(request, "query_params") else request.GET.get("date_from")
    raw_to = request.query_params.get("date_to") if hasattr(request, "query_params") else request.GET.get("date_to")

    date_from = parse_date(raw_from) if raw_from else today.replace(day=1)
    date_to = parse_date(raw_to) if raw_to else today

    if not date_from:
        raise ValidationError({"date_from": "Неверный формат даты. Используйте YYYY-MM-DD."})
    if not date_to:
        raise ValidationError({"date_to": "Неверный формат даты. Используйте YYYY-MM-DD."})
    if date_to < date_from:
        raise ValidationError({"date_to": "date_to должен быть >= date_from."})

    return date_from, date_to


def _can_view_barber_analytics(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = str(getattr(user, "role", "") or "").strip().lower()
    if role in {"admin", "owner"}:
        return True
    return bool(getattr(user, "can_view_barber_history", False) or getattr(user, "can_view_barber_records", False))


def _build_barber_analytics(base_qs, date_from, date_to, include_masters: bool):
    """
    base_qs уже должен быть скоупнут по company/branch.
    """
    qs = base_qs.filter(start_at__date__gte=date_from, start_at__date__lte=date_to)

    effective_price = ExpressionWrapper(
        F("price") * (Value(Decimal("1")) - (F("discount") / Value(Decimal("100")))),
        output_field=DecimalField(max_digits=14, decimal_places=6),
    )

    totals = qs.aggregate(
        appointments_total=Count("id"),
        appointments_completed=Count("id", filter=Q(status=Appointment.Status.COMPLETED)),
        appointments_canceled=Count("id", filter=Q(status=Appointment.Status.CANCELED)),
        appointments_no_show=Count("id", filter=Q(status=Appointment.Status.NO_SHOW)),
        revenue=Sum(effective_price, filter=Q(status=Appointment.Status.COMPLETED)),
        avg_ticket=Avg(effective_price, filter=Q(status=Appointment.Status.COMPLETED)),
    )

    totals["revenue"] = totals["revenue"] or 0

    services_rows = (
        Service.objects
        .filter(appointments__in=qs.filter(status=Appointment.Status.COMPLETED))
        .values("id", "name")
        .annotate(
            count=Count("appointments", distinct=True),
            revenue=Sum("price"),
        )
        .order_by("-revenue", "-count", "name")
    )

    masters_rows = []
    if include_masters:
        masters_rows = list(
            qs.filter(status=Appointment.Status.COMPLETED)
            .values("barber_id", "barber__first_name", "barber__last_name", "barber__email")
            .annotate(
                count=Count("id"),
                revenue=Sum(effective_price),
            )
            .order_by("-revenue", "-count")
        )
        for r in masters_rows:
            first = r.pop("barber__first_name") or ""
            last = r.pop("barber__last_name") or ""
            email = r.pop("barber__email") or ""
            full = f"{first} {last}".strip()
            r["master_id"] = r.pop("barber_id")
            r["master_name"] = full or email
            r["revenue"] = r["revenue"] or 0

    data = {
        "date_from": date_from,
        "date_to": date_to,
        "totals": {
            "appointments_total": totals["appointments_total"] or 0,
            "appointments_completed": totals["appointments_completed"] or 0,
            "appointments_canceled": totals["appointments_canceled"] or 0,
            "appointments_no_show": totals["appointments_no_show"] or 0,
            "revenue": totals["revenue"],
            "avg_ticket": totals["avg_ticket"],
        },
        "services": [
            {
                "service_id": r["id"],
                "name": r["name"],
                "count": r["count"] or 0,
                "revenue": r["revenue"] or 0,
            }
            for r in services_rows
        ],
    }
    if include_masters:
        data["masters"] = masters_rows
    return data


class BarberAnalyticsView(CompanyQuerysetMixin, generics.GenericAPIView):
    """
    Общая аналитика по барбершопу (по компании/филиалу).

    Query params:
      - date_from: YYYY-MM-DD (опционально)
      - date_to: YYYY-MM-DD (опционально)
      - branch: UUID (опционально, учитывается CompanyQuerysetMixin)
    """

    queryset = (
        Appointment.objects
        .select_related("barber", "client")
        .prefetch_related("services")
        .all()
    )
    serializer_class = BarberAnalyticsResponseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        if not _can_view_barber_analytics(getattr(request, "user", None)):
            raise PermissionDenied("Нет доступа к аналитике барбершопа.")
        date_from, date_to = _analytics_date_range(request)
        data = _build_barber_analytics(self.get_queryset(), date_from, date_to, include_masters=True)
        return Response(self.serializer_class(data).data)


class MyBarberAnalyticsView(CompanyQuerysetMixin, generics.GenericAPIView):
    """
    Аналитика мастера (только по своим записям).

    Query params:
      - date_from: YYYY-MM-DD (опционально)
      - date_to: YYYY-MM-DD (опционально)
      - branch: UUID (опционально, учитывается CompanyQuerysetMixin)
    """

    queryset = (
        Appointment.objects
        .select_related("barber", "client")
        .prefetch_related("services")
        .all()
    )
    serializer_class = BarberAnalyticsResponseSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        date_from, date_to = _analytics_date_range(request)
        user = getattr(request, "user", None)
        data = _build_barber_analytics(self.get_queryset().filter(barber=user), date_from, date_to, include_masters=False)
        return Response(self.serializer_class(data).data)


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

class ProductSalePayoutListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/barber/product-sale-payouts/  – список начислений с процента от товара
    POST /api/barber/product-sale-payouts/  – создать одно начисление (по форме модалки)
    """

    queryset = (
        ProductSalePayout.objects
        .select_related("company", "branch", "product", "employee")
        .all()
    )
    serializer_class = ProductSalePayoutSerializer
    permission_classes = [permissions.IsAuthenticated]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        f.name for f in ProductSalePayout._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]
    search_fields = [
        "product__name",
        "employee__first_name",
        "employee__last_name",
        "employee__email",
    ]
    ordering_fields = ["created_at", "price", "payout_amount", "percent"]
    ordering = ["-created_at"]


class ProductSalePayoutRetrieveUpdateDestroyView(
    CompanyQuerysetMixin,
    generics.RetrieveUpdateDestroyAPIView,
):
    """
    GET    /api/barber/product-sale-payouts/<uuid:pk>/   – одно начисление
    PATCH  /api/barber/product-sale-payouts/<uuid:pk>/   – можно править процент/цену/комментарий
    DELETE /api/barber/product-sale-payouts/<uuid:pk>/   – удалить начисление
    """

    queryset = (
        ProductSalePayout.objects
        .select_related("company", "branch", "product", "employee")
        .all()
    )
    serializer_class = ProductSalePayoutSerializer
    permission_classes = [permissions.IsAuthenticated]    
    
class PayoutSaleListCreateView(
    CompanyQuerysetMixin,
    generics.ListCreateAPIView
):

    queryset = PayoutSale.objects.all()
    serializer_class = PayoutSaleSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = [
        f.name for f in PayoutSale._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["period"]
    ordering_fields = ["period", "total", "old_total_fund", "new_total_fund"]
    ordering = ["-period"]

class PayoutSaleRetrieveUpdateDestroyView(
    CompanyQuerysetMixin,
    generics.RetrieveUpdateDestroyAPIView
):
    queryset = PayoutSale.objects.all()
    serializer_class = PayoutSaleSerializer
    permission_classes = [permissions.IsAuthenticated]


# ===========================
# OnlineBooking - Публичный эндпоинт для создания заявки
# ===========================
class OnlineBookingPublicCreateView(generics.CreateAPIView):
    """
    Публичный эндпоинт для создания заявки на онлайн запись.
    Доступен без авторизации, но требует slug компании в URL.
    URL: /api/barbershop/public/{company_slug}/bookings/
    """
    serializer_class = OnlineBookingCreateSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    
    def get_company(self):
        """Получаем компанию по slug из URL"""
        slug = self.kwargs.get('company_slug')
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise ValidationError({'detail': 'Компания не найдена'})
    
    def perform_create(self, serializer):
        """Создаем заявку с автоматическим определением компании"""
        company = self.get_company()
        serializer.save(
            company=company,
            status=OnlineBooking.Status.NEW
        )


# ===========================
# OnlineBooking - Защищенные эндпоинты для управления заявками
# ===========================
class OnlineBookingListView(CompanyQuerysetMixin, generics.ListAPIView):
    """
    Список заявок на онлайн запись (только для авторизованных пользователей компании).
    GET /api/barbershop/bookings/
    """
    queryset = OnlineBooking.objects.select_related('company', 'branch').all()
    serializer_class = OnlineBookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'date', 'payment_method', 'branch']
    search_fields = ['client_name', 'client_phone']
    ordering_fields = ['created_at', 'date', 'time_start']
    ordering = ['-created_at']


class OnlineBookingDetailView(CompanyQuerysetMixin, generics.RetrieveAPIView):
    """
    Детали заявки.
    GET /api/barbershop/bookings/{pk}/
    """
    queryset = OnlineBooking.objects.select_related('company', 'branch').all()
    serializer_class = OnlineBookingSerializer
    permission_classes = [permissions.IsAuthenticated]


class OnlineBookingStatusUpdateView(CompanyQuerysetMixin, generics.UpdateAPIView):
    """
    Изменение статуса заявки.
    PATCH /api/barbershop/bookings/{pk}/status/
    """
    queryset = OnlineBooking.objects.select_related('company', 'branch').all()
    serializer_class = OnlineBookingStatusUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_serializer_class(self):
        return OnlineBookingStatusUpdateSerializer


# ===========================
# Публичные эндпоинты для онлайн-записи
# ===========================
class PublicServicesListView(generics.ListAPIView):
    """
    Публичный эндпоинт для получения услуг компании.
    Доступен без авторизации, требует slug компании в URL.
    URL: /api/barbershop/public/{company_slug}/services/
    
    Query params:
        - branch: UUID филиала (опционально)
    """
    serializer_class = PublicServiceSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    
    def get_company(self):
        """Получаем компанию по slug из URL"""
        slug = self.kwargs.get('company_slug')
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise ValidationError({'detail': 'Компания не найдена'})
    
    def get_queryset(self):
        company = self.get_company()
        qs = Service.objects.filter(
            company=company,
            is_active=True
        ).select_related('category').order_by('category__name', 'name')
        
        # Фильтрация по филиалу (если указан)
        branch_id = self.request.query_params.get('branch')
        if branch_id:
            try:
                branch = Branch.objects.get(id=branch_id, company=company)
                # Показываем глобальные услуги + услуги филиала
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=branch))
            except (Branch.DoesNotExist, ValueError):
                # Если филиал не найден - показываем только глобальные
                qs = qs.filter(branch__isnull=True)
        
        return qs


class PublicServiceCategoriesListView(generics.ListAPIView):
    """
    Публичный эндпоинт для получения категорий услуг с услугами.
    Доступен без авторизации, требует slug компании в URL.
    URL: /api/barbershop/public/{company_slug}/service-categories/
    
    Query params:
        - branch: UUID филиала (опционально)
    """
    serializer_class = PublicServiceCategorySerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    
    def get_company(self):
        """Получаем компанию по slug из URL"""
        slug = self.kwargs.get('company_slug')
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise ValidationError({'detail': 'Компания не найдена'})
    
    def get_queryset(self):
        company = self.get_company()
        
        # Фильтрация по филиалу
        branch_id = self.request.query_params.get('branch')
        branch_filter = Q(branch__isnull=True)
        services_branch_filter = Q(branch__isnull=True)
        
        if branch_id:
            try:
                branch = Branch.objects.get(id=branch_id, company=company)
                branch_filter = Q(branch__isnull=True) | Q(branch=branch)
                services_branch_filter = Q(branch__isnull=True) | Q(branch=branch)
            except (Branch.DoesNotExist, ValueError):
                pass
        
        # Получаем категории с активными услугами
        qs = ServiceCategory.objects.filter(
            company=company,
            is_active=True
        ).filter(branch_filter).prefetch_related(
            Prefetch(
                'services',
                queryset=Service.objects.filter(
                    is_active=True,
                    company=company
                ).filter(services_branch_filter).order_by('name')
            )
        ).order_by('name')
        
        return qs


class PublicMastersListView(generics.ListAPIView):
    """
    Публичный эндпоинт для получения мастеров компании.
    Доступен без авторизации, требует slug компании в URL.
    URL: /api/barbershop/public/{company_slug}/masters/
    
    Мастера определяются по одному из критериев:
    1. У пользователя есть записи (appointments) как barber
    2. У пользователя установлен флаг can_view_barber_records=True
    3. Роль пользователя = 'barber' или 'master'
    
    Query params:
        - branch: UUID филиала (опционально)
    """
    serializer_class = PublicMasterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    
    def get_company(self):
        """Получаем компанию по slug из URL"""
        slug = self.kwargs.get('company_slug')
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise ValidationError({'detail': 'Компания не найдена'})
    
    def get_queryset(self):
        from apps.users.models import User
         
        company = self.get_company()
         
        # Получаем ID пользователей, у которых есть записи как мастер
        masters_with_appointments = Appointment.objects.filter(
            company=company
        ).values_list('barber_id', flat=True).distinct()
         
        # Получаем сотрудников компании, которые считаются мастерами по любому критерию:
        # 1) Есть записи как мастер (barber)
        # 2) У сотрудника есть доступ к модулям барбершопа (обычно ставится мастерам/админу)
        # 3) Кастомная роль / роль в филиале указывает на мастера
        qs = User.objects.filter(
            company=company,
            is_active=True
        ).filter(
            Q(id__in=masters_with_appointments) |
            Q(can_view_barber_records=True) |
            Q(can_view_barber_services=True) |
            Q(can_view_barber_clients=True) |
            Q(can_view_barber_history=True) |
            Q(custom_role__name__in=["barber", "master", "Барбер", "Мастер", "барбер", "мастер"]) |
            Q(branch_memberships__role__in=["barber", "master", "Барбер", "Мастер", "барбер", "мастер"])
        ).distinct().order_by('first_name', 'last_name')
         
        # Фильтрация по филиалу (если указан)
        branch_id = self.request.query_params.get('branch')
        if branch_id:
            try:
                branch = Branch.objects.get(id=branch_id, company=company)
                # Показываем мастеров, привязанных к филиалу + "глобальных" (без филиалов)
                qs = qs.filter(Q(branches=branch) | Q(branches__isnull=True)).distinct()
            except (Branch.DoesNotExist, ValueError):
                pass
         
        return qs


class PublicMasterScheduleView(generics.GenericAPIView):
    """
    Публичный эндпоинт для получения занятых слотов мастера.
    URL: /api/barbershop/public/{company_slug}/masters/{master_id}/schedule/
    
    Query params:
        - date: дата в формате YYYY-MM-DD (обязательно)
        - days: количество дней вперед (по умолчанию 1, максимум 14)
    
    Возвращает занятые временные слоты мастера на указанную дату/период.
    """
    serializer_class = PublicMasterScheduleSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    
    def get_company(self):
        slug = self.kwargs.get('company_slug')
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise ValidationError({'detail': 'Компания не найдена'})
    
    def get(self, request, *args, **kwargs):
        from datetime import datetime, timedelta
        from apps.users.models import User
        
        company = self.get_company()
        master_id = self.kwargs.get('master_id')
        
        # Проверяем, что мастер существует и принадлежит компании
        try:
            master = User.objects.get(id=master_id, company=company, is_active=True)
        except User.DoesNotExist:
            raise ValidationError({'detail': 'Мастер не найден'})
        
        # Получаем параметры запроса
        date_str = request.query_params.get('date')
        if not date_str:
            raise ValidationError({'date': 'Параметр date обязателен (формат: YYYY-MM-DD)'})
        
        try:
            start_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            raise ValidationError({'date': 'Неверный формат даты. Используйте YYYY-MM-DD'})
        
        # Количество дней
        try:
            days = min(int(request.query_params.get('days', 1)), 14)
        except ValueError:
            days = 1
        
        end_date = start_date + timedelta(days=days)
        
        # Получаем занятые слоты мастера
        # Показываем только активные записи (booked, confirmed)
        busy_slots = Appointment.objects.filter(
            company=company,
            barber=master,
            start_at__date__gte=start_date,
            start_at__date__lt=end_date,
            status__in=[Appointment.Status.BOOKED, Appointment.Status.CONFIRMED]
        ).order_by('start_at').values('id', 'start_at', 'end_at')
        
        # Формируем ответ
        result = {
            'master_id': str(master.id),
            'master_name': f"{master.first_name or ''} {master.last_name or ''}".strip() or master.email,
            'date_from': start_date.isoformat(),
            'date_to': (end_date - timedelta(days=1)).isoformat(),
            'busy_slots': list(busy_slots),
            'work_start': '09:00',
            'work_end': '21:00'
        }
        
        return Response(result)


class PublicMastersAvailabilityView(generics.GenericAPIView):
    """
    Публичный эндпоинт для получения доступности всех мастеров на дату.
    URL: /api/barbershop/public/{company_slug}/masters/availability/
    
    Query params:
        - date: дата в формате YYYY-MM-DD (обязательно)
        - branch: UUID филиала (опционально)
    
    Возвращает список мастеров с их занятыми слотами на указанную дату.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    
    def get_company(self):
        slug = self.kwargs.get('company_slug')
        try:
            return Company.objects.get(slug=slug)
        except Company.DoesNotExist:
            raise ValidationError({'detail': 'Компания не найдена'})
    
    def get(self, request, *args, **kwargs):
        from datetime import datetime, timedelta
        from apps.users.models import User
        
        company = self.get_company()
        
        # Получаем дату
        date_str = request.query_params.get('date')
        if not date_str:
            raise ValidationError({'date': 'Параметр date обязателен (формат: YYYY-MM-DD)'})
        
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            raise ValidationError({'date': 'Неверный формат даты. Используйте YYYY-MM-DD'})
        
        # Получаем мастеров
        masters_with_appointments = Appointment.objects.filter(
            company=company
        ).values_list('barber_id', flat=True).distinct()
        
        masters_qs = User.objects.filter(
            company=company,
            is_active=True
        ).filter(
            Q(id__in=masters_with_appointments) |
            Q(can_view_barber_records=True) |
            Q(can_view_barber_services=True) |
            Q(can_view_barber_clients=True) |
            Q(can_view_barber_history=True) |
            Q(custom_role__name__in=["barber", "master", "Барбер", "Мастер", "барбер", "мастер"]) |
            Q(branch_memberships__role__in=["barber", "master", "Барбер", "Мастер", "барбер", "мастер"])
        ).distinct()
         
        # Фильтрация по филиалу
        branch_id = request.query_params.get('branch')
        if branch_id:
            try:
                branch = Branch.objects.get(id=branch_id, company=company)
                masters_qs = masters_qs.filter(Q(branches=branch) | Q(branches__isnull=True)).distinct()
            except (Branch.DoesNotExist, ValueError):
                pass
        
        # Получаем все занятые слоты на дату
        busy_appointments = Appointment.objects.filter(
            company=company,
            start_at__date=target_date,
            status__in=[Appointment.Status.BOOKED, Appointment.Status.CONFIRMED]
        ).select_related('barber').order_by('start_at')
        
        # Группируем по мастерам
        busy_by_master = {}
        for apt in busy_appointments:
            if apt.barber_id not in busy_by_master:
                busy_by_master[apt.barber_id] = []
            busy_by_master[apt.barber_id].append({
                'id': str(apt.id),
                'start_at': apt.start_at.isoformat(),
                'end_at': apt.end_at.isoformat()
            })
        
        # Формируем результат
        result = []
        for master in masters_qs:
            result.append({
                'master_id': str(master.id),
                'master_name': f"{master.first_name or ''} {master.last_name or ''}".strip() or master.email,
                'avatar': master.avatar,
                'date': target_date.isoformat(),
                'busy_slots': busy_by_master.get(master.id, []),
                'work_start': '09:00',
                'work_end': '21:00'
            })
        
        return Response({
            'date': target_date.isoformat(),
            'masters': result
        })
