from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Count
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import generics, permissions, filters, status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django_filters.rest_framework import DjangoFilterBackend

from apps.construction.models import Cashbox, CashShift, CashFlow

from .models import (
    ResidentialComplex,
    ResidentialComplexMember,
    ResidentialComplexDrawing,
    ResidentialComplexWarehouse,
    ResidentialComplexApartment,
    BuildingProduct,
    BuildingProcurementRequest,
    BuildingProcurementItem,
    BuildingTransferRequest,
    BuildingWorkflowEvent,
    BuildingWarehouseStockItem,
    BuildingWarehouseStockMove,
    BuildingClient,
    BuildingTreaty,
    BuildingTreatyFile,
    BuildingWorkEntry,
    BuildingWorkEntryPhoto,
    BuildingTask,
    BuildingTaskChecklistItem,
    BuildingEmployeeCompensation,
    BuildingPayrollPeriod,
    BuildingPayrollLine,
    BuildingPayrollAdjustment,
    BuildingPayrollPayment,
)
from .serializers import (
    ResidentialComplexSerializer,
    ResidentialComplexCreateSerializer,
    ResidentialComplexMemberSerializer,
    ResidentialComplexMemberCreateSerializer,
    ResidentialComplexDrawingSerializer,
    ResidentialComplexWarehouseSerializer,
    ResidentialComplexApartmentSerializer,
    BuildingProductSerializer,
    BuildingProcurementSerializer,
    BuildingProcurementItemSerializer,
    BuildingTransferSerializer,
    BuildingWorkflowEventSerializer,
    BuildingWarehouseStockItemSerializer,
    BuildingWarehouseStockMoveSerializer,
    BuildingReasonSerializer,
    BuildingTransferCreateSerializer,
    BuildingTransferAcceptSerializer,
    BuildingPurchaseDocumentSerializer,
    BuildingClientSerializer,
    BuildingClientDetailSerializer,
    BuildingTreatySerializer,
    BuildingTreatyFileCreateSerializer,
    BuildingWorkEntrySerializer,
    BuildingWorkEntryPhotoCreateSerializer,
    BuildingTaskSerializer,
    BuildingTaskChecklistItemSerializer,
    BuildingTaskChecklistItemUpdateSerializer,
    BuildingSalaryEmployeeSerializer,
    BuildingEmployeeCompensationSerializer,
    BuildingPayrollPeriodSerializer,
    BuildingPayrollPeriodApproveSerializer,
    BuildingPayrollLineSerializer,
    BuildingPayrollLineCreateSerializer,
    BuildingPayrollAdjustmentSerializer,
    BuildingPayrollAdjustmentCreateSerializer,
    BuildingPayrollPaymentSerializer,
    BuildingPayrollPaymentCreateSerializer,
    BuildingPayrollMyLineSerializer,
)
from . import services

User = get_user_model()


class CompanyQuerysetMixin:
    """Ограничение выборки объектами компании текущего пользователя."""

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return qs
        if user.is_authenticated and getattr(user, "company_id", None):
            field_names = {f.name for f in qs.model._meta.get_fields()}
            if "company" in field_names:
                return qs.filter(company_id=user.company_id)
            return qs
        return qs


def _is_owner_like(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "role", None) in ("owner", "admin"):
        return True
    if getattr(user, "owned_company_id", None):
        return True
    return False


def _can_access_task(user, task: BuildingTask) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or _is_owner_like(user):
        return True
    if task.created_by_id == getattr(user, "id", None):
        return True
    return task.assignees.filter(user_id=getattr(user, "id", None)).exists()


def _require_salary_perm(user):
    if _is_owner_like(user) or getattr(user, "is_superuser", False):
        return
    if not getattr(user, "can_view_building_salary", False):
        raise PermissionDenied("Нет прав на зарплату (Building).")


def _require_cash_register_perm(user):
    if _is_owner_like(user) or getattr(user, "is_superuser", False):
        return
    if not getattr(user, "can_view_building_cash_register", False):
        raise PermissionDenied("Нет прав кассы (Building).")


def _require_building_employees_perm(user):
    if _is_owner_like(user) or getattr(user, "is_superuser", False):
        return
    if not getattr(user, "can_view_building_employess", False) and not getattr(user, "can_view_employees", False):
        raise PermissionDenied("Нет прав на сотрудников (Building).")


def _allowed_residential_complex_ids(user):
    """
    Если у пользователя есть активные назначения на ЖК, то возвращаем список этих ЖК.
    Если назначений нет — возвращаем None (ограничение не применяется).
    owner/admin/superuser — None.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if _is_owner_like(user) or getattr(user, "is_superuser", False):
        return None
    company_id = getattr(user, "company_id", None)
    if not company_id:
        return None
    ids = list(
        ResidentialComplexMember.objects.filter(
            user_id=getattr(user, "id", None),
            is_active=True,
            residential_complex__company_id=company_id,
        ).values_list("residential_complex_id", flat=True)
    )
    return ids or None

class ResidentialComplexListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/building/objects/  — список ЖК компании.
    POST /api/building/objects/  — создание ЖК (company из user).
    """
    permission_classes = [permissions.IsAuthenticated]
    queryset = ResidentialComplex.objects.all()

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ResidentialComplexCreateSerializer
        return ResidentialComplexSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids:
            qs = qs.filter(id__in=allowed_ids)
        return qs


class ResidentialComplexDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/building/objects/<uuid>/
    PATCH  /api/building/objects/<uuid>/
    PUT    /api/building/objects/<uuid>/
    DELETE /api/building/objects/<uuid>/
    """
    permission_classes = [permissions.IsAuthenticated]
    queryset = ResidentialComplex.objects.all()
    serializer_class = ResidentialComplexSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids:
            qs = qs.filter(id__in=allowed_ids)
        return qs


class ResidentialComplexMembersView(CompanyQuerysetMixin, generics.GenericAPIView):
    """
    Назначения сотрудников на ЖК:
    - GET  /api/building/objects/<uuid:pk>/members/
    - POST /api/building/objects/<uuid:pk>/members/  {user, is_active?}
    - DELETE /api/building/objects/<uuid:pk>/members/<uuid:user_id>/
    """

    permission_classes = [permissions.IsAuthenticated]
    queryset = ResidentialComplex.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        # назначениями управляют owner/admin/superuser или с правом employees
        if self.request.method in ("POST", "DELETE"):
            _require_building_employees_perm(user)
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids:
            qs = qs.filter(id__in=allowed_ids)
        return qs

    def get(self, request, pk=None):
        rc = self.get_object()
        _require_building_employees_perm(request.user)
        memberships = (
            ResidentialComplexMember.objects
            .select_related("user", "added_by", "residential_complex")
            .filter(residential_complex=rc)
            .order_by("-created_at")
        )
        return Response(ResidentialComplexMemberSerializer(memberships, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, pk=None):
        rc = self.get_object()
        _require_building_employees_perm(request.user)
        ser = ResidentialComplexMemberCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        company_id = getattr(request.user, "company_id", None)
        user_obj = User.objects.filter(id=ser.validated_data["user"], company_id=company_id).first()
        if not user_obj:
            raise ValidationError({"user": "Сотрудник не найден (или другой компании)."})

        obj, _ = ResidentialComplexMember.objects.update_or_create(
            residential_complex=rc,
            user=user_obj,
            defaults={
                "is_active": bool(ser.validated_data.get("is_active", True)),
                "added_by": request.user,
            },
        )
        return Response(ResidentialComplexMemberSerializer(obj, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def delete(self, request, pk=None, user_id=None):
        rc = self.get_object()
        _require_building_employees_perm(request.user)
        ResidentialComplexMember.objects.filter(residential_complex=rc, user_id=user_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ResidentialComplexDrawingListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/building/drawings/  — список чертежей ЖК компании.
    POST /api/building/drawings/  — создание чертежа и привязка к ЖК.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexDrawingSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = ResidentialComplexDrawing.objects.select_related("residential_complex")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["residential_complex", "is_active"]
    search_fields = ["title", "description", "residential_complex__name"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        rc = serializer.validated_data["residential_complex"]
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids and rc.id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")
        serializer.save()


class ResidentialComplexDrawingDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/building/drawings/<uuid>/
    PATCH  /api/building/drawings/<uuid>/
    PUT    /api/building/drawings/<uuid>/
    DELETE /api/building/drawings/<uuid>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexDrawingSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = ResidentialComplexDrawing.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class ResidentialComplexWarehouseListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexWarehouseSerializer
    queryset = ResidentialComplexWarehouse.objects.select_related("residential_complex")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["residential_complex", "is_active"]
    search_fields = ["name", "residential_complex__name"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        residential_complex = serializer.validated_data["residential_complex"]
        user = self.request.user
        if not getattr(user, "is_superuser", False) and residential_complex.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids and residential_complex.id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")
        serializer.save()


class ResidentialComplexWarehouseDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexWarehouseSerializer
    queryset = ResidentialComplexWarehouse.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class ResidentialComplexApartmentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    Квартиры ЖК:
    GET  /api/building/apartments/
    POST /api/building/apartments/
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexApartmentSerializer
    queryset = ResidentialComplexApartment.objects.select_related("residential_complex")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["residential_complex", "floor", "status"]
    search_fields = ["number", "notes"]

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на продажи/договора (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на продажи/договора (Building).")
        rc = serializer.validated_data["residential_complex"]
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids and rc.id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")
        serializer.save()


class ResidentialComplexApartmentDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /api/building/apartments/<uuid>/
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexApartmentSerializer
    queryset = ResidentialComplexApartment.objects.select_related("residential_complex")

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на продажи/договора (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class ResidentialComplexFloorsView(CompanyQuerysetMixin, generics.GenericAPIView):
    """
    Этажи по ЖК (для шага выбора этаж → квартира):
    GET /api/building/objects/<uuid:pk>/floors/
    """

    permission_classes = [permissions.IsAuthenticated]
    queryset = ResidentialComplex.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def get(self, request, pk=None):
        user = request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на продажи/договора (Building).")

        rc = self.get_object()
        qs = (
            ResidentialComplexApartment.objects.filter(residential_complex=rc)
            .values("floor")
            .annotate(
                total=Count("id"),
                available=Count("id", filter=Q(status=ResidentialComplexApartment.Status.AVAILABLE)),
                reserved=Count("id", filter=Q(status=ResidentialComplexApartment.Status.RESERVED)),
                sold=Count("id", filter=Q(status=ResidentialComplexApartment.Status.SOLD)),
            )
            .order_by("floor")
        )
        return Response(list(qs), status=status.HTTP_200_OK)

class BuildingProductListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProductSerializer
    queryset = BuildingProduct.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active"]
    search_fields = ["name", "article", "barcode", "unit"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            return qs.filter(company_id=user.company_id)
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        company_id = getattr(user, "company_id", None)
        if not company_id and not getattr(user, "is_superuser", False):
            raise PermissionDenied("У пользователя не указана компания.")
        serializer.save(company_id=company_id)


class BuildingProductDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProductSerializer
    queryset = BuildingProduct.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            return qs.filter(company_id=user.company_id)
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingProcurementListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex", "initiator").prefetch_related("items")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["residential_complex", "status"]
    search_fields = ["title", "comment", "residential_complex__name"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        rc = serializer.validated_data["residential_complex"]
        user = self.request.user
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids and rc.id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")
        serializer.save(initiator=user)
        services.log_event(action="procurement_created", actor=user, procurement=serializer.instance)


class BuildingProcurementDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex", "initiator").prefetch_related("items")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingProcurementItemListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementItemSerializer
    queryset = BuildingProcurementItem.objects.select_related("procurement", "procurement__residential_complex")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["procurement"]
    search_fields = ["name", "unit", "note"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(procurement__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(procurement__residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        procurement = serializer.validated_data["procurement"]
        user = self.request.user
        if procurement.status != BuildingProcurementRequest.Status.DRAFT:
            raise PermissionDenied("Позиции можно добавлять только в черновик.")
        if not getattr(user, "is_superuser", False) and procurement.residential_complex.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("Закупка другой компании.")
        serializer.save()
        item = serializer.instance
        services.log_event(
            action="procurement_item_created",
            actor=user,
            procurement=procurement,
            procurement_item=item,
            payload={
                "name": item.name,
                "unit": item.unit,
                "quantity": str(item.quantity),
                "price": str(item.price),
                "line_total": str(item.line_total),
            },
        )


class BuildingProcurementItemDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementItemSerializer
    queryset = BuildingProcurementItem.objects.select_related("procurement", "procurement__residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(procurement__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(procurement__residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_update(self, serializer):
        item = self.get_object()
        if item.procurement.status != BuildingProcurementRequest.Status.DRAFT:
            raise PermissionDenied("Позиции можно менять только в черновике.")
        serializer.save()
        item.refresh_from_db()
        services.log_event(
            action="procurement_item_updated",
            actor=self.request.user,
            procurement=item.procurement,
            procurement_item=item,
            payload={
                "name": item.name,
                "unit": item.unit,
                "quantity": str(item.quantity),
                "price": str(item.price),
                "line_total": str(item.line_total),
            },
        )

    def perform_destroy(self, instance):
        if instance.procurement.status != BuildingProcurementRequest.Status.DRAFT:
            raise PermissionDenied("Позиции можно удалять только из черновика.")
        services.log_event(
            action="procurement_item_deleted",
            actor=self.request.user,
            procurement=instance.procurement,
            procurement_item=instance,
            payload={"name": instance.name, "unit": instance.unit, "quantity": str(instance.quantity)},
        )
        instance.delete()


class BuildingProcurementSubmitToCashView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        procurement = self.get_object()
        services.submit_procurement_to_cash(procurement, request.user)
        procurement.refresh_from_db()
        return Response(self.get_serializer(procurement).data, status=status.HTTP_200_OK)


class BuildingCashPendingProcurementListView(CompanyQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex", "initiator").prefetch_related("items")

    def get_queryset(self):
        qs = super().get_queryset().filter(status=BuildingProcurementRequest.Status.SUBMITTED_TO_CASH)
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingCashApproveProcurementView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        procurement = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        services.approve_procurement_cash(procurement, request.user, reason=reason)
        procurement.refresh_from_db()
        return Response(self.get_serializer(procurement).data, status=status.HTTP_200_OK)


class BuildingCashRejectProcurementView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingProcurementSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        procurement = self.get_object()
        ser = BuildingReasonSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.reject_procurement_cash(procurement, request.user, reason=ser.validated_data["reason"])
        procurement.refresh_from_db()
        return Response(self.get_serializer(procurement).data, status=status.HTTP_200_OK)


class BuildingTransferCreateView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTransferSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        procurement = self.get_object()
        ser = BuildingTransferCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        transfer = services.create_transfer_from_procurement(procurement, request.user, note=ser.validated_data.get("note", ""))
        return Response(BuildingTransferSerializer(transfer, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BuildingTransferListView(CompanyQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTransferSerializer
    queryset = BuildingTransferRequest.objects.select_related(
        "procurement",
        "procurement__residential_complex",
        "warehouse",
        "created_by",
        "decided_by",
    ).prefetch_related("items")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["status", "warehouse", "procurement"]
    search_fields = ["note", "warehouse__name", "procurement__title", "procurement__residential_complex__name"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(warehouse__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(warehouse__residential_complex_id__in=allowed_ids)
        elif not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        incoming_only = self.request.query_params.get("incoming")
        if incoming_only in ("1", "true", "True"):
            qs = qs.filter(status=BuildingTransferRequest.Status.PENDING_RECEIPT)
        return qs


class BuildingTransferDetailView(CompanyQuerysetMixin, generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTransferSerializer
    queryset = BuildingTransferRequest.objects.select_related(
        "procurement",
        "procurement__residential_complex",
        "warehouse",
        "created_by",
        "decided_by",
    ).prefetch_related("items")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(warehouse__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(warehouse__residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingTransferAcceptView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTransferSerializer
    queryset = BuildingTransferRequest.objects.select_related("warehouse", "warehouse__residential_complex", "procurement")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(warehouse__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(warehouse__residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        transfer = self.get_object()
        ser = BuildingTransferAcceptSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.accept_transfer(transfer, request.user, note=ser.validated_data.get("note", ""))
        transfer.refresh_from_db()
        return Response(self.get_serializer(transfer).data, status=status.HTTP_200_OK)


class BuildingTransferRejectView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTransferSerializer
    queryset = BuildingTransferRequest.objects.select_related("warehouse", "warehouse__residential_complex", "procurement")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(warehouse__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(warehouse__residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        transfer = self.get_object()
        ser = BuildingReasonSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.reject_transfer(transfer, request.user, reason=ser.validated_data["reason"])
        transfer.refresh_from_db()
        return Response(self.get_serializer(transfer).data, status=status.HTTP_200_OK)


class BuildingWorkflowEventListView(CompanyQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingWorkflowEventSerializer
    queryset = BuildingWorkflowEvent.objects.select_related(
        "procurement",
        "transfer",
        "warehouse",
        "stock_item",
        "actor",
        "procurement_item",
        "transfer_item",
    )
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["procurement", "procurement_item", "transfer", "transfer_item", "warehouse", "stock_item", "action"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(
                Q(procurement__residential_complex__company_id=user.company_id)
                | Q(transfer__warehouse__residential_complex__company_id=user.company_id)
            )
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(
                    Q(procurement__residential_complex_id__in=allowed_ids)
                    | Q(transfer__warehouse__residential_complex_id__in=allowed_ids)
                )
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingWarehouseStockItemListView(CompanyQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingWarehouseStockItemSerializer
    queryset = BuildingWarehouseStockItem.objects.select_related("warehouse", "warehouse__residential_complex")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["warehouse"]
    search_fields = ["name", "unit", "warehouse__name", "warehouse__residential_complex__name"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(warehouse__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(warehouse__residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingWarehouseStockMoveListView(CompanyQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingWarehouseStockMoveSerializer
    queryset = BuildingWarehouseStockMove.objects.select_related(
        "warehouse",
        "warehouse__residential_complex",
        "stock_item",
        "transfer",
        "created_by",
    )
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["warehouse", "stock_item", "transfer", "move_type"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(warehouse__residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(warehouse__residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingPurchaseDocumentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    Совместимый с warehouse формат закупок:
    GET/POST /api/building/documents/purchase/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPurchaseDocumentSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex", "initiator").prefetch_related("items")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["status", "residential_complex"]
    search_fields = ["comment", "title", "residential_complex__name"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        rc = serializer.validated_data["residential_complex"]
        user = self.request.user
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids and rc.id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")
        doc = serializer.save()
        services.log_event(action="procurement_created", actor=user, procurement=doc)


class BuildingPurchaseDocumentDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    Совместимый с warehouse формат закупки:
    GET/PATCH/PUT/DELETE /api/building/documents/purchase/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPurchaseDocumentSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex", "initiator").prefetch_related("items")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingPurchaseDocumentCashApproveView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPurchaseDocumentSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        doc = self.get_object()
        reason = (request.data.get("note") or request.data.get("reason") or "").strip()
        if doc.status == BuildingProcurementRequest.Status.DRAFT:
            services.submit_procurement_to_cash(doc, request.user)
            doc.refresh_from_db()
        services.approve_procurement_cash(doc, request.user, reason=reason)
        doc.refresh_from_db()
        return Response(self.get_serializer(doc).data, status=status.HTTP_200_OK)


class BuildingPurchaseDocumentCashRejectView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPurchaseDocumentSerializer
    queryset = BuildingProcurementRequest.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        doc = self.get_object()
        reason = (request.data.get("note") or request.data.get("reason") or "").strip()
        if not reason:
            return Response({"reason": ["Укажите причину отказа."]}, status=status.HTTP_400_BAD_REQUEST)
        if doc.status == BuildingProcurementRequest.Status.DRAFT:
            services.submit_procurement_to_cash(doc, request.user)
            doc.refresh_from_db()
        services.reject_procurement_cash(doc, request.user, reason=reason)
        doc.refresh_from_db()
        return Response(self.get_serializer(doc).data, status=status.HTTP_200_OK)


class BuildingWorkEntryListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingWorkEntrySerializer
    queryset = BuildingWorkEntry.objects.select_related(
        "residential_complex",
        "client",
        "treaty",
        "created_by",
    ).prefetch_related("photos")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["residential_complex", "category", "created_by", "client", "treaty"]
    search_fields = ["title", "description", "residential_complex__name", "client__name", "treaty__number"]

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_work_process", False)):
            raise PermissionDenied("Нет прав на процесс работы (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_work_process", False)):
            raise PermissionDenied("Нет прав на процесс работы (Building).")

        rc = serializer.validated_data["residential_complex"]
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids and rc.id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")

        client = serializer.validated_data.get("client")
        if client and client.company_id != rc.company_id:
            raise PermissionDenied("Клиент принадлежит другой компании.")

        treaty = serializer.validated_data.get("treaty")
        if treaty and treaty.residential_complex.company_id != rc.company_id:
            raise PermissionDenied("Договор принадлежит другой компании.")

        serializer.save(created_by=user)


class BuildingWorkEntryDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingWorkEntrySerializer
    queryset = BuildingWorkEntry.objects.select_related(
        "residential_complex",
        "client",
        "treaty",
        "created_by",
    ).prefetch_related("photos")

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_work_process", False)):
            raise PermissionDenied("Нет прав на процесс работы (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_update(self, serializer):
        user = self.request.user
        obj = self.get_object()
        if not (_is_owner_like(user) or obj.created_by_id == getattr(user, "id", None)):
            raise PermissionDenied("Изменять запись может только автор или владелец.")

        rc = serializer.validated_data.get("residential_complex") or obj.residential_complex
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")

        client = serializer.validated_data.get("client")
        if client and client.company_id != rc.company_id:
            raise PermissionDenied("Клиент принадлежит другой компании.")

        treaty = serializer.validated_data.get("treaty")
        if treaty and treaty.residential_complex.company_id != rc.company_id:
            raise PermissionDenied("Договор принадлежит другой компании.")

        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not (_is_owner_like(user) or instance.created_by_id == getattr(user, "id", None)):
            raise PermissionDenied("Удалять запись может только автор или владелец.")
        instance.delete()


class BuildingWorkEntryPhotoAddView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingWorkEntryPhotoCreateSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = BuildingWorkEntry.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        user = request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_work_process", False)):
            raise PermissionDenied("Нет прав на процесс работы (Building).")

        entry = self.get_object()
        if not getattr(user, "is_superuser", False) and entry.residential_complex.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)

        BuildingWorkEntryPhoto.objects.create(
            entry=entry,
            image=ser.validated_data["image"],
            caption=ser.validated_data.get("caption", "") or "",
            created_by=user,
        )
        return Response(BuildingWorkEntrySerializer(entry, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BuildingClientListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingClientSerializer
    queryset = BuildingClient.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active"]
    search_fields = ["name", "phone", "email", "inn"]

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_clients", False)):
            raise PermissionDenied("Нет прав на клиентов (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(treaties__residential_complex_id__in=allowed_ids).distinct()
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_clients", False)):
            raise PermissionDenied("Нет прав на клиентов (Building).")
        company_id = getattr(user, "company_id", None)
        if not company_id and not getattr(user, "is_superuser", False):
            raise PermissionDenied("У пользователя не указана компания.")
        serializer.save(company_id=company_id)


class BuildingClientDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingClientDetailSerializer
    queryset = BuildingClient.objects.all()

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_clients", False)):
            raise PermissionDenied("Нет прав на клиентов (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(treaties__residential_complex_id__in=allowed_ids).distinct()
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class BuildingTreatyListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTreatySerializer
    queryset = BuildingTreaty.objects.select_related("residential_complex", "client", "created_by", "apartment").prefetch_related("files", "installments")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["residential_complex", "client", "status", "erp_sync_status", "auto_create_in_erp", "operation_type", "payment_type", "apartment"]
    search_fields = ["number", "title", "description", "residential_complex__name", "client__name", "apartment__number"]

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на договора (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на договора (Building).")

        rc = serializer.validated_data["residential_complex"]
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids and rc.id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")

        client = serializer.validated_data.get("client")
        if client and client.company_id != rc.company_id:
            raise PermissionDenied("Клиент принадлежит другой компании.")

        treaty = serializer.save(created_by=user)

        # Автоматически фиксируем событие в процессе работ
        try:
            if getattr(user, "can_view_building_work_process", False) or _is_owner_like(user):
                BuildingWorkEntry.objects.create(
                    residential_complex=rc,
                    client=client,
                    treaty=treaty,
                    created_by=user,
                    category=BuildingWorkEntry.Category.TREATY,
                    title=(treaty.title or treaty.number or "Договор"),
                    description=treaty.description or "",
                    occurred_at=getattr(treaty, "created_at", None) or None,
                )
        except Exception:
            # Не блокируем создание договора из-за ошибок вторичной записи в work process
            pass

        # Автосоздание в ERP (если включено и настроено)
        if getattr(treaty, "auto_create_in_erp", False):
            try:
                services.request_treaty_create_in_erp(treaty, user)
            except Exception:
                pass


class BuildingTreatyDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTreatySerializer
    queryset = BuildingTreaty.objects.select_related("residential_complex", "client", "created_by", "apartment").prefetch_related("files", "installments")

    def get_queryset(self):
        user = self.request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на договора (Building).")
        qs = super().get_queryset()
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_update(self, serializer):
        user = self.request.user
        obj = self.get_object()
        if not (_is_owner_like(user) or obj.created_by_id == getattr(user, "id", None)):
            raise PermissionDenied("Изменять договор может только автор или владелец.")

        rc = serializer.validated_data.get("residential_complex") or obj.residential_complex
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")

        client = serializer.validated_data.get("client")
        if client and client.company_id != rc.company_id:
            raise PermissionDenied("Клиент принадлежит другой компании.")

        with transaction.atomic():
            old_status = obj.status
            old_apartment_id = obj.apartment_id
            old_operation = obj.operation_type

            serializer.save()
            obj.refresh_from_db()

            # синхронизируем статус квартиры с договором (если привязана)
            if old_apartment_id:
                apt = ResidentialComplexApartment.objects.select_for_update().filter(id=old_apartment_id).first()
                if apt:
                    if obj.status == BuildingTreaty.Status.CANCELLED:
                        has_other = (
                            BuildingTreaty.objects
                            .filter(apartment_id=apt.id)
                            .exclude(id=obj.id)
                            .exclude(status=BuildingTreaty.Status.CANCELLED)
                            .exists()
                        )
                        if not has_other:
                            apt.status = ResidentialComplexApartment.Status.AVAILABLE
                            apt.save(update_fields=["status", "updated_at"])
                    else:
                        desired = (
                            ResidentialComplexApartment.Status.SOLD
                            if obj.operation_type == BuildingTreaty.OperationType.SALE
                            else ResidentialComplexApartment.Status.RESERVED
                        )
                        if apt.status != desired and (obj.operation_type != old_operation or obj.status != old_status):
                            apt.status = desired
                            apt.save(update_fields=["status", "updated_at"])

    def perform_destroy(self, instance):
        user = self.request.user
        if not (_is_owner_like(user) or instance.created_by_id == getattr(user, "id", None)):
            raise PermissionDenied("Удалять договор может только автор или владелец.")
        with transaction.atomic():
            apt_id = instance.apartment_id
            instance.delete()
            if apt_id:
                apt = ResidentialComplexApartment.objects.select_for_update().filter(id=apt_id).first()
                if apt:
                    has_other = (
                        BuildingTreaty.objects
                        .filter(apartment_id=apt.id)
                        .exclude(status=BuildingTreaty.Status.CANCELLED)
                        .exists()
                    )
                    if not has_other:
                        apt.status = ResidentialComplexApartment.Status.AVAILABLE
                        apt.save(update_fields=["status", "updated_at"])


class BuildingTreatyFileAddView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTreatyFileCreateSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = BuildingTreaty.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        user = request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на договора (Building).")

        treaty = self.get_object()
        if not getattr(user, "is_superuser", False) and treaty.residential_complex.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("Договор другой компании.")

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)

        BuildingTreatyFile.objects.create(
            treaty=treaty,
            file=ser.validated_data["file"],
            title=(ser.validated_data.get("title") or "").strip(),
            created_by=user,
        )
        treaty.refresh_from_db()
        return Response(BuildingTreatySerializer(treaty, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BuildingTreatyErpCreateView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTreatySerializer
    queryset = BuildingTreaty.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            qs = qs.filter(residential_complex__company_id=user.company_id)
            allowed_ids = _allowed_residential_complex_ids(user)
            if allowed_ids:
                qs = qs.filter(residential_complex_id__in=allowed_ids)
            return qs
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def post(self, request, pk=None):
        user = request.user
        if not (_is_owner_like(user) or getattr(user, "can_view_building_treaty", False)):
            raise PermissionDenied("Нет прав на договора (Building).")

        treaty = self.get_object()
        services.request_treaty_create_in_erp(treaty, user)
        treaty.refresh_from_db()
        return Response(BuildingTreatySerializer(treaty, context={"request": request}).data, status=status.HTTP_200_OK)


class BuildingTaskListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    Задачи/напоминания:
    - видит автор и отмеченные сотрудники
    - owner/admin/superuser видит все задачи компании
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTaskSerializer
    queryset = BuildingTask.objects.select_related("company", "created_by", "residential_complex", "client", "treaty").prefetch_related(
        "assignees__user",
        "checklist_items",
    )
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["status", "residential_complex", "client", "treaty", "created_by"]
    search_fields = ["title", "description"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user

        if getattr(user, "is_superuser", False):
            return qs

        company_id = getattr(user, "company_id", None)
        if not company_id:
            return qs.none()

        qs = qs.filter(company_id=company_id)

        allowed_ids = _allowed_residential_complex_ids(user)
        if allowed_ids:
            # если задача привязана к ЖК — показываем только назначенные ЖК;
            # задачи без ЖК оставляем видимыми (личные/общие)
            qs = qs.filter(Q(residential_complex_id__in=allowed_ids) | Q(residential_complex__isnull=True))

        if _is_owner_like(user):
            return qs

        # обычный пользователь: только свои и где он отмечен
        return qs.filter(Q(created_by_id=user.id) | Q(assignees__user_id=user.id)).distinct()

    def perform_create(self, serializer):
        user = self.request.user
        if not getattr(user, "company_id", None) and not getattr(user, "is_superuser", False):
            raise PermissionDenied("У пользователя не указана компания.")
        serializer.save()


class BuildingTaskDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTaskSerializer
    queryset = BuildingTask.objects.select_related("company", "created_by", "residential_complex", "client", "treaty").prefetch_related(
        "assignees__user",
        "checklist_items",
    )

    def get_queryset(self):
        # фильтрацию доступа делаем на уровне get_object через _can_access_task,
        # но базово ограничим компанией
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(company_id=company_id) if company_id else qs.none()

    def get_object(self):
        obj = super().get_object()
        if not _can_access_task(self.request.user, obj):
            raise PermissionDenied("Нет доступа к задаче.")
        allowed_ids = _allowed_residential_complex_ids(self.request.user)
        if allowed_ids and obj.residential_complex_id and obj.residential_complex_id not in set(allowed_ids):
            raise PermissionDenied("Нет доступа к этому ЖК.")
        return obj

    def perform_update(self, serializer):
        obj = self.get_object()
        user = self.request.user
        if not (_is_owner_like(user) or obj.created_by_id == getattr(user, "id", None)):
            raise PermissionDenied("Изменять задачу может только автор или владелец.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not (_is_owner_like(user) or instance.created_by_id == getattr(user, "id", None)):
            raise PermissionDenied("Удалять задачу может только автор или владелец.")
        instance.delete()


class BuildingTaskChecklistItemAddView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTaskChecklistItemUpdateSerializer
    queryset = BuildingTask.objects.select_related("company").prefetch_related("assignees")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(company_id=company_id) if company_id else qs.none()

    def post(self, request, pk=None):
        task = self.get_object()
        if not _can_access_task(request.user, task):
            raise PermissionDenied("Нет доступа к задаче.")

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        item = BuildingTaskChecklistItem.objects.create(
            task=task,
            text=ser.validated_data["text"],
            is_done=bool(ser.validated_data.get("is_done", False)),
            order=int(ser.validated_data.get("order") or 0),
        )
        if item.is_done:
            item.done_by = request.user
            item.done_at = timezone.now()
            item.save(update_fields=["done_by", "done_at", "updated_at"])

        task.refresh_from_db()
        return Response(BuildingTaskSerializer(task, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BuildingTaskChecklistItemDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingTaskChecklistItemSerializer
    queryset = BuildingTaskChecklistItem.objects.select_related("task", "task__company", "done_by")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(task__company_id=company_id) if company_id else qs.none()

    def get_object(self):
        obj = super().get_object()
        if not _can_access_task(self.request.user, obj.task):
            raise PermissionDenied("Нет доступа к задаче.")
        return obj

    def perform_update(self, serializer):
        obj = self.get_object()
        user = self.request.user
        task = obj.task

        if not (_is_owner_like(user) or task.created_by_id == getattr(user, "id", None) or task.assignees.filter(user_id=user.id).exists()):
            raise PermissionDenied("Нет прав менять чек-лист.")

        is_done_before = obj.is_done
        serializer.save()
        obj.refresh_from_db()

        if "is_done" in serializer.validated_data:
            if obj.is_done and not is_done_before:
                obj.done_by = user
                obj.done_at = timezone.now()
                obj.save(update_fields=["done_by", "done_at", "updated_at"])
            if (not obj.is_done) and is_done_before:
                obj.done_by = None
                obj.done_at = None
                obj.save(update_fields=["done_by", "done_at", "updated_at"])


class BuildingSalaryEmployeeListView(generics.ListAPIView):
    """
    Список сотрудников компании для начисления ЗП (с настройками оклада/ставки).
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingSalaryEmployeeSerializer
    pagination_class = None

    def get(self, request, *args, **kwargs):
        _require_salary_perm(request.user)
        company_id = getattr(request.user, "company_id", None)
        if not company_id and not getattr(request.user, "is_superuser", False):
            raise PermissionDenied("У пользователя не указана компания.")

        users = User.objects.filter(company_id=company_id).only("id", "first_name", "last_name", "email", "username").order_by("last_name", "first_name")
        comps = {
            str(c.user_id): c
            for c in BuildingEmployeeCompensation.objects.filter(company_id=company_id).only("id", "user_id", "salary_type", "base_salary", "is_active")
        }

        rows = []
        for u in users:
            full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
            display = full_name or getattr(u, "email", None) or getattr(u, "username", None) or str(u.id)
            c = comps.get(str(u.id))
            rows.append(
                {
                    "id": u.id,
                    "display": display,
                    "compensation_id": getattr(c, "id", None),
                    "salary_type": getattr(c, "salary_type", None),
                    "base_salary": getattr(c, "base_salary", None),
                    "is_active": getattr(c, "is_active", None),
                }
            )

        return Response(self.get_serializer(rows, many=True).data, status=status.HTTP_200_OK)


