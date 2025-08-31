from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Count, Avg
from django.utils.dateparse import parse_date

from rest_framework import generics, permissions, filters, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound
from django.shortcuts import get_object_or_404
from rest_framework import serializers

from django_filters.rest_framework import DjangoFilterBackend

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics,
    Order, Product, Review, Notification, Event,
    ProductBrand, ProductCategory, Warehouse, WarehouseEvent, Client,
    GlobalProduct, GlobalBrand, GlobalCategory, ClientDeal, Bid, SocialApplications
)
from apps.main.serializers import (
    ContactSerializer, PipelineSerializer, DealSerializer, TaskSerializer,
    IntegrationSerializer, AnalyticsSerializer, OrderSerializer, ProductSerializer,
    ReviewSerializer, NotificationSerializer, EventSerializer,
    WarehouseSerializer, WarehouseEventSerializer,
    ProductCategorySerializer, ProductBrandSerializer,
    OrderItemSerializer, ClientSerializer, ClientDealSerializer, BidSerializers, SocialApplicationsSerializers
)


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

        # Ищем в глобальной базе (без company!)
        gp = GlobalProduct.objects.select_related("brand", "category").filter(barcode=barcode).first()
        if not gp:
            return Response(
                {"barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Парсим цену и количество
        try:
            price = Decimal(str(request.data.get("price", 0)))
        except Exception:
            return Response({"price": "Неверный формат цены."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            quantity = int(request.data.get("quantity", 0))
            if quantity < 0:
                raise ValueError
        except Exception:
            return Response({"quantity": "Неверное количество."}, status=status.HTTP_400_BAD_REQUEST)

        # Создаём локальный товар
        product = Product.objects.create(
            company=company,
            name=gp.name,
            barcode=gp.barcode,
            brand=gp.brand,        # теперь сразу глобальная ссылка
            category=gp.category,  # теперь сразу глобальная ссылка
            price=price,
            quantity=quantity,
        )

        return Response(self.get_serializer(product).data, status=status.HTTP_201_CREATED)


class ProductCreateManualAPIView(generics.CreateAPIView):
    """
    Ручное создание товара + добавление в глобальную базу.
    """
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = request.user.company
        data = request.data

        name = (data.get("name") or "").strip()
        if not name:
            return Response({"name": "Обязательное поле."}, status=status.HTTP_400_BAD_REQUEST)

        barcode = (data.get("barcode") or "").strip() or None

        # Проверка уникальности в компании
        if barcode and Product.objects.filter(company=company, barcode=barcode).exists():
            return Response(
                {"barcode": "В вашей компании уже есть товар с таким штрих-кодом."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # price / quantity
        try:
            price = Decimal(str(data.get("price", 0)))
        except Exception:
            return Response({"price": "Неверный формат цены."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            quantity = int(data.get("quantity", 0))
            if quantity < 0:
                raise ValueError
        except Exception:
            return Response({"quantity": "Неверное количество."}, status=status.HTTP_400_BAD_REQUEST)

        # глобальный бренд / категория (или создаём при необходимости)
        g_brand = None
        brand_name = (data.get("brand_name") or "").strip()
        if brand_name:
            g_brand, _ = GlobalBrand.objects.get_or_create(name=brand_name)

        g_category = None
        category_name = (data.get("category_name") or "").strip()
        if category_name:
            g_category, _ = GlobalCategory.objects.get_or_create(name=category_name)

        # Создаём товар компании
        product = Product.objects.create(
            company=company,
            name=name,
            barcode=barcode,
            brand=g_brand,
            category=g_category,
            price=price,
            quantity=quantity,
        )

        # Если штрих-код есть — синхронизируем в глобальную базу
        if barcode:
            GlobalProduct.objects.get_or_create(
                barcode=barcode,
                defaults={"name": name, "brand": g_brand, "category": g_category},
            )

        return Response(self.get_serializer(product).data, status=status.HTTP_201_CREATED)



class ProductRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related("brand", "category").all()


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


# ---------- Сделки клиента ----------
class ClientDealListCreateAPIView(generics.ListCreateAPIView):
    """
    Варианты:
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
    ordering_fields = ["created_at", "amount", "kind"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = ClientDeal.objects.select_related("client").filter(company=self.request.user.company)
        client_id = self.kwargs.get("client_id")
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs

    def perform_create(self, serializer):
        company = self.request.user.company
        client_id = self.kwargs.get("client_id")

        if client_id:
            # Вложенный маршрут: клиент берётся из URL, проверяем принадлежность компании
            client = get_object_or_404(Client, id=client_id, company=company)
            serializer.save(company=company, client=client)
        else:
            # Обычный маршрут: client приходит в теле запроса — дополнительно проверим компанию
            client = serializer.validated_data.get("client")
            if not client or client.company_id != company.id:
                raise serializers.ValidationError({"client": "Клиент не найден в вашей компании."})
            serializer.save(company=company)


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
        # Ограничиваем сделки компанией текущего пользователя
        return ClientDeal.objects.select_related("client").filter(company=self.request.user.company)

    def perform_update(self, serializer):
        """
        Запрещаем «уход» сделки в другую компанию через смену client.
        Разрешаем смену клиента только внутри своей компании.
        """
        company = self.request.user.company
        new_client = serializer.validated_data.get("client")
        if new_client and new_client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        serializer.save()
        
        
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
    
    