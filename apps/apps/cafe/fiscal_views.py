# apps/cafe/fiscal_views.py
"""
API фискальной интеграции (налоговая ГНС КР) для кафе.

Фронт общается с коннектором (localhost:8080) напрямую. Эти эндпоинты:
  * хранят настройки кассы (реквизиты/ставки);
  * ведут журнал фискальных смен;
  * готовят тело чека из заказа и фиксируют результат фискализации.

Существующая логика оплаты заказов (OrderPayView и т.д.) не изменяется — модуль
аддитивный: фискальный чек пробивается отдельным шагом со стороны фронта.
"""
from django.utils import timezone
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .views import CompanyBranchQuerysetMixin
from .models import Order
from .fiscal_models import CafeFiscalSettings, CafeFiscalShift, CafeFiscalReceipt
from .fiscal_serializers import (
    CafeFiscalSettingsSerializer,
    CafeFiscalShiftSerializer,
    CafeFiscalShiftOpenSerializer,
    CafeFiscalShiftCloseSerializer,
    CafeFiscalReceiptSerializer,
    CafeFiscalReceiptRecordSerializer,
    CafeFiscalCashSerializer,
)
from .services.fiscal import build_receipt_payload, _q2


class _FiscalBase(CompanyBranchQuerysetMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _company_or_403(self):
        company = self._user_company()
        if not company:
            return None, Response(
                {"detail": "Компания не найдена."}, status=status.HTTP_403_FORBIDDEN
            )
        return company, None

    def _current_open_shift(self, company, branch):
        qs = CafeFiscalShift.objects.filter(company=company, status=CafeFiscalShift.Status.OPEN)
        if branch is not None:
            qs = qs.filter(branch=branch)
        return qs.order_by("-created_at").first()


class CafeFiscalSettingsView(_FiscalBase):
    """
    GET   /cafe/fiscal/settings/   — реквизиты кассы компании.
    PATCH /cafe/fiscal/settings/   — сохранить/обновить.
    """

    def get(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        obj, _ = CafeFiscalSettings.objects.get_or_create(company=company)
        return Response(CafeFiscalSettingsSerializer(obj).data, status=status.HTTP_200_OK)

    def patch(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        obj, _ = CafeFiscalSettings.objects.get_or_create(company=company)
        ser = CafeFiscalSettingsSerializer(instance=obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_200_OK)


class CafeFiscalShiftStateView(_FiscalBase):
    """GET /cafe/fiscal/shift/state/ — текущая открытая смена (или null)."""

    def get(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()
        shift = self._current_open_shift(company, branch)
        return Response(
            {
                "shift_opened": shift is not None,
                "shift": CafeFiscalShiftSerializer(shift).data if shift else None,
            },
            status=status.HTTP_200_OK,
        )


class CafeFiscalShiftListView(_FiscalBase):
    """GET /cafe/fiscal/shifts/ — журнал смен."""

    def get(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()
        qs = CafeFiscalShift.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)
        return Response(
            CafeFiscalShiftSerializer(qs.order_by("-created_at")[:200], many=True).data,
            status=status.HTTP_200_OK,
        )


class CafeFiscalShiftOpenView(_FiscalBase):
    """
    POST /cafe/fiscal/shift/open/ — зафиксировать открытие смены.
    Вызывается фронтом ПОСЛЕ успешного /driver/open-shift на коннекторе.
    """

    def post(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()

        ser = CafeFiscalShiftOpenSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        settings_obj = CafeFiscalSettings.objects.filter(company=company).first()

        with transaction.atomic():
            existing = self._current_open_shift(company, branch)
            if existing:
                return Response(
                    {"detail": "Смена уже открыта.", "shift": CafeFiscalShiftSerializer(existing).data},
                    status=status.HTTP_409_CONFLICT,
                )
            shift = CafeFiscalShift.objects.create(
                company=company,
                branch=branch,
                status=CafeFiscalShift.Status.OPEN,
                registration_number=(
                    data.get("registration_number")
                    or (settings_obj.registration_number if settings_obj else "")
                    or ""
                ),
                opened_at=timezone.now(),
                open_shift_datetime=data.get("open_shift_datetime"),
                fm_expiration_date=data.get("fm_expiration_date"),
                opened_by=request.user if request.user.is_authenticated else None,
                raw_open=data.get("raw") or {},
            )
            CafeFiscalReceipt.objects.create(
                company=company,
                branch=branch,
                shift=shift,
                kind=CafeFiscalReceipt.Kind.OPEN_SHIFT,
                response_payload=data.get("raw") or {},
                created_by=request.user if request.user.is_authenticated else None,
            )
        return Response(CafeFiscalShiftSerializer(shift).data, status=status.HTTP_201_CREATED)


class CafeFiscalShiftCloseView(_FiscalBase):
    """
    POST /cafe/fiscal/shift/close/ — зафиксировать закрытие смены.
    Вызывается фронтом ПОСЛЕ успешного /driver/close-shift на коннекторе.
    """

    def post(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()

        ser = CafeFiscalShiftCloseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        with transaction.atomic():
            shift = self._current_open_shift(company, branch)
            if not shift:
                return Response({"detail": "Открытая смена не найдена."}, status=status.HTTP_409_CONFLICT)
            shift.status = CafeFiscalShift.Status.CLOSED
            shift.closed_at = timezone.now()
            shift.closed_by = request.user if request.user.is_authenticated else None
            shift.raw_close = data.get("raw") or {}
            shift.save(update_fields=["status", "closed_at", "closed_by", "raw_close", "updated_at"])
            CafeFiscalReceipt.objects.create(
                company=company,
                branch=branch,
                shift=shift,
                kind=CafeFiscalReceipt.Kind.CLOSE_SHIFT,
                response_payload=data.get("raw") or {},
                created_by=request.user if request.user.is_authenticated else None,
            )
        return Response(CafeFiscalShiftSerializer(shift).data, status=status.HTTP_200_OK)


class _FiscalCashBase(_FiscalBase):
    kind = None
    operation_type = ""

    def post(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()

        ser = CafeFiscalCashSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        amount = _q2(data["amount"])

        shift = self._current_open_shift(company, branch)
        is_cash = self.kind in (CafeFiscalReceipt.Kind.DEPOSIT, CafeFiscalReceipt.Kind.WITHDRAW)
        receipt = CafeFiscalReceipt.objects.create(
            company=company,
            branch=branch,
            shift=shift,
            kind=self.kind,
            operation_type=self.operation_type,
            fd_number=data.get("fd_number"),
            fn_serial_number=data.get("fn_serial_number") or "",
            total_sum=amount,
            total_cash_sum=amount if is_cash else 0,
            pay_sum=amount,
            response_payload=data.get("response_payload") or {},
            created_by=request.user if request.user.is_authenticated else None,
        )
        return Response(CafeFiscalReceiptSerializer(receipt).data, status=status.HTTP_201_CREATED)


class CafeFiscalCashDepositView(_FiscalCashBase):
    """POST /cafe/fiscal/cash/deposit/ — зафиксировать внесение наличных."""
    kind = CafeFiscalReceipt.Kind.DEPOSIT


class CafeFiscalCashWithdrawView(_FiscalCashBase):
    """POST /cafe/fiscal/cash/withdraw/ — зафиксировать изъятие наличных."""
    kind = CafeFiscalReceipt.Kind.WITHDRAW


class CafeFiscalOrderReceiptPayloadView(_FiscalBase):
    """
    GET /cafe/fiscal/orders/<uuid:pk>/receipt-payload/?operation_type=INCOME&cash_received=...

    Возвращает готовое тело для POST {connector}/driver/cash-register/receipt.
    Фронт отправляет его на коннектор, а результат фиксирует через receipt/.
    """

    def get(self, request, pk):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()

        qs = Order.objects.filter(company=company).prefetch_related(
            "items__menu_item", "checkout_payments"
        )
        if branch is not None:
            qs = qs.filter(branch=branch)
        order = generics.get_object_or_404(qs, pk=pk)

        settings_obj, _ = CafeFiscalSettings.objects.get_or_create(company=company)

        operation_type = request.query_params.get("operation_type") or "INCOME"
        cash_received = request.query_params.get("cash_received")
        charge_amount = request.query_params.get("charge_amount")
        origin_fd = request.query_params.get("origin_fd_number")
        origin_fn = request.query_params.get("origin_fn_serial_number")

        payload = build_receipt_payload(
            order,
            settings_obj,
            operation_type=operation_type,
            cash_received=cash_received if cash_received not in (None, "") else None,
            charge_amount=charge_amount if charge_amount not in (None, "") else None,
            origin_fd_number=origin_fd if origin_fd not in (None, "") else None,
            origin_fn_serial_number=origin_fn or None,
        )
        return Response(
            {
                "connector_base_url": settings_obj.connector_base_url,
                "receipt_width": settings_obj.receipt_width,
                "path": "/driver/cash-register/receipt",
                "method": "POST",
                "body": payload,
            },
            status=status.HTTP_200_OK,
        )


class CafeFiscalOrderReceiptRecordView(_FiscalBase):
    """
    POST /cafe/fiscal/orders/<uuid:pk>/receipt/

    Фиксирует результат фискализации чека (ФД/ФМ) после ответа коннектора и
    связывает фискальный документ с заказом.
    """

    def post(self, request, pk):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()

        qs = Order.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)
        order = generics.get_object_or_404(qs, pk=pk)

        ser = CafeFiscalReceiptRecordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        order.recalc_total()
        final_amount = _q2((order.total_amount or 0) - (order.discount_amount or 0))

        shift = self._current_open_shift(company, order.branch or branch)
        receipt = CafeFiscalReceipt.objects.create(
            company=company,
            branch=order.branch or branch,
            order=order,
            shift=shift,
            kind=data["kind"],
            operation_type=data.get("operation_type") or "INCOME",
            fd_number=data.get("fd_number"),
            fn_serial_number=data.get("fn_serial_number") or "",
            total_sum=final_amount,
            request_payload=data.get("request_payload") or {},
            response_payload=data.get("response_payload") or {},
            created_by=request.user if request.user.is_authenticated else None,
        )
        return Response(CafeFiscalReceiptSerializer(receipt).data, status=status.HTTP_201_CREATED)


class CafeFiscalReceiptListView(_FiscalBase):
    """GET /cafe/fiscal/receipts/?kind=sale&order=<uuid> — журнал фискальных документов."""

    def get(self, request):
        company, err = self._company_or_403()
        if err:
            return err
        branch = self._active_branch()
        qs = CafeFiscalReceipt.objects.filter(company=company)
        if branch is not None:
            qs = qs.filter(branch=branch)
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        order_id = request.query_params.get("order")
        if order_id:
            qs = qs.filter(order_id=order_id)
        return Response(
            CafeFiscalReceiptSerializer(qs.order_by("-created_at")[:300], many=True).data,
            status=status.HTTP_200_OK,
        )
