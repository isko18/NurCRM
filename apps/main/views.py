from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Count, Avg
from django.utils.dateparse import parse_date
from django.utils import timezone

from rest_framework import generics, permissions, filters, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from .filters import TransactionRecordFilter, DebtFilter, DebtPaymentFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied

from apps.construction.models import Department

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics,
    Order, Product, Review, Notification, Event,
    ProductBrand, ProductCategory, Warehouse, WarehouseEvent, Client,
    GlobalProduct, GlobalBrand, GlobalCategory, ClientDeal, Bid, SocialApplications, TransactionRecord,
    ContractorWork, DealInstallment, DebtPayment, Debt, ObjectSaleItem, ObjectSale, ObjectItem, ItemMake
)
from apps.main.serializers import (
    ContactSerializer, PipelineSerializer, DealSerializer, TaskSerializer,
    IntegrationSerializer, AnalyticsSerializer, OrderSerializer, ProductSerializer,
    ReviewSerializer, NotificationSerializer, EventSerializer,
    WarehouseSerializer, WarehouseEventSerializer,
    ProductCategorySerializer, ProductBrandSerializer,
    OrderItemSerializer, ClientSerializer, ClientDealSerializer, BidSerializers, SocialApplicationsSerializers, TransactionRecordSerializer, ContractorWorkSerializer, DebtSerializer, DebtPaymentSerializer, ObjectItemSerializer, ObjectSaleSerializer, ObjectSaleItemSerializer, BulkIdsSerializer, ItemMakeSerializer
)
from django.db.models import ProtectedError

class CompanyRestrictedMixin:
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(company=self.request.user.company)

    def get_serializer_context(self):
        return {"request": self.request}

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class ContactListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "email", "phone", "client_company"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class ContactRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.all()


class PipelineListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.all()
    filter_backends = [filters.SearchFilter, DjangoFilterBackend]
    search_fields = ["name"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class PipelineRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.all()


class DealListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["title", "stage"]
    filterset_fields = "__all__"


class DealRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.all()


class TaskListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["title", "description"]
    filterset_fields = "__all__"


class TaskRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()


class IntegrationListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


class IntegrationRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()


class AnalyticsListAPIView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = AnalyticsSerializer
    queryset = Analytics.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


class OrderListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related("items__product")
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["order_number", "customer_name", "department", "phone"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        # важно не потерять company из миксина
        super().perform_create(serializer)


class OrderRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related("items__product")
    
    
class ProductCreateByBarcodeAPIView(generics.CreateAPIView):
    """
    Создание товара только по штрих-коду (если найден в глобальной базе).
    """
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = request.user.company
        barcode = (request.data.get("barcode") or "").strip()

        if not barcode:
            return Response({"barcode": "Укажите штрих-код."}, status=status.HTTP_400_BAD_REQUEST)

        # Проверка дубликатов внутри компании
        if Product.objects.filter(company=company, barcode=barcode).exists():
            return Response(
                {"barcode": "В вашей компании уже есть товар с таким штрих-кодом."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Ищем в глобальной базе
        gp = GlobalProduct.objects.select_related("brand", "category").filter(barcode=barcode).first()
        if not gp:
            return Response(
                {"barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Парсим цены и количество
        try:
            price = Decimal(str(request.data.get("price", 0)))
        except Exception:
            return Response({"price": "Неверный формат цены."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            purchase_price = Decimal(str(request.data.get("purchase_price", 0)))
        except Exception:
            return Response({"purchase_price": "Неверный формат закупочной цены."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            quantity = int(request.data.get("quantity", 0))
            if quantity < 0:
                raise ValueError
        except Exception:
            return Response({"quantity": "Неверное количество."}, status=status.HTTP_400_BAD_REQUEST)

        # Обработка даты
        date_value = request.data.get("date")
        if date_value:
            try:
                date_value = timezone.datetime.strptime(date_value, "%Y-%m-%d").date()
            except ValueError:
                return Response({"date": "Неверный формат даты. Используйте YYYY-MM-DD."}, status=400)
        else:
            date_value = timezone.now().date()

        # создаём или берём локальные справочники
        brand = ProductBrand.objects.get_or_create(company=company, name=gp.brand.name)[0] if gp.brand else None
        category = ProductCategory.objects.get_or_create(company=company, name=gp.category.name)[0] if gp.category else None

        # Создаём локальный товар
        product = Product.objects.create(
            company=company,
            name=gp.name,
            barcode=gp.barcode,
            brand=brand,
            category=category,
            price=price,
            purchase_price=purchase_price,
            quantity=quantity,
            date=date_value,  # <- теперь сохраняем дату
        )

        return Response(self.get_serializer(product).data, status=status.HTTP_201_CREATED)


class ProductCreateManualAPIView(generics.CreateAPIView):
    """
    Ручное создание товара + (опционально) добавление в глобальную базу.
    Принимает status как: pending/accepted/rejected или Ожидание/Принят/Отказ.
    Пустая строка/null -> статус не устанавливается (NULL).
    """
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    def _normalize_status(self, raw):
        """Приводим входной статус к коду из Product.Status.* или None."""
        if raw in (None, "", "null"):
            return None
        v = str(raw).strip().lower()
        mapping = {
            "pending":  Product.Status.PENDING,
            "accepted": Product.Status.ACCEPTED,
            "rejected": Product.Status.REJECTED,
            "ожидание": Product.Status.PENDING,
            "принят":   Product.Status.ACCEPTED,
            "отказ":    Product.Status.REJECTED,
        }
        if v in mapping:
            return mapping[v]
        codes = {c[0] for c in Product.Status.choices}
        if v in codes:
            return v
        raise ValueError(f"Недопустимый статус. Допустимые: {', '.join(sorted(codes))}.")

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = request.user.company
        data = request.data

        name = (data.get("name") or "").strip()
        if not name:
            return Response({"name": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)

        barcode = (data.get("barcode") or "").strip() or None

        # уникальность штрих-кода в компании
        if barcode and Product.objects.filter(company=company, barcode=barcode).exists():
            return Response(
                {"barcode": "В вашей компании уже есть товар с таким штрих-кодом."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # цены/кол-во
        try:
            price = Decimal(str(data.get("price", 0)))
        except Exception:
            return Response({"price": "Неверный формат цены."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            purchase_price = Decimal(str(data.get("purchase_price", 0)))
        except Exception:
            return Response({"purchase_price": "Неверный формат закупочной цены."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            quantity = int(data.get("quantity", 0))
            if quantity < 0:
                raise ValueError
        except Exception:
            return Response({"quantity": "Неверное количество."}, status=status.HTTP_400_BAD_REQUEST)

        # статус (необязателен)
        try:
            status_value = self._normalize_status(data.get("status"))
        except ValueError as e:
            return Response({"status": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # дата
        date_value = data.get("date")
        if date_value:
            try:
                date_value = timezone.datetime.strptime(date_value, "%Y-%m-%d").date()
            except ValueError:
                return Response({"date": "Неверный формат даты. Используйте YYYY-MM-DD."}, status=400)
        else:
            date_value = timezone.now().date()

        # глобальные справочники (если переданы имена)
        brand_name = (data.get("brand_name") or "").strip()
        category_name = (data.get("category_name") or "").strip()
        g_brand = GlobalBrand.objects.get_or_create(name=brand_name)[0] if brand_name else None
        g_category = GlobalCategory.objects.get_or_create(name=category_name)[0] if category_name else None

        # локальные справочники компании
        brand = ProductBrand.objects.get_or_create(company=company, name=g_brand.name)[0] if g_brand else None
        category = ProductCategory.objects.get_or_create(company=company, name=g_category.name)[0] if g_category else None

        # клиент (если передан)
        client = None
        client_id = data.get("client")
        if client_id:
            client = get_object_or_404(Client, id=client_id, company=company)

        # создаём товар
        product = Product.objects.create(
            company=company,
            name=name,
            barcode=barcode,
            brand=brand,
            category=category,
            price=price,
            purchase_price=purchase_price,
            quantity=quantity,
            client=client,
            status=status_value,
            date=date_value,  # <- сохраняем дату
        )

        # синхронизация в глобальную базу (если есть штрих-код)
        if barcode:
            GlobalProduct.objects.get_or_create(
                barcode=barcode,
                defaults={"name": name, "brand": g_brand, "category": g_category},
            )

        return Response(self.get_serializer(product).data, status=status.HTTP_201_CREATED)

class ProductRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related("brand", "category").all()

class ProductBulkDeleteAPIView(CompanyRestrictedMixin, APIView):
    """
    DELETE /api/main/products/bulk-delete/
    Body: {"ids": [...], "soft": false, "require_all": false}
    """
    def delete(self, request, *args, **kwargs):
        serializer = BulkIdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data["ids"]
        soft = serializer.validated_data["soft"]
        require_all = serializer.validated_data["require_all"]

        # найдём продукты заранее, чтобы отделить "не найдено"
        qs = Product.objects.filter(id__in=ids)
        found_map = {p.id: p for p in qs}
        not_found = [str(id_) for id_ in ids if id_ not in found_map]

        results = {"deleted": [], "protected": [], "not_found": not_found}

        def _delete_one(p: Product):
            try:
                if soft and hasattr(p, "is_active"):
                    p.is_active = False
                    p.save(update_fields=["is_active"])
                else:
                    p.delete()
                results["deleted"].append(str(p.id))
            except ProtectedError:
                results["protected"].append(str(p.id))

        if require_all:
            # либо все успешно, либо ничего
            try:
                with transaction.atomic():
                    for p in found_map.values():
                        _delete_one(p)
                    # если хоть один защищённый — ошибка, откатываем
                    if results["protected"]:
                        raise ProtectedError("protected", None)
            except ProtectedError:
                return Response(
                    {
                        "detail": "Некоторые продукты защищены связями, удаление откатено.",
                        "protected": results["protected"],
                        "not_found": results["not_found"],
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(results, status=status.HTTP_200_OK)

        # частичный режим: удаляем что можем
        for p in found_map.values():
            _delete_one(p)

        http_status = status.HTTP_200_OK if not results["protected"] else status.HTTP_207_MULTI_STATUS
        return Response(results, status=http_status)

class ReviewListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)


class ReviewRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.all()


class NotificationListView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


class NotificationDetailView(CompanyRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()


class MarkAllNotificationsReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"status": "Все уведомления прочитаны"}, status=status.HTTP_200_OK)


class EventListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = "__all__"


class EventRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.all()


class WarehouseListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "location"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class WarehouseRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()


class WarehouseEventListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["title", "client_name"]
    filterset_fields = "__all__"

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class WarehouseEventRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.all()


class ProductCategoryListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name"]
    filterset_fields = "__all__"


class ProductCategoryRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()


class ProductBrandListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name"]
    filterset_fields = "__all__"


class ProductBrandRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.all()


class ProductByBarcodeAPIView(CompanyRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = ProductSerializer
    lookup_field = "barcode"

    def get_object(self):
        barcode = self.kwargs.get("barcode")
        if not barcode:
            raise NotFound(detail="Штрих-код не указан")

        product = self.get_queryset().filter(barcode=barcode).first()
        if not product:
            raise NotFound(detail="Товар с таким штрих-кодом не найден")
        return product


class ProductListView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = ProductSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "barcode"]
    ordering_fields = ["created_at", "updated_at", "price"]

    def get_queryset(self):
        return Product.objects.filter(company=self.request.user.company)


class OrderAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        company = request.user.company
        start_date = request.query_params.get("start")
        end_date = request.query_params.get("end")
        status_filter = request.query_params.get("status")

        orders = Order.objects.filter(company=company)

        if start_date:
            start_date = parse_date(start_date)
            orders = orders.filter(date_ordered__gte=start_date)

        if end_date:
            end_date = parse_date(end_date)
            orders = orders.filter(date_ordered__lte=end_date)

        if status_filter:
            orders = orders.filter(status=status_filter)

        total_orders = orders.count()
        total_amount = orders.aggregate(total=Sum("items__total"))["total"] or 0
        average_amount = orders.aggregate(avg=Avg("items__total"))["avg"] or 0

        orders_by_status = orders.values("status").annotate(
            order_count=Count("id"),
            total_amount=Sum("items__total"),
            average_amount=Avg("items__total"),
        )

        response_data = {
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "status": status_filter,
            },
            "summary": {
                "total_orders": total_orders,
                "total_amount": total_amount,
                "average_order_amount": average_amount,
            },
            "orders_by_status": list(orders_by_status),
        }

        return Response(response_data)


class ClientListCreateAPIView(generics.ListCreateAPIView):
    """
    GET  /api/main/clients/
    POST /api/main/clients/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClientSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    # фильтры и поиск по базе
    filterset_fields = ["status", "date"]
    search_fields = ["full_name", "phone", "email"]
    ordering_fields = ["created_at", "updated_at", "date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Client.objects.filter(company=self.request.user.company)

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class ClientRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/main/clients/<uuid:pk>/
    PATCH  /api/main/clients/<uuid:pk>/
    PUT    /api/main/clients/<uuid:pk>/
    DELETE /api/main/clients/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClientSerializer

    def get_queryset(self):
        return Client.objects.filter(company=self.request.user.company)


class ClientDealListCreateAPIView(generics.ListCreateAPIView):
    """
      GET  /api/main/deals/                      — все сделки компании
      POST /api/main/deals/                      — создать сделку (client в теле)
      GET  /api/main/clients/<client_id>/deals/  — сделки конкретного клиента
      POST /api/main/clients/<client_id>/deals/  — создать сделку для клиента из URL
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClientDealSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["kind", "client"]
    search_fields = ["title", "note"]
    ordering_fields = ["created_at", "updated_at", "amount", "kind"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = (
            ClientDeal.objects
            .select_related("client")
            .prefetch_related("installments")        # чтобы не ловить N+1 при выдаче графика
            .filter(company=self.request.user.company)
        )
        client_id = self.kwargs.get("client_id")
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        company = self.request.user.company
        client_id = self.kwargs.get("client_id")

        if client_id:
            # nested-роут: клиент из URL, проверяем компанию
            client = get_object_or_404(Client, id=client_id, company=company)
            serializer.save(company=company, client=client)
        else:
            # плоский роут: client должен быть в теле и принадлежать компании
            client = serializer.validated_data.get("client")
            if not client or client.company_id != company.id:
                raise serializers.ValidationError({"client": "Клиент не найден в вашей компании."})
            serializer.save(company=company)          # company фиксируем на пользователя


class ClientDealRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/main/deals/<uuid:pk>/
    PATCH  /api/main/deals/<uuid:pk>/
    PUT    /api/main/deals/<uuid:pk>/
    DELETE /api/main/deals/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClientDealSerializer
    
    def get_queryset(self):
        qs = (
            ClientDeal.objects
            .select_related("client")
            .prefetch_related("installments")
            .filter(company_id=self.request.user.company_id)
        )
        client_id = self.kwargs.get("client_id")
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs
    
    @transaction.atomic
    def perform_update(self, serializer):
        """
        Не даём увести сделку в другую компанию через смену client.
        График пересоберётся в model.save().
        """
        company = self.request.user.company
        new_client = serializer.validated_data.get("client")
        if new_client and new_client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        serializer.save(company=company)  # company остаётся той же
        
class ClientDealPayAPIView(APIView):
    """
    POST /api/main/deals/<uuid:pk>/pay/
    Body (опционально):
      {
        "installment_number": 2,      // если не указать — оплатится ближайший не оплаченный
        "date": "2025-11-10"          // если не указать — сегодняшняя дата (локальная)
      }
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        company = request.user.company
        # если используете nested, можно проверить client_id:
        client_id = kwargs.get("client_id")

        deal_qs = ClientDeal.objects.filter(company=company, pk=pk)
        if client_id:
            deal_qs = deal_qs.filter(client_id=client_id)

        deal = get_object_or_404(deal_qs)

        if deal.kind != ClientDeal.Kind.DEBT:
            return Response({"detail": "Оплата помесячно доступна только для сделок типа 'debt'."},
                            status=status.HTTP_400_BAD_REQUEST)

        number = request.data.get("installment_number")
        paid_date_str = request.data.get("date")
        paid_date = parse_date(paid_date_str) if paid_date_str else timezone.localdate()

        if number:
            inst = get_object_or_404(DealInstallment, deal=deal, number=number)
            if inst.paid_on:
                return Response({"detail": f"Взнос №{number} уже оплачен ({inst.paid_on})."},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            inst = deal.installments.filter(paid_on__isnull=True).order_by("number").first()
            if not inst:
                return Response({"detail": "Все взносы уже оплачены."}, status=status.HTTP_400_BAD_REQUEST)

        inst.paid_on = paid_date
        inst.save(update_fields=["paid_on"])

        # вернём обновлённую сделку (с пересчитанным remaining_debt)
        data = ClientDealSerializer(deal, context={"request": request}).data
        return Response(data, status=status.HTTP_200_OK)

class BidListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = BidSerializers
    queryset = Bid.objects.all()
    
    
class BidRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BidSerializers
    queryset = Bid.objects.all()
    # lookup_field = "uuid"
    
class SocialApplicationsListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = SocialApplicationsSerializers
    queryset = SocialApplications.objects.all()
    
    
class SocialApplicationsRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SocialApplicationsSerializers
    queryset = SocialApplications.objects.all()
    

def _get_company(user):
    if user.is_superuser:
        return None
    return getattr(user, "owned_company", None) or getattr(user, "company", None)


class TransactionRecordListCreateView(generics.ListCreateAPIView):
    serializer_class = TransactionRecordSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = TransactionRecordFilter
    search_fields = ["name"]
    ordering_fields = ["date", "amount", "created_at", "id"]
    ordering = ["-date", "-created_at"]

    def get_queryset(self):
        user = self.request.user
        qs = TransactionRecord.objects.select_related("company", "department")
        if user.is_superuser:
            return qs
        company = _get_company(user)
        if company:
            return qs.filter(company=company)
        return qs.none()

    def perform_create(self, serializer):
        user = self.request.user
        company = _get_company(user)
        department = serializer.validated_data.get("department")

        # если не суперюзер и нет компании — запрещаем
        if not user.is_superuser and not company:
            raise PermissionDenied("Нет прав создавать записи.")

        # сверка company ↔ department (если оба заданы)
        if company and department and department.company_id != company.id:
            raise PermissionDenied("Отдел принадлежит другой компании.")

        # суперюзер без company должен указать department (чтобы взять company из него)
        if user.is_superuser and not company and department is None:
            raise PermissionDenied("Укажите отдел, чтобы определить компанию записи.")

        # не передаём company=None в save — пусть сериализатор установит из department при необходимости
        extra = {"company": company} if company is not None else {}
        serializer.save(**extra)


class TransactionRecordRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TransactionRecordSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = TransactionRecord.objects.select_related("company", "department")
        if user.is_superuser:
            return qs
        company = _get_company(user)
        if company:
            return qs.filter(company=company)
        return qs.none()
    
    
class ContractorWorkListCreateAPIView(generics.ListCreateAPIView):
    """
      GET  /api/main/contractor-works/
      POST /api/main/contractor-works/
      GET  /api/main/departments/<uuid:department_id>/contractor-works/
      POST /api/main/departments/<uuid:department_id>/contractor-works/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ContractorWorkSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["department", "contractor_entity_type", "start_date", "end_date"]
    search_fields = ["title", "contractor_name", "contractor_phone", "contractor_entity_name", "description"]
    ordering_fields = ["created_at", "updated_at", "amount", "start_date", "end_date", "planned_completion_date"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = ContractorWork.objects.select_related("department").filter(
            company_id=self.request.user.company_id
        )
        dep_id = self.kwargs.get("department_id")
        if dep_id:
            qs = qs.filter(department_id=dep_id)
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        company = self.request.user.company
        dep_id = self.kwargs.get("department_id")
        if dep_id:
            # nested: отдел из URL и в рамках компании
            department = get_object_or_404(Department, id=dep_id, company=company)
            serializer.save(company=company, department=department)
        else:
            # flat: отдел приходит в теле и должен принадлежать компании
            dep = serializer.validated_data.get("department")
            if not dep or dep.company_id != company.id:
                raise serializers.ValidationError({"department": "Отдел не найден в вашей компании."})
            serializer.save(company=company)


class ContractorWorkRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/main/contractor-works/<uuid:pk>/
    PATCH  /api/main/contractor-works/<uuid:pk>/
    PUT    /api/main/contractor-works/<uuid:pk>/
    DELETE /api/main/contractor-works/<uuid:pk>/

    (опционально nested)
    GET    /api/main/departments/<uuid:department_id>/contractor-works/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ContractorWorkSerializer

    def get_queryset(self):
        qs = ContractorWork.objects.select_related("department").filter(
            company_id=self.request.user.company_id
        )
        dep_id = self.kwargs.get("department_id")
        if dep_id:
            qs = qs.filter(department_id=dep_id)
        return qs

    @transaction.atomic
    def perform_update(self, serializer):
        company = self.request.user.company
        dep = serializer.validated_data.get("department")
        if dep and dep.company_id != company.id:
            raise serializers.ValidationError({"department": "Отдел принадлежит другой компании."})
        serializer.save(company=company)
        
        
class DebtListCreateAPIView(generics.ListCreateAPIView):
    """
    GET  /api/main/debts/?search=...&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    POST /api/main/debts/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DebtSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = DebtFilter
    search_fields = ["name", "phone"]
    ordering_fields = ["created_at", "updated_at", "amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Debt.objects.filter(company_id=self.request.user.company_id)


class DebtRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET/PATCH/PUT/DELETE /api/main/debts/<uuid:pk>/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DebtSerializer

    def get_queryset(self):
        return Debt.objects.filter(company_id=self.request.user.company_id)


class DebtPayAPIView(APIView):
    """
    POST /api/main/debts/<uuid:pk>/pay/
    Body: { "amount": "235.00", "paid_at": "2025-09-12", "note": "оплата с карты" }
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        company = request.user.company
        debt = get_object_or_404(Debt, pk=pk, company=company)

        ser = DebtPaymentSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        # создаём платеж, компания и долг фиксируются тут
        DebtPayment.objects.create(
            company=company,
            debt=debt,
            amount=ser.validated_data["amount"],
            paid_at=ser.validated_data.get("paid_at"),
            note=ser.validated_data.get("note", ""),
        )
        # вернём обновлённую карточку долга (для таблицы)
        return Response(DebtSerializer(debt, context={"request": request}).data, status=status.HTTP_201_CREATED)


# (опционально) список платежей по долгу
class DebtPaymentListAPIView(generics.ListAPIView):
    """
    GET /api/main/debts/<uuid:pk>/payments/?date_from=&date_to=
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = DebtPaymentSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = DebtPaymentFilter
    ordering_fields = ["paid_at", "created_at", "amount"]
    ordering = ["-paid_at", "-created_at"]

    def get_queryset(self):
        return DebtPayment.objects.filter(
            company_id=self.request.user.company_id,
            debt_id=self.kwargs["pk"]
        )
        
class ObjectItemListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ObjectItemSerializer
    queryset = ObjectItem.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["date", "created_at", "updated_at", "price", "quantity", "name"]
    ordering = ["-date", "-created_at"]

class ObjectItemRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ObjectItemSerializer
    queryset = ObjectItem.objects.all()
    
class ObjectSaleListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ObjectSaleSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["note", "client__full_name", "client__phone"]
    ordering_fields = ["sold_at", "created_at", "subtotal", "status"]
    ordering = ["-sold_at", "-created_at"]

    def get_queryset(self):
        return (
            ObjectSale.objects
            .select_related("client")
            .prefetch_related("items")
            .filter(company_id=self.request.user.company_id)
        )

    def perform_create(self, serializer):
        client = serializer.validated_data.get("client")
        if client.company_id != self.request.user.company_id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        serializer.save(company=self.request.user.company)

class ObjectSaleRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ObjectSaleSerializer
    def get_queryset(self):
        return (
            ObjectSale.objects
            .select_related("client")
            .prefetch_related("items")
            .filter(company_id=self.request.user.company_id)
        )


class ObjectSaleAddItemAPIView(CompanyRestrictedMixin, APIView):
    """
    POST /api/main/object-sales/<uuid:sale_id>/items/
    Body:
      { "object_item": "<uuid>", "unit_price": "200.00", "quantity": 2 }
    """
    def post(self, request, sale_id):
        company = request.user.company
        sale = get_object_or_404(ObjectSale, id=sale_id, company=company)

        ser = ObjectSaleItemSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)

        obj = get_object_or_404(ObjectItem, id=ser.validated_data["object_item"].id, company=company)

        item = ObjectSaleItem.objects.create(
            sale=sale,
            object_item=obj,
            name_snapshot=obj.name,
            unit_price=ser.validated_data.get("unit_price") or obj.price,
            quantity=ser.validated_data["quantity"],
        )
        sale.recalc()
        return Response(ObjectSaleItemSerializer(item).data, status=status.HTTP_201_CREATED)



class ItemListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    """
    GET  /api/main/items/      — список единиц товаров компании
    POST /api/main/items/      — создание новой единицы товара
    """
    serializer_class = ItemMakeSerializer
    queryset = ItemMake.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    # поиск по имени единицы и по названию связанных продуктов
    search_fields = ["name", "products__name"]
    filterset_fields = ["unit", "price", "quantity", "products"]
    ordering_fields = ["created_at", "updated_at", "price", "quantity", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """
        Ограничиваем queryset объектами компании пользователя.
        """
        qs = super().get_queryset()
        company = getattr(self.request.user, "company", None)
        if company:
            qs = qs.filter(company=company).distinct()
        return qs

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)

class ItemRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/main/items/<uuid:pk>/   — просмотр единицы товара
    PATCH  /api/main/items/<uuid:pk>/   — частичное обновление
    PUT    /api/main/items/<uuid:pk>/   — полное обновление
    DELETE /api/main/items/<uuid:pk>/   — удаление
    """
    serializer_class = ItemMakeSerializer
    queryset = ItemMake.objects.all()