"""
Прокат и залоги (BACKEND_API.md, 7.3):

    GET  /api/rentals/?status=active|overdue|returned
    POST /api/rentals/                {client, items: [{variant} | {product, qty}], date_from, date_to,
                                       tariff, deposit_type: money|document, deposit_amount}
    GET  /api/rentals/{id}/
    PATCH /api/rentals/{id}/          {rent_amount?, sale?, note?}
    POST /api/rentals/{id}/return/    {condition: ok|damaged, penalty, payment: {method, received},
                                       items?: [{variant} | {product, qty}]  — возврат части вещей}

deposit_method: cash | transfer | card. Залог cash — в наличные смены; transfer/card — безнал,
в ящик смены не попадает (affects_shift_drawer=False).
Штраф при залоге-документе (или payment.method=debt) — продажа «в долг» клиента (penalty_sale).
deposit_document скрыт для сотрудников без доступа к клиентам и очищается через 30 дней после возврата
(apps.main.rentals_tasks.purge_rental_documents_30_days).

Залог — движение денег смены: приход «Залог», расход «Возврат залога».
Штраф — строка в продаже; из денежного залога он удерживается («зачёт»).
Выданные вещи уходят из остатка магазина и возвращаются при возврате.
"""
from decimal import Decimal

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
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


class RentalReturnItemSerializer(serializers.Serializer):
    variant = serializers.UUIDField(required=False, allow_null=True)
    product = serializers.UUIDField(required=False, allow_null=True)
    qty = QtyField(required=False, default=Decimal("1"))


class RentalCreateSerializer(serializers.Serializer):
    client = serializers.UUIDField()
    items = RentalItemInSerializer(many=True, allow_empty=False)
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    tariff = serializers.CharField(required=False, allow_blank=True, max_length=255)
    rent_amount = MoneyField(required=False, default=ZERO)
    sale = serializers.UUIDField(required=False, allow_null=True)
    deposit_type = serializers.ChoiceField(choices=Rental.DepositType.choices, default=Rental.DepositType.MONEY)
    deposit_amount = MoneyField(required=False, default=ZERO)
    deposit_method = serializers.ChoiceField(
        choices=Rental.DepositMethod.choices,
        required=False,
        allow_blank=True,
        default=Rental.DepositMethod.CASH,
    )
    deposit_document = serializers.CharField(required=False, allow_blank=True, max_length=255)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate(self, attrs):
        if attrs["date_to"] < attrs["date_from"]:
            raise serializers.ValidationError({"date_to": "Дата возврата раньше даты выдачи."})
        if attrs["deposit_amount"] < 0:
            raise serializers.ValidationError({"deposit_amount": "Залог не может быть отрицательным."})
        if attrs.get("rent_amount") is not None and attrs["rent_amount"] < 0:
            raise serializers.ValidationError({"rent_amount": "Стоимость проката не может быть отрицательной."})
        if attrs["deposit_type"] == Rental.DepositType.DOCUMENT:
            attrs["deposit_amount"] = ZERO
        return attrs


class RentalUpdateSerializer(serializers.Serializer):
    """PATCH /api/rentals/{id}/ — касса дописывает стоимость проката и ссылку на продажу."""

    rent_amount = MoneyField(required=False)
    sale = serializers.UUIDField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_rent_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Стоимость проката не может быть отрицательной.")
        return value


class RentalReturnSerializer(serializers.Serializer):
    condition = serializers.ChoiceField(choices=Rental.Condition.choices, default=Rental.Condition.OK)
    penalty = MoneyField(required=False, default=ZERO)
    payment = serializers.DictField(required=False)
    items = RentalReturnItemSerializer(many=True, required=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, max_length=128)

    def validate_penalty(self, value):
        if value < 0:
            raise serializers.ValidationError("Штраф не может быть отрицательным.")
        return value


