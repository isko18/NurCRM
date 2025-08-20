from rest_framework import generics, status, permissions, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime, parse_date
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend

from apps.main.models import Cart, CartItem, Sale, Product, MobileScannerToken
from .pos_serializers import (
    SaleCartSerializer, SaleItemSerializer,
    ScanRequestSerializer, AddItemSerializer,
    CheckoutSerializer, MobileScannerTokenSerializer, SaleListSerializer,
)
from apps.main.services import checkout_cart, NotEnoughStock
from apps.main.views import CompanyRestrictedMixin
from apps.construction.models import Department


class SaleStartAPIView(APIView):
    """
    POST — создать/получить активную корзину для текущего пользователя.
    Если найдено несколько активных — оставим самую свежую, остальные закроем.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        user = request.user
        company = user.company

        qs = (Cart.objects
              .filter(company=company, user=user, status=Cart.Status.ACTIVE)
              .order_by('-created_at'))
        cart = qs.first()
        if cart is None:
            cart = Cart.objects.create(company=company, user=user, status=Cart.Status.ACTIVE)
        else:
            # закрыть дубликаты, если есть
            extra_ids = list(qs.values_list('id', flat=True)[1:])
            if extra_ids:
                Cart.objects.filter(id__in=extra_ids).update(
                    status=Cart.Status.CHECKED_OUT, updated_at=timezone.now()
                )

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class SaleDetailAPIView(generics.RetrieveAPIView):
    """
    GET — получить корзину по id.
    """
    serializer_class = SaleCartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Cart.objects.filter(company=self.request.user.company)


class SaleScanAPIView(APIView):
    """
    POST — добавить товар по штрих-коду (сканер ПК).
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )
        ser = ScanRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        barcode = ser.validated_data["barcode"].strip()
        qty = ser.validated_data["quantity"]

        try:
            product = Product.objects.get(company=cart.company, barcode=barcode)
        except Product.DoesNotExist:
            return Response({"not_found": True, "message": "Товар не найден"}, status=404)

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"company": cart.company, "quantity": qty, "unit_price": product.price},
        )
        if not created:
            item.quantity += qty
            item.save(update_fields=["quantity"])
        cart.recalc()

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class SaleAddItemAPIView(APIView):
    """
    POST — ручное добавление товара после поиска.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        product = get_object_or_404(
            Product, id=ser.validated_data["product_id"], company=cart.company
        )
        qty = ser.validated_data["quantity"]

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"company": cart.company, "quantity": qty, "unit_price": product.price},
        )
        if not created:
            item.quantity += qty
            item.save(update_fields=["quantity"])
        cart.recalc()

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class SaleCheckoutAPIView(APIView):
    """
    POST — оформить продажу (печать чека / без чека) + создать приход по кассе.
    body:
    {
      "print_receipt": true|false,
      "department_id": "uuid"   # опционально; если не передан — попробуем взять отдел пользователя
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )

        ser = CheckoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        print_receipt = ser.validated_data["print_receipt"]

        # Определяем отдел
        department_id = request.data.get("department_id")
        department = None
        if department_id:
            department = get_object_or_404(Department, id=department_id, company=request.user.company)

        try:
            sale = checkout_cart(cart, department=department)
        except NotEnoughStock as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            "sale_id": str(sale.id),
            "total": str(sale.total),
            "status": sale.status,
        }

        if print_receipt:
            lines = [
                f"{it.name_snapshot} x{it.quantity} = {(it.unit_price * it.quantity):.2f}"
                for it in sale.items.all()
            ]
            payload["receipt_text"] = "ЧЕК\n" + "\n".join(lines) + f"\nИТОГО: {sale.total:.2f}"

        return Response(payload, status=status.HTTP_201_CREATED)


class SaleMobileScannerTokenAPIView(APIView):
    """
    POST — выдать токен для телефона как сканера.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )
        token = MobileScannerToken.issue(cart, ttl_minutes=10)
        return Response(MobileScannerTokenSerializer(token).data, status=201)


class ProductFindByBarcodeAPIView(APIView):
    """
    GET — поиск товара по штрих-коду (строгий, 0 или 1 запись).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        barcode = request.query_params.get("barcode", "").strip()
        if not barcode:
            return Response([], status=200)
        qs = Product.objects.filter(company=request.user.company, barcode=barcode)[:1]
        return Response([
            {"id": str(p.id), "name": p.name, "barcode": p.barcode, "price": str(p.price)}
            for p in qs
        ], status=200)


class MobileScannerIngestAPIView(APIView):
    """
    POST — телефон отправляет штрих-код в корзину по токену.
    """
    permission_classes = [permissions.AllowAny]

    @transaction.atomic
    def post(self, request, token, *args, **kwargs):
        barcode = request.data.get("barcode", "").strip()
        qty = int(request.data.get("quantity", 1))
        if not barcode or qty <= 0:
            return Response({"detail": "barcode required"}, status=400)

        mt = MobileScannerToken.objects.select_related("cart", "cart__company").filter(token=token).first()
        if not mt:
            return Response({"detail": "invalid token"}, status=404)
        if not mt.is_valid():
            return Response({"detail": "token expired"}, status=410)

        cart = mt.cart
        try:
            product = Product.objects.get(company=cart.company, barcode=barcode)
        except Product.DoesNotExist:
            return Response({"not_found": True, "message": "Товар не найден"}, status=404)

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"company": cart.company, "quantity": qty, "unit_price": product.price},
        )
        if not created:
            item.quantity += qty
            item.save(update_fields=["quantity"])
        cart.recalc()

        return Response({"ok": True}, status=201)


class SaleListAPIView(CompanyRestrictedMixin, generics.ListAPIView):
    """
    GET /api/pos/sales?status=new|paid|canceled&start=2025-08-01&end=2025-08-10&user=<uuid>
    Возвращает список продаж по компании с простыми фильтрами.
    """
    serializer_class = SaleListSerializer
    queryset = Sale.objects.select_related("user").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ("status", "user")
    search_fields = ("id",)
    ordering_fields = ("created_at", "total", "status")
    ordering = ("-created_at",)

    def get_queryset(self):
        qs = super().get_queryset().filter(company=self.request.user.company)

        # Фильтры по дате (поддерживаются YYYY-MM-DD и ISO datetime)
        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            dt = parse_datetime(start) or (parse_date(start) and f"{start} 00:00:00")
            qs = qs.filter(created_at__gte=dt)
        if end:
            dt = parse_datetime(end) or (parse_date(end) and f"{end} 23:59:59")
            qs = qs.filter(created_at__lte=dt)

        paid_only = self.request.query_params.get("paid")
        if paid_only in ("1", "true", "True"):
            qs = qs.filter(status=Sale.Status.PAID)

        return qs
