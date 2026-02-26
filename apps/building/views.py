from django.db.models import Q
from rest_framework import generics, permissions, filters, status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    ResidentialComplex,
    ResidentialComplexDrawing,
    ResidentialComplexWarehouse,
    BuildingProcurementRequest,
    BuildingProcurementItem,
    BuildingTransferRequest,
    BuildingWorkflowEvent,
    BuildingWarehouseStockItem,
    BuildingWarehouseStockMove,
)
from .serializers import (
    ResidentialComplexSerializer,
    ResidentialComplexCreateSerializer,
    ResidentialComplexDrawingSerializer,
    ResidentialComplexWarehouseSerializer,
    BuildingProcurementSerializer,
    BuildingProcurementItemSerializer,
    BuildingTransferSerializer,
    BuildingWorkflowEventSerializer,
    BuildingWarehouseStockItemSerializer,
    BuildingWarehouseStockMoveSerializer,
    BuildingReasonSerializer,
    BuildingTransferCreateSerializer,
    BuildingTransferAcceptSerializer,
)
from . import services


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


class ResidentialComplexDrawingListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/building/drawings/  — список чертежей ЖК компании.
    POST /api/building/drawings/  — создание чертежа и привязка к ЖК.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexDrawingSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = ResidentialComplexDrawing.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            return qs.filter(residential_complex__company_id=user.company_id)
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


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
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(residential_complex__company_id=user.company_id)
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        residential_complex = serializer.validated_data["residential_complex"]
        user = self.request.user
        if not getattr(user, "is_superuser", False) and residential_complex.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
        serializer.save()


class ResidentialComplexWarehouseDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ResidentialComplexWarehouseSerializer
    queryset = ResidentialComplexWarehouse.objects.select_related("residential_complex")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(residential_complex__company_id=user.company_id)
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs

    def perform_create(self, serializer):
        rc = serializer.validated_data["residential_complex"]
        user = self.request.user
        if not getattr(user, "is_superuser", False) and rc.company_id != getattr(user, "company_id", None):
            raise PermissionDenied("ЖК принадлежит другой компании.")
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
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(procurement__residential_complex__company_id=user.company_id)
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
            return qs.filter(procurement__residential_complex__company_id=user.company_id)
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
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(residential_complex__company_id=user.company_id)
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
            return qs.filter(warehouse__residential_complex__company_id=user.company_id)
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
            return qs.filter(warehouse__residential_complex__company_id=user.company_id)
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
            return qs.filter(warehouse__residential_complex__company_id=user.company_id)
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
            return qs.filter(
                Q(procurement__residential_complex__company_id=user.company_id)
                | Q(transfer__warehouse__residential_complex__company_id=user.company_id)
            )
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
            return qs.filter(warehouse__residential_complex__company_id=user.company_id)
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
            return qs.filter(warehouse__residential_complex__company_id=user.company_id)
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs
