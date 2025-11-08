# barber_crm/views.py
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import SearchFilter, OrderingFilter  # NEW
from django.db.models.deletion import ProtectedError
from django.db.models import Q

from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend

from apps.users.models import Branch
from .models import Service, Client, Appointment, Document, Folder
from .serializers import (
    ServiceSerializer,
    ClientSerializer,
    AppointmentSerializer,
    FolderSerializer,
    DocumentSerializer,
)

# ---- DocumentFilter без изменений ----
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
      - нет филиала у пользователя → только глобальные записи (branch IS NULL)
      - есть филиал у пользователя → (рекомендуется) филиал И глобальные записи
    Создание:
      - company берётся из request.user.company/owned_company
      - branch проставляется автоматически (или None)
    Обновление:
      - company фиксируется
      - branch НЕ перезаписывается автоматически (безопаснее)
    """

    # --- helpers ---
    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def _user_primary_branch(self):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        memberships = getattr(user, "branch_memberships", None)
        if memberships is None:
            return None
        primary = memberships.filter(is_primary=True).select_related("branch").first()
        if primary and primary.branch:
            return primary.branch
        any_member = memberships.select_related("branch").first()
        if any_member and any_member.branch:
            return any_member.branch
        return None

    def _model_has_field(self, field_name: str) -> bool:
        model = getattr(self, "queryset", None).model
        return field_name in {f.name for f in model._meta.get_fields()}

    def _active_branch(self):
        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            return None
        user_branch = self._user_primary_branch()
        if user_branch and user_branch.company_id == company.id:
            setattr(request, "branch", user_branch)
            return user_branch
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

        qs = qs.filter(company=company)

        if self._model_has_field("branch"):
            active_branch = self._active_branch()  # None или Branch
            if active_branch is not None:
                # РЕКОМЕНДУЕМО: филиал + глобальные
                qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
                # Если хотите строго только филиал — замените строкой ниже:
                # qs = qs.filter(branch=active_branch)
            else:
                qs = qs.filter(branch__isnull=True)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не задана компания.")
        if self._model_has_field("branch"):
            active_branch = self._active_branch()
            serializer.save(company=company, branch=active_branch)
        else:
            serializer.save(company=company)

    def perform_update(self, serializer):
        # company фиксируем, branch не трогаем (не переносим запись между филиалами случайно)
        company = self._user_company()
        if not company:
            raise PermissionDenied("У пользователя не задана компания.")
        serializer.save(company=company)


# ==== Service ====
class ServiceListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]  # NEW
    filterset_fields = [
        f.name for f in Service._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["name", "category"]  # NEW
    ordering_fields = ["name", "price", "is_active"]  # NEW
    ordering = ["name"]  # NEW


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
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]  # NEW
    filterset_fields = [
        f.name for f in Client._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["full_name", "phone", "email", "notes"]  # NEW
    ordering_fields = ["full_name", "created_at", "status"]  # NEW
    ordering = ["-created_at"]  # NEW


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
                .select_related("barber")                # FIX
                .prefetch_related("services")            # FIX
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
                service_names = list(a.services.values_list("name", flat=True))  # FIX
                examples.append(
                    {
                        "start_at": a.start_at,
                        "services": service_names,  # FIX
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
        .select_related("client", "barber")      # FIX: убран "service"
        .prefetch_related("services")            # FIX
        .all()
    )
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]  # NEW
    filterset_fields = [
        f.name for f in Appointment._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["client__full_name", "barber__first_name", "barber__last_name", "comment"]  # NEW
    ordering_fields = ["start_at", "end_at", "status", "created_at"]  # NEW
    ordering = ["-start_at"]  # NEW


class AppointmentRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = (
        Appointment.objects
        .select_related("client", "barber")      # FIX
        .prefetch_related("services")            # FIX
        .all()
    )
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Folder ====
class FolderListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related("parent").all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]  # NEW
    filterset_fields = [
        f.name for f in Folder._meta.get_fields() if not f.is_relation or f.many_to_one
    ]
    search_fields = ["name", "parent__name"]  # NEW
    ordering_fields = ["name"]  # NEW
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
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]  # NEW
    filterset_class = DocumentFilter
    search_fields = ["name", "folder__name", "file"]  # NEW
    ordering_fields = ["name", "created_at", "updated_at"]  # NEW
    ordering = ["name"]  # NEW


class DocumentRetrieveUpdateDestroyView(
    CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    queryset = Document.objects.select_related("folder").all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