def _can_view_client_details(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    role = getattr(user, "role", "")
    if role in ("owner", "director", "admin", "creator"):
        return True
    return bool(getattr(user, "can_view_clients", False))


def _rental_items(r: Rental):
    cache_ = getattr(r, "_prefetched_objects_cache", {})
    if "items" in cache_:
        return r.items.all()
    return r.items.select_related("product", "variant")


def _payload(r: Rental, user=None):
    today = timezone.localdate()
    show_document = _can_view_client_details(user)
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
        "rent_amount": str(r.rent_amount) if getattr(r, "rent_amount", None) is not None else "0.00",
        "sale": str(r.sale_id) if getattr(r, "sale_id", None) else None,
        "deposit_type": r.deposit_type,
        "deposit_amount": str(r.deposit_amount),
        "deposit_method": r.deposit_method,
        "deposit_document": r.deposit_document if show_document else None,
        "items": [
            {
                "product": str(i.product_id),
                "variant": str(i.variant_id) if i.variant_id else None,
                "name": i.product.name,
                "size": i.variant.size if i.variant_id else None,
                "color": i.variant.color if i.variant_id else None,
                "qty": str(qty3(i.quantity)),
                "returned_qty": str(qty3(i.returned_quantity or 0)),
                "remaining_qty": str(qty3(max(Decimal("0"), i.quantity - (i.returned_quantity or 0)))),
            }
            for i in _rental_items(r)
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
        qs = (
            Rental.objects.filter(company=company)
            .select_related("client")
            .prefetch_related(Prefetch("items", queryset=RentalItem.objects.select_related("product", "variant")))
        )
        st = request.query_params.get("status")
        if st == "overdue":
            qs = qs.filter(status=Rental.Status.ACTIVE, date_to__lt=timezone.localdate())
        elif st in Rental.Status.values:
            qs = qs.filter(status=st)
        elif st:
            raise ValidationError({"status": "Допустимо: active, overdue, returned."})
        if request.query_params.get("client"):
            qs = qs.filter(client_id=request.query_params["client"])
        return Response([_payload(r, request.user) for r in qs[:500]])

    def post(self, request):
        company = _company(request)
        ser = RentalCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        client = get_object_or_404(Client, pk=data["client"], company=company)
        deposit = money(data["deposit_amount"])
        rent_amount = money(data.get("rent_amount") or ZERO)
        sale_id = data.get("sale")
        sale_obj = None
        if sale_id:
            sale_obj = Sale.objects.filter(pk=sale_id, company=company).first()
            if sale_obj is None:
                raise ValidationError({"sale": "Продажа не найдена."})

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
                        rent_amount=rent_amount,
                        sale=sale_obj,
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
                            if product.variants.filter(is_active=True).exists():
                                raise ValidationError({"items": {idx: f"Товар «{product.name}» имеет размеры/цвета. Выберите размер/цвет."}})
                        qty = qty3(it["qty"])
                        _move_stock(company, None, product, variant, -qty)
                        RentalItem.objects.create(rental=rental, product=product, variant=variant, quantity=qty)
                    if deposit > 0:
                        _deposit_flow(rental, request.user, shift, deposit, refund=False)
                break
            except IntegrityError:
                if attempt == 2:
                    raise
        return Response(_payload(rental, request.user), status=status.HTTP_201_CREATED)


class RentalDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        return Response(_payload(get_object_or_404(Rental, pk=pk, company=_company(request)), request.user))

    def patch(self, request, pk):
        company = _company(request)
        ser = RentalUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        with transaction.atomic():
            rental = get_object_or_404(Rental.objects.select_for_update(), pk=pk, company=company)
            fields = []
            if "rent_amount" in data:
                rental.rent_amount = money(data["rent_amount"] or ZERO)
                fields.append("rent_amount")
            if "sale" in data:
                sale_obj = None
                if data["sale"]:
                    sale_obj = Sale.objects.filter(pk=data["sale"], company=company).first()
                    if sale_obj is None:
                        raise ValidationError({"sale": "Продажа не найдена."})
                rental.sale = sale_obj
                fields.append("sale")
            if "note" in data:
                rental.note = data["note"] or ""
                fields.append("note")
            if fields:
                rental.save(update_fields=fields)
        return Response(_payload(rental, request.user))


class RentalReturnAPIView(APIView):
    """POST /api/rentals/{id}/return/ {"condition": "ok"|"damaged", "penalty": "0", "payment": {...}, "items": [...]}"""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from apps.main.pos_views import SaleCheckoutAPIView

        company = _company(request)
        ser = RentalReturnSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        penalty = money(data.get("penalty") or ZERO)
        items_to_return = data.get("items")
        idem_key = (request.headers.get("Idempotency-Key") or data.get("idempotency_key") or "").strip()
        idem_cache_key = f"rental_return:{company.id}:{pk}:{idem_key}" if idem_key else None
        if idem_cache_key:
            try:
                cached = cache.get(idem_cache_key)
            except Exception:
                cached = None
            if cached:
                return Response({**cached, "replayed": True})

        with transaction.atomic():
            rental = get_object_or_404(Rental.objects.select_for_update(), pk=pk, company=company)
            if rental.status == Rental.Status.RETURNED:
                return Response({**_payload(rental, request.user), "replayed": True})
            shift = _open_shift(company, request.user)

            rental_items = list(rental.items.select_for_update(of=("self",)).select_related("product", "variant"))

            def _remaining(ri):
                return qty3(ri.quantity - (ri.returned_quantity or 0))

            if items_to_return:
                # Возврат части вещей: копим количества по позициям проката
                to_return = {}
                for ret_it in items_to_return:
                    v_id = ret_it.get("variant")
                    p_id = ret_it.get("product")
                    q_val = qty3(ret_it.get("qty") or Decimal("1"))
                    if q_val <= 0:
                        continue
                    matched_item = None
                    for ri in rental_items:
                        if _remaining(ri) - to_return.get(ri.id, Decimal("0")) <= 0:
                            continue
                        if v_id and ri.variant_id == v_id:
                            matched_item = ri
                            break
                        if not v_id and p_id and ri.product_id == p_id:
                            matched_item = ri
                            break
                    if not matched_item:
                        raise ValidationError(
                            {"items": f"Товар {v_id or p_id} не найден среди невозвращённых вещей проката №{rental.number}."}
                        )
                    left = _remaining(matched_item) - to_return.get(matched_item.id, Decimal("0"))
                    if q_val > left:
                        raise ValidationError(
                            {"items": f"Нельзя вернуть {q_val:g}: по позиции осталось {left:g}."}
                        )
                    to_return[matched_item.id] = to_return.get(matched_item.id, Decimal("0")) + q_val
            else:
                # Полный возврат всех оставшихся вещей
                to_return = {ri.id: _remaining(ri) for ri in rental_items if _remaining(ri) > 0}

            for ri in rental_items:
                q = to_return.get(ri.id)
                if not q:
                    continue
                _move_stock(company, None, ri.product, ri.variant, q)
                ri.returned_quantity = qty3((ri.returned_quantity or 0) + q)
                ri.save(update_fields=["returned_quantity"])

            is_partial = any(_remaining(ri) > 0 for ri in rental_items)
            if is_partial and penalty > 0:
                raise ValidationError(
                    {"penalty": "Штраф указывается при возврате последних вещей (полном закрытии проката)."}
                )

            withheld = ZERO
            refund = ZERO
            penalty_offset_sale_id = None

            if not is_partial:
                # Только при полном возврате обрабатываем штраф и возврат залога
                if penalty > 0:
                    if rental.deposit_type == Rental.DepositType.MONEY:
                        withheld = min(rental.deposit_amount, penalty)

                    pay_method = (data.get("payment") or {}).get("method")
                    to_debt = pay_method == Sale.PaymentMethod.DEBT or (
                        rental.deposit_type == Rental.DepositType.DOCUMENT and not data.get("payment")
                    )

                    def _penalty_checkout(amount, title, pay):
                        cart = _new_cart(company, request.user, shift)
                        CartItem(
                            company=company,
                            branch=cart.branch,
                            cart=cart,
                            product=None,
                            custom_name=title,
                            unit_price=amount,
                            quantity=Decimal("1"),
                        ).save(skip_full_clean=True)
                        cart.recalc()
                        pay["client_id"] = str(rental.client_id)
                        return SaleCheckoutAPIView().checkout(request, cart.id, pay, allow_offset=True)

                    title = f"Штраф по прокату №{rental.number}"
                    debt_rest = money(penalty - withheld)
                    if to_debt and debt_rest > 0:
                        # Штраф в долг клиента: продажа со статусом «долг» (ClientDeal создаёт checkout).
                        # Денежный залог всё равно зачитывается: удержанная часть — отдельной продажей «зачёт»,
                        # в долг уходит только остаток. penalty_sale — долговая продажа (её принимает касса).
                        if withheld > 0:
                            resp = _penalty_checkout(
                                withheld,
                                f"{title} (удержано из залога)",
                                _checkout_payload(None, withheld, withheld),
                            )
                            if resp.status_code >= 400:
                                transaction.set_rollback(True)
                                return resp
                            penalty_offset_sale_id = resp.data["sale_id"]
                        resp = _penalty_checkout(
                            debt_rest,
                            f"{title} (в долг)" if withheld > 0 else title,
                            {"payment_method": Sale.PaymentMethod.DEBT, "cash_received": "0.00"},
                        )
                    else:
                        payment = None if to_debt else data.get("payment")
                        resp = _penalty_checkout(penalty, title, _checkout_payload(payment, penalty, withheld))
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
            else:
                # Частичный возврат: статус остаётся ACTIVE
                rental.condition = data["condition"]
                rental.save(update_fields=["condition"])

        body = _payload(rental, request.user)
        body["deposit_refunded"] = str(refund)
        body["deposit_withheld"] = str(withheld)
        body["is_partial"] = is_partial
        body["penalty_offset_sale"] = str(penalty_offset_sale_id) if penalty_offset_sale_id else None
        if idem_cache_key:
            try:
                cache.set(idem_cache_key, body, timeout=86400)
            except Exception:
                pass
        return Response(body)
