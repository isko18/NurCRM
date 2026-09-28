"""
Прокат и залоги (BACKEND_API.md, 7.3):

    GET  /api/rentals/?status=active|overdue|returned
    POST /api/rentals/                {client, items: [{variant} | {product, qty}], date_from, date_to,
                                       tariff, deposit_type: money|document, deposit_amount}
    GET  /api/rentals/{id}/
    POST /api/rentals/{id}/return/    {condition: ok|damaged, penalty, payment: {method, received}}

Залог — движение денег смены: приход «Залог», расход «Возврат залога».
Штраф — строка в продаже; из денежного залога он удерживается («зачёт»).
Выданные вещи уходят из остатка магазина и возвращаются при возврате.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.main.kassa_stage3 import _checkout_payload, _move_stock, _new_cart, _open_shift
from apps.main.kassa_views import _company
from apps.main.models import CartItem, Client, Product, ProductVariant, Rental, RentalItem, Sale
from apps.main.pos_serializers import MoneyField, QtyField
from apps.main.pos_utils import money, qty3

ZERO = Decimal("0.00")


class RentalItemInSerializer(serializers.Serializer):
    variant = serializers.UUIDField(required=False, allow_null=True)
    product = serializers.UUIDField(required=False, allow_null=True)
    qty = QtyField(required=False, default=Decimal("1"))

    def validate(self, attrs):
        if not attrs.get("variant") and not attrs.get("product"):
            raise serializers.ValidationError("Укажите variant или product.")
        if attrs["qty"] <= 0:
            raise serializers.ValidationError({"qty": "Количество должно быть больше нуля."})
        return attrs


class RentalCreateSerializer(serializers.Serializer):
    client = serializers.UUIDField()
    items = RentalItemInSerializer(many=True, allow_empty=False)
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    tariff = serializers.CharField(required=False, allow_blank=True, max_length=255)
    deposit_type = serializers.ChoiceField(choices=Rental.DepositType.choices, default=Rental.DepositType.MONEY)
    deposit_amount = MoneyField(required=False, default=ZERO)
    deposit_method = serializers.CharField(required=False, allow_blank=True, default="cash")
    deposit_document = serializers.CharField(required=False, allow_blank=True, max_length=255)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate(self, attrs):
        if attrs["date_to"] < attrs["date_from"]:
            raise serializers.ValidationError({"date_to": "Дата возврата раньше даты выдачи."})
        if attrs["deposit_amount"] < 0:
            raise serializers.ValidationError({"deposit_amount": "Залог не может быть отрицательным."})
        if attrs["deposit_type"] == Rental.DepositType.DOCUMENT:
            attrs["deposit_amount"] = ZERO
        return attrs


class RentalReturnSerializer(serializers.Serializer):
    condition = serializers.ChoiceField(choices=Rental.Condition.choices, default=Rental.Condition.OK)
    penalty = MoneyField(required=False, default=ZERO)
    payment = serializers.DictField(required=False)

    def validate_penalty(self, value):
        if value < 0:
            raise serializers.ValidationError("Штраф не может быть отрицательным.")
        return value


def _payload(r: Rental):
    today = timezone.localdate()
    return {
        "id": str(r.id),
        "number": r.number,
        "client": str(r.client_id),
        "client_name": r.client.full_name if r.client_id else None,
        "status": r.status,
        "overdue": r.status == Rental.Status.ACTIVE and r.date_to < today,
        "date_from": r.date_from.isoformat(),
        "date_to": r.date_to.isoformat(),
        "tariff": r.tariff,
        "deposit_type": r.deposit_type,
        "deposit_amount": str(r.deposit_amount),
        "deposit_method": r.deposit_method,
        "deposit_document": r.deposit_document,
        "items": [
            {
                "product": str(i.product_id),
                "variant": str(i.variant_id) if i.variant_id else None,
                "name": i.product.name,
                "size": i.variant.size if i.variant_id else None,
                "color": i.variant.color if i.variant_id else None,
                "qty": str(qty3(i.quantity)),
            }
            for i in r.items.select_related("product", "variant")
        ],
        "condition": r.condition or None,
        "penalty": str(r.penalty),
        "penalty_sale": str(r.penalty_sale_id) if r.penalty_sale_id else None,
        "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "returned_at": r.returned_at.isoformat() if r.returned_at else None,
    }


def _deposit_flow(rental, user, shift, amount, *, refund):
    from apps.construction.auto_cashflow import create_auto_cashflow
    from apps.construction.models import CashFlow

    method = rental.deposit_method or "cash"
    return create_auto_cashflow(
        company=rental.company,
        branch=shift.branch,
        cashbox=shift.cashbox,
        user=user,
        shift=shift,
        type=CashFlow.Type.EXPENSE if refund else CashFlow.Type.INCOME,
        amount=amount,
        source_kind=CashFlow.SourceKind.RENTAL_DEPOSIT,
        source_id=str(rental.id),
        payment_method=method,
        affects_shift_drawer=(method == "cash"),
        name=f"{'Возврат залога' if refund else 'Залог'} по прокату №{rental.number}",
        source_business_operation_id="rental",
    )


class RentalListCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        company = _company(request)
        qs = Rental.objects.filter(company=company).select_related("client")
        st = request.query_params.get("status")
        if st == "overdue":
            qs = qs.filter(status=Rental.Status.ACTIVE, date_to__lt=timezone.localdate())
        elif st in Rental.Status.values:
            qs = qs.filter(status=st)
        elif st:
            raise ValidationError({"status": "Допустимо: active, overdue, returned."})
        if request.query_params.get("client"):
            qs = qs.filter(client_id=request.query_params["client"])
        return Response([_payload(r) for r in qs[:500]])

    def post(self, request):
        company = _company(request)
        ser = RentalCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        client = get_object_or_404(Client, pk=data["client"], company=company)
        deposit = money(data["deposit_amount"])

        for attempt in range(3):
            try:
                with transaction.atomic():
                    shift = _open_shift(company, request.user) if deposit > 0 else None
                    last = Rental.objects.select_for_update().filter(company=company).order_by("-number").first()
                    rental = Rental.objects.create(
                        company=company,
                        branch=getattr(shift, "branch", None),
                        number=(last.number + 1) if last else 1,
                        client=client,
                        date_from=data["date_from"],
                        date_to=data["date_to"],
                        tariff=data.get("tariff") or "",
                        deposit_type=data["deposit_type"],
                        deposit_amount=deposit,
                        deposit_method=(data.get("deposit_method") or "cash") if deposit > 0 else "",
                        deposit_document=data.get("deposit_document") or "",
                        note=data.get("note") or "",
                        created_by=request.user,
                    )
                    for idx, it in enumerate(data["items"]):
                        variant = None
                        if it.get("variant"):
                            variant = ProductVariant.objects.select_related("product").filter(
                                pk=it["variant"], company=company
                            ).first()
                            if variant is None:
                                raise ValidationError({"items": {idx: "Вариант не найден."}})
                            product = variant.product
                        else:
                            product = Product.objects.filter(pk=it["product"], company=company).first()
                            if product is None:
                                raise ValidationError({"items": {idx: "Товар не найден."}})
                        qty = qty3(it["qty"])
                        _move_stock(company, None, product, variant, -qty)
                        RentalItem.objects.create(rental=rental, product=product, variant=variant, quantity=qty)
                    if deposit > 0:
                        _deposit_flow(rental, request.user, shift, deposit, refund=False)
                break
            except IntegrityError:
                if attempt == 2:
                    raise
        return Response(_payload(rental), status=status.HTTP_201_CREATED)


class RentalDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        return Response(_payload(get_object_or_404(Rental, pk=pk, company=_company(request))))


class RentalReturnAPIView(APIView):
    """POST /api/rentals/{id}/return/ {"condition": "ok"|"damaged", "penalty": "0", "payment": {...}}"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from apps.main.pos_views import SaleCheckoutAPIView

        company = _company(request)
        ser = RentalReturnSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        penalty = money(data.get("penalty") or ZERO)

        with transaction.atomic():
            rental = get_object_or_404(Rental.objects.select_for_update(), pk=pk, company=company)
            if rental.status == Rental.Status.RETURNED:
                return Response({**_payload(rental), "replayed": True})
            shift = _open_shift(company, request.user)

            for it in rental.items.select_related("product", "variant"):
                _move_stock(company, None, it.product, it.variant, qty3(it.quantity))

            withheld = ZERO
            if penalty > 0:
                if rental.deposit_type == Rental.DepositType.MONEY:
                    withheld = min(rental.deposit_amount, penalty)
                cart = _new_cart(company, request.user, shift)
                CartItem(
                    company=company,
                    branch=cart.branch,
                    cart=cart,
                    product=None,
                    custom_name=f"Штраф по прокату №{rental.number}",
                    unit_price=penalty,
                    quantity=Decimal("1"),
                ).save(skip_full_clean=True)
                cart.recalc()
                pay = _checkout_payload(data.get("payment"), penalty, withheld)
                pay["client_id"] = str(rental.client_id)
                resp = SaleCheckoutAPIView().checkout(request, cart.id, pay, allow_offset=True)
                if resp.status_code >= 400:
                    transaction.set_rollback(True)
                    return resp
                rental.penalty_sale = Sale.objects.get(pk=resp.data["sale_id"])

            refund = money(rental.deposit_amount - withheld) if rental.deposit_type == Rental.DepositType.MONEY else ZERO
            if refund > 0:
                _deposit_flow(rental, request.user, shift, refund, refund=True)

            rental.status = Rental.Status.RETURNED
            rental.condition = data["condition"]
            rental.penalty = penalty
            rental.returned_at = timezone.now()
            rental.save(update_fields=["status", "condition", "penalty", "penalty_sale", "returned_at"])

        body = _payload(rental)
        body["deposit_refunded"] = str(refund)
        body["deposit_withheld"] = str(withheld)
        return Response(body)
