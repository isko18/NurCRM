"""API посуды и расходников + категории расходов кафе."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cafe.models import (
    CafeExpenseCategory,
    CafeHouseholdInventoryLine,
    CafeHouseholdInventorySession,
    CafeHouseholdItem,
    CafeHouseholdMovement,
)
from apps.cafe.serializers import (
    CafeExpenseCategorySerializer,
    CafeHouseholdInventorySessionSerializer,
    CafeHouseholdItemSerializer,
    CafeHouseholdMovementSerializer,
    CafeHouseholdReceiveSerializer,
    CafeHouseholdWriteOffSerializer,
    WarehouseReceiveSerializer,
)
from apps.cafe.services.warehouse_expense import (
    household_receive,
    household_write_off,
    create_warehouse_receipt_expense,
)
from apps.cafe.views import CompanyBranchQuerysetMixin
from apps.cafe.cache_utils import invalidate_cafe_analytics_cache
from apps.cafe.models import CafeExpense, Warehouse
from apps.cafe.serializers import WarehouseSerializer


class CafeExpenseCategoryListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = CafeExpenseCategorySerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["is_system", "branch"]
    ordering_fields = ["sort_order", "title", "id"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CafeExpenseCategory.objects.none()
        qs = CafeExpenseCategory.objects.filter(company=company)
        branch = self._active_branch()
        if branch is not None:
            qs = qs.filter(branch__in=[branch, None])
        return qs.order_by("sort_order", "title")


class CafeExpenseCategoryRetrieveUpdateDestroyView(
    CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = CafeExpenseCategorySerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CafeExpenseCategory.objects.none()
        return CafeExpenseCategory.objects.filter(company=company)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.is_system:
            return Response(
                {"detail": "Системную категорию нельзя изменить."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.is_system:
            return Response(
                {"detail": "Системную категорию нельзя удалить."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)


class WarehouseReceiveView(CompanyBranchQuerysetMixin, APIView):
    """POST /cafe/warehouse/<uuid:pk>/receive/ — оприходование с авто-расходом «Закупки»."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN)

        wh = generics.get_object_or_404(Warehouse.objects.filter(company=company), pk=pk)
        active_branch = self._active_branch()
        if active_branch is not None and wh.branch_id not in (None, active_branch.id):
            return Response({"detail": "Позиция другого филиала."}, status=status.HTTP_404_NOT_FOUND)

        ser = WarehouseReceiveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        qty = ser.validated_data["quantity"]
        unit_price = ser.validated_data.get("unit_price")
        if unit_price is None:
            unit_price = wh.unit_price or Decimal("0")
        note = ser.validated_data.get("note") or f"Склад: приход {qty} {wh.unit}"

        with transaction.atomic():
            if ser.validated_data.get("unit_price") is not None:
                wh.unit_price = unit_price
                wh.save(update_fields=["unit_price"])
            movement, expense = create_warehouse_receipt_expense(
                warehouse=wh,
                quantity=qty,
                unit_price=unit_price,
                user=request.user,
                source=CafeExpense.Source.WAREHOUSE_RECEIPT,
                note=note,
            )

        invalidate_cafe_analytics_cache(company.id)
        data = WarehouseSerializer(wh, context={"request": request}).data
        data["expense_id"] = str(expense.id) if expense else None
        data["expense_amount"] = f"{expense.amount:.2f}" if expense else None
        data["movement_id"] = str(movement.id)
        return Response(data, status=status.HTTP_200_OK)


class CafeHouseholdItemListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = CafeHouseholdItemSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active", "branch"]
    search_fields = ["title", "sku"]
    ordering_fields = ["title", "remainder", "id"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CafeHouseholdItem.objects.none()
        qs = CafeHouseholdItem.objects.filter(company=company)
        branch = self._active_branch()
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true", "yes"))
        return qs


class CafeHouseholdItemRetrieveUpdateDestroyView(
    CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = CafeHouseholdItemSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CafeHouseholdItem.objects.none()
        return CafeHouseholdItem.objects.filter(company=company)

    def perform_update(self, serializer):
        if "remainder" in serializer.validated_data:
            raise ValidationError({"remainder": "Остаток меняется только через receive / write-off / инвентаризацию."})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.movements.exists():
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])
        else:
            instance.delete()


class CafeHouseholdMovementListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    serializer_class = CafeHouseholdMovementSerializer

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CafeHouseholdMovement.objects.none()
        item = generics.get_object_or_404(
            CafeHouseholdItem.objects.filter(company=company),
            pk=self.kwargs["pk"],
        )
        return item.movements.select_related("created_by", "expense").order_by("-created_at")


class CafeHouseholdReceiveView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN)
        item = generics.get_object_or_404(CafeHouseholdItem.objects.filter(company=company), pk=pk)
        ser = CafeHouseholdReceiveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                movement, expense = household_receive(
                    item=item,
                    quantity=ser.validated_data["quantity"],
                    unit_price=ser.validated_data.get("unit_price"),
                    user=request.user,
                    note=ser.validated_data.get("note") or "",
                )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        invalidate_cafe_analytics_cache(company.id)
        return Response({
            "item": CafeHouseholdItemSerializer(item, context={"request": request}).data,
            "movement": CafeHouseholdMovementSerializer(movement).data,
            "expense_id": str(expense.id) if expense else None,
        })


class CafeHouseholdWriteOffView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN)
        item = generics.get_object_or_404(CafeHouseholdItem.objects.filter(company=company), pk=pk)
        ser = CafeHouseholdWriteOffSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                movement = household_write_off(
                    item=item,
                    quantity=ser.validated_data["quantity"],
                    user=request.user,
                    note=ser.validated_data.get("note") or "",
                )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "item": CafeHouseholdItemSerializer(item, context={"request": request}).data,
            "movement": CafeHouseholdMovementSerializer(movement).data,
        })


class CafeHouseholdInventorySessionListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    serializer_class = CafeHouseholdInventorySessionSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["status", "branch"]
    ordering_fields = ["created_at", "id"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CafeHouseholdInventorySession.objects.none()
        qs = CafeHouseholdInventorySession.objects.filter(company=company).prefetch_related(
            "lines__item"
        )
        branch = self._active_branch()
        if branch is not None:
            qs = qs.filter(branch=branch)
        return qs


class CafeHouseholdInventorySessionRetrieveUpdateView(
    CompanyBranchQuerysetMixin, generics.RetrieveUpdateAPIView
):
    serializer_class = CafeHouseholdInventorySessionSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        company = self._user_company()
        if not company:
            return CafeHouseholdInventorySession.objects.none()
        return CafeHouseholdInventorySession.objects.filter(company=company).prefetch_related(
            "lines__item"
        )

    def partial_update(self, request, *args, **kwargs):
        session = self.get_object()
        if session.status != CafeHouseholdInventorySession.Status.DRAFT:
            return Response(
                {"detail": "Редактировать можно только черновик."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().partial_update(request, *args, **kwargs)


class CafeHouseholdInventorySessionConfirmView(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        company = self._user_company()
        if not company:
            return Response({"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN)
        session = generics.get_object_or_404(
            CafeHouseholdInventorySession.objects.filter(company=company).prefetch_related("lines__item"),
            pk=pk,
        )
        if session.status == CafeHouseholdInventorySession.Status.CONFIRMED:
            return Response(
                CafeHouseholdInventorySessionSerializer(session, context={"request": request}).data,
            )
        try:
            with transaction.atomic():
                summary = session.confirm(user=request.user)
        except DjangoValidationError as e:
            return Response(e.message_dict if hasattr(e, "message_dict") else {"detail": str(e)}, status=400)
        session.refresh_from_db()
        data = CafeHouseholdInventorySessionSerializer(session, context={"request": request}).data
        data["summary"] = summary
        return Response(data)