class BuildingEmployeeCompensationUpsertView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingEmployeeCompensationSerializer

    def patch(self, request, user_id):
        _require_salary_perm(request.user)
        company_id = getattr(request.user, "company_id", None)
        if not company_id and not getattr(request.user, "is_superuser", False):
            raise PermissionDenied("У пользователя не указана компания.")

        employee = User.objects.filter(id=user_id, company_id=company_id).first()
        if not employee:
            raise ValidationError({"user": "Сотрудник не найден (или другой компании)."})

        obj, _ = BuildingEmployeeCompensation.objects.get_or_create(company_id=company_id, user_id=employee.id)
        ser = self.get_serializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(self.get_serializer(obj).data, status=status.HTTP_200_OK)


class BuildingPayrollPeriodListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollPeriodSerializer
    queryset = BuildingPayrollPeriod.objects.select_related("company", "created_by", "approved_by").prefetch_related(
        "lines__employee",
        "lines__adjustments",
        "lines__payments",
    )
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["status", "period_start", "period_end"]
    search_fields = ["title"]

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(company_id=company_id) if company_id else qs.none()

    def perform_create(self, serializer):
        _require_salary_perm(self.request.user)
        company_id = getattr(self.request.user, "company_id", None)
        if not company_id and not getattr(self.request.user, "is_superuser", False):
            raise PermissionDenied("У пользователя не указана компания.")

        ps = serializer.validated_data["period_start"]
        pe = serializer.validated_data["period_end"]
        if ps > pe:
            raise ValidationError({"period_end": "period_end должен быть >= period_start"})

        serializer.save(company_id=company_id, created_by=self.request.user)


class BuildingPayrollPeriodDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollPeriodSerializer
    queryset = BuildingPayrollPeriod.objects.select_related("company", "created_by", "approved_by").prefetch_related(
        "lines__employee",
        "lines__adjustments",
        "lines__payments",
    )

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(company_id=company_id) if company_id else qs.none()

    def perform_update(self, serializer):
        _require_salary_perm(self.request.user)
        obj = self.get_object()
        if obj.status != BuildingPayrollPeriod.Status.DRAFT:
            raise PermissionDenied("Редактировать можно только период в draft.")
        ps = serializer.validated_data.get("period_start") or obj.period_start
        pe = serializer.validated_data.get("period_end") or obj.period_end
        if ps > pe:
            raise ValidationError({"period_end": "period_end должен быть >= period_start"})
        serializer.save()

    def perform_destroy(self, instance):
        _require_salary_perm(self.request.user)
        if instance.status != BuildingPayrollPeriod.Status.DRAFT:
            raise PermissionDenied("Удалять можно только период в draft.")
        instance.delete()


class BuildingPayrollPeriodApproveView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollPeriodApproveSerializer
    queryset = BuildingPayrollPeriod.objects.all()

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(company_id=company_id) if company_id else qs.none()

    @transaction.atomic
    def post(self, request, pk=None):
        payroll = self.get_object()
        _require_salary_perm(request.user)
        if payroll.status != BuildingPayrollPeriod.Status.DRAFT:
            raise ValidationError({"status": "Начислить можно только период в draft."})

        payroll.status = BuildingPayrollPeriod.Status.APPROVED
        payroll.approved_by = request.user
        payroll.approved_at = timezone.now()
        payroll.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])

        for line in payroll.lines.all():
            line.recalculate_totals()
            line.recalculate_paid_total()

        payroll.refresh_from_db()
        return Response(BuildingPayrollPeriodSerializer(payroll, context={"request": request}).data, status=status.HTTP_200_OK)


class BuildingPayrollLineListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollLineSerializer
    queryset = BuildingPayrollLine.objects.select_related("payroll", "payroll__company", "employee").prefetch_related("adjustments", "payments")

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(payroll__company_id=company_id) if company_id else qs.none()

    @transaction.atomic
    def post(self, request, payroll_id=None):
        _require_salary_perm(request.user)
        payroll = BuildingPayrollPeriod.objects.select_for_update().filter(id=payroll_id).first()
        if not payroll:
            raise ValidationError({"payroll": "Период не найден."})
        if not getattr(request.user, "is_superuser", False) and payroll.company_id != getattr(request.user, "company_id", None):
            raise PermissionDenied("Период другой компании.")
        if payroll.status != BuildingPayrollPeriod.Status.DRAFT:
            raise PermissionDenied("Добавлять строки можно только в draft.")

        ser = BuildingPayrollLineCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        employee = User.objects.filter(id=ser.validated_data["employee"], company_id=payroll.company_id).first()
        if not employee:
            raise ValidationError({"employee": "Сотрудник не найден (или другой компании)."})

        base_amount = ser.validated_data.get("base_amount", None)
        if base_amount is None:
            comp = BuildingEmployeeCompensation.objects.filter(company_id=payroll.company_id, user_id=employee.id, is_active=True).first()
            base_amount = getattr(comp, "base_salary", None) or Decimal("0.00")

        line = BuildingPayrollLine.objects.create(
            payroll=payroll,
            employee=employee,
            base_amount=base_amount,
            comment=ser.validated_data.get("comment", "") or "",
        )
        line.recalculate_totals()
        line.recalculate_paid_total()

        return Response(BuildingPayrollLineSerializer(line, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BuildingPayrollLineDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollLineSerializer
    queryset = BuildingPayrollLine.objects.select_related("payroll", "payroll__company", "employee").prefetch_related("adjustments", "payments")

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(payroll__company_id=company_id) if company_id else qs.none()

    def perform_update(self, serializer):
        _require_salary_perm(self.request.user)
        obj = self.get_object()
        if obj.payroll.status != BuildingPayrollPeriod.Status.DRAFT:
            raise PermissionDenied("Редактировать строку можно только в draft периоде.")
        serializer.save()
        obj.refresh_from_db()
        obj.recalculate_totals()

    def perform_destroy(self, instance):
        _require_salary_perm(self.request.user)
        if instance.payroll.status != BuildingPayrollPeriod.Status.DRAFT:
            raise PermissionDenied("Удалять строку можно только в draft периоде.")
        instance.delete()


class BuildingPayrollAdjustmentCreateView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollAdjustmentCreateSerializer
    queryset = BuildingPayrollLine.objects.select_related("payroll", "payroll__company").prefetch_related("payments", "adjustments")

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(payroll__company_id=company_id) if company_id else qs.none()

    @transaction.atomic
    def post(self, request, pk=None):
        _require_salary_perm(request.user)
        line = self.get_object()
        if line.payroll.status == BuildingPayrollPeriod.Status.PAID:
            raise PermissionDenied("Нельзя менять начисления в периоде paid.")

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)

        adj = BuildingPayrollAdjustment.objects.create(
            line=line,
            type=ser.validated_data["type"],
            title=(ser.validated_data.get("title") or "").strip(),
            amount=ser.validated_data["amount"],
            created_by=request.user,
        )
        line.refresh_from_db()
        line.recalculate_totals()
        line.recalculate_paid_total()

        if line.net_to_pay < line.paid_total:
            raise ValidationError({"amount": "Корректировка приводит к переплате (paid_total > net_to_pay)."})

        return Response(BuildingPayrollAdjustmentSerializer(adj, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BuildingPayrollAdjustmentDetailView(CompanyQuerysetMixin, generics.RetrieveDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollAdjustmentSerializer
    queryset = BuildingPayrollAdjustment.objects.select_related("line", "line__payroll", "line__payroll__company", "created_by")

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(line__payroll__company_id=company_id) if company_id else qs.none()

    @transaction.atomic
    def perform_destroy(self, instance):
        _require_salary_perm(self.request.user)
        if instance.line.payroll.status == BuildingPayrollPeriod.Status.PAID:
            raise PermissionDenied("Нельзя менять начисления в периоде paid.")
        line = instance.line
        instance.delete()
        line.refresh_from_db()
        line.recalculate_totals()
        line.recalculate_paid_total()
        if line.net_to_pay < line.paid_total:
            raise ValidationError({"detail": "Удаление корректировки приводит к переплате (paid_total > net_to_pay)."})


class BuildingPayrollPaymentListCreateView(CompanyQuerysetMixin, generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollPaymentCreateSerializer
    queryset = BuildingPayrollLine.objects.select_related("payroll", "payroll__company", "employee").prefetch_related("payments")

    def get_queryset(self):
        _require_salary_perm(self.request.user)
        qs = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(payroll__company_id=company_id) if company_id else qs.none()

    def get(self, request, pk=None):
        _require_salary_perm(request.user)
        line = self.get_object()
        payments = line.payments.select_related("paid_by", "cashbox", "shift", "cashflow").all().order_by("-paid_at", "-created_at")
        return Response(BuildingPayrollPaymentSerializer(payments, many=True, context={"request": request}).data, status=status.HTTP_200_OK)

    @transaction.atomic
    def post(self, request, pk=None):
        _require_salary_perm(request.user)
        _require_cash_register_perm(request.user)
        line = self.get_object()

        payroll = BuildingPayrollPeriod.objects.select_for_update().get(id=line.payroll_id)
        if payroll.status != BuildingPayrollPeriod.Status.APPROVED:
            raise PermissionDenied("Выплачивать можно только из периода в статусе approved.")

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)

        amount = ser.validated_data["amount"]
        if amount <= 0:
            raise ValidationError({"amount": "Сумма должна быть > 0."})

        line.recalculate_totals()
        line.recalculate_paid_total()
        remaining = (Decimal(line.net_to_pay or 0) - Decimal(line.paid_total or 0)).quantize(Decimal("0.01"))
        if remaining <= 0:
            raise ValidationError({"amount": "По строке уже всё выплачено."})
        if amount > remaining:
            raise ValidationError({"amount": f"Нельзя выплатить больше остатка ({remaining})."})

        cashbox = Cashbox.objects.select_related("company").filter(id=ser.validated_data["cashbox"]).first()
        if not cashbox:
            raise ValidationError({"cashbox": "Касса не найдена."})
        if not getattr(request.user, "is_superuser", False) and cashbox.company_id != getattr(request.user, "company_id", None):
            raise PermissionDenied("Касса другой компании.")

        shift = None
        shift_id = ser.validated_data.get("shift")
        if shift_id:
            shift = CashShift.objects.select_related("cashbox").filter(id=shift_id).first()
            if not shift:
                raise ValidationError({"shift": "Смена не найдена."})
            if shift.cashbox_id != cashbox.id:
                raise ValidationError({"shift": "Смена относится к другой кассе."})
            if shift.status != CashShift.Status.OPEN:
                raise ValidationError({"shift": "Нельзя выплатить из закрытой смены."})
            if (not _is_owner_like(request.user)) and shift.cashier_id != request.user.id:
                raise ValidationError({"shift": "Это не ваша смена."})

        paid_at = ser.validated_data.get("paid_at") or timezone.now()

        payment = BuildingPayrollPayment.objects.create(
            line=line,
            amount=amount,
            paid_at=paid_at,
            paid_by=request.user,
            cashbox=cashbox,
            shift=shift,
            status=BuildingPayrollPayment.Status.POSTED,
        )

        emp = line.employee
        emp_name = f"{getattr(emp, 'first_name', '')} {getattr(emp, 'last_name', '')}".strip() if emp else ""
        emp_display = emp_name or getattr(emp, "email", None) or getattr(emp, "username", None) or str(getattr(emp, "id", ""))
        cf = CashFlow.objects.create(
            cashbox=cashbox,
            shift=shift,
            type=CashFlow.Type.EXPENSE,
            name=f"ЗП: {emp_display} / {payroll.period_start} - {payroll.period_end}",
            amount=abs(amount),
            status=CashFlow.Status.APPROVED,
            source_business_operation_id=str(payment.id),
        )
        payment.cashflow = cf
        payment.save(update_fields=["cashflow"])

        line.recalculate_paid_total()
        payroll.try_mark_paid()

        return Response(BuildingPayrollPaymentSerializer(payment, context={"request": request}).data, status=status.HTTP_201_CREATED)


class BuildingPayrollMyLinesView(CompanyQuerysetMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = BuildingPayrollMyLineSerializer
    queryset = BuildingPayrollLine.objects.select_related("payroll").prefetch_related("payments")
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["payroll__status"]

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset().filter(employee_id=getattr(user, "id", None))
        if getattr(user, "is_superuser", False):
            return qs
        company_id = getattr(user, "company_id", None)
        return qs.filter(payroll__company_id=company_id) if company_id else qs.none()

