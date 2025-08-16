from rest_framework import generics, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from django.utils.dateparse import parse_date
from django.db.models import Sum, Count, Avg
from rest_framework.exceptions import NotFound
from django.db import transaction

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics,
    Order, Product, Review, Notification, Event, ProductBrand, ProductCategory, Warehouse, WarehouseEvent, Client,
    GlobalProduct, CartItem
)
from apps.main.serializers import (
    ContactSerializer, PipelineSerializer, DealSerializer, TaskSerializer,
    IntegrationSerializer, AnalyticsSerializer, OrderSerializer, ProductSerializer,
    ReviewSerializer, NotificationSerializer, EventSerializer,
    WarehouseSerializer, WarehouseEventSerializer,
    ProductCategorySerializer, ProductBrandSerializer,
    OrderItemSerializer, ClientSerializer
)


class CompanyRestrictedMixin:
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(company=self.request.user.company)

    def get_serializer_context(self):
        return {'request': self.request}

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class ContactListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'email', 'phone', 'client_company']
    filterset_fields = '__all__'

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class ContactRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.all()


class PipelineListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.all()
    filter_backends = [filters.SearchFilter, DjangoFilterBackend]
    search_fields = ['name']
    filterset_fields = '__all__'

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class PipelineRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.all()


class DealListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'stage']
    filterset_fields = '__all__'


class DealRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.all()


class TaskListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'description']
    filterset_fields = '__all__'


class TaskRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()


class IntegrationListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = '__all__'


class IntegrationRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()


class AnalyticsListAPIView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = AnalyticsSerializer
    queryset = Analytics.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = '__all__'


class OrderListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related('items__product')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['order_number', 'customer_name', 'department', 'phone']
    filterset_fields = '__all__'

    def perform_create(self, serializer):
        serializer.save()


class OrderRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related('items__product')


class ProductCreateByBarcodeAPIView(generics.CreateAPIView):
    """Создание товара только по штрих-коду (если найден в глобальной базе)"""
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        barcode = (request.data.get('barcode') or '').strip()
        company = request.user.company

        if not barcode:
            return Response({"barcode": "Укажите штрих-код."}, status=status.HTTP_400_BAD_REQUEST)

        # Проверка, что в компании нет такого товара
        if Product.objects.filter(company=company, barcode=barcode).exists():
            return Response({"barcode": "В вашей компании уже есть товар с таким штрих-кодом."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Ищем в глобальной базе (связки brand/category уже FK)
        gp = GlobalProduct.objects.select_related('brand', 'category').filter(barcode=barcode).first()
        if not gp:
            return Response({"barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."},
                            status=status.HTTP_404_NOT_FOUND)

        # Создаём бренд и категорию в компании (если нет)
        brand = ProductBrand.objects.get_or_create(company=company, name=gp.brand.name if gp.brand else None)[0] if gp.brand else None
        category = ProductCategory.objects.get_or_create(company=company, name=gp.category.name if gp.category else None)[0] if gp.category else None

        # Создаём товар в компании
        product = Product.objects.create(
            company=company,
            name=gp.name,
            barcode=gp.barcode,
            brand=brand,
            category=category,
            price=request.data.get('price', 0),
            quantity=request.data.get('quantity', 0)
        )
        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ProductCreateManualAPIView(generics.CreateAPIView):
    """Ручное создание товара + добавление в глобальную базу"""
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        company = request.user.company
        barcode = (request.data.get('barcode') or '').strip()

        # Проверка уникальности в компании
        if barcode and Product.objects.filter(company=company, barcode=barcode).exists():
            return Response({"barcode": "В вашей компании уже есть товар с таким штрих-кодом."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Локальный бренд
        brand_name = request.data.get('brand_name', '').strip()
        brand = ProductBrand.objects.get_or_create(company=company, name=brand_name)[0] if brand_name else None

        # Локальная категория
        category_name = request.data.get('category_name', '').strip()
        category = ProductCategory.objects.get_or_create(company=company, name=category_name)[0] if category_name else None

        # Создаём товар в компании
        product = Product.objects.create(
            company=company,
            name=request.data.get('name'),
            barcode=barcode or None,
            brand=brand,
            category=category,
            price=request.data.get('price', 0),
            quantity=request.data.get('quantity', 0)
        )

        # Если есть barcode — создаём в глобальной базе (если ещё нет)
        if barcode and not GlobalProduct.objects.filter(barcode=barcode).exists():
            # Создаём глобальные бренд/категорию
            g_brand = None
            if brand:
                from apps.main.models import GlobalBrand
                g_brand, _ = GlobalBrand.objects.get_or_create(name=brand.name)

            g_category = None
            if category:
                from apps.main.models import GlobalCategory
                g_category, _ = GlobalCategory.objects.get_or_create(name=category.name)

            GlobalProduct.objects.create(
                name=product.name,
                barcode=product.barcode,
                brand=g_brand,
                category=g_category
            )

        serializer = self.get_serializer(product)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ProductRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.select_related('brand', 'category').all()



class ReviewListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = '__all__'

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)


class ReviewRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.all()


class NotificationListView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = '__all__'


class NotificationDetailView(CompanyRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()


class MarkAllNotificationsReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'Все уведомления прочитаны'}, status=status.HTTP_200_OK)


class EventListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = '__all__'


class EventRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.all()


class WarehouseListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'location']
    filterset_fields = '__all__'

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class WarehouseRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()


class WarehouseEventListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'client_name']
    filterset_fields = '__all__'

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class WarehouseEventRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.all()


class ProductCategoryListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name']
    filterset_fields = '__all__'


class ProductCategoryRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()


class ProductBrandListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name']
    filterset_fields = '__all__'


class ProductBrandRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.all()
    
class ProductByBarcodeAPIView(CompanyRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = ProductSerializer
    lookup_field = 'barcode'

    def get_object(self):
        barcode = self.kwargs.get('barcode')
        if not barcode:
            raise NotFound(detail="Штрих-код не указан")

        product = self.get_queryset().filter(barcode=barcode).first()
        if not product:
            raise NotFound(detail="Товар с таким штрих-кодом не найден")
        return product
        
class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'barcode']
    ordering_fields = ['created_at', 'updated_at', 'price']

    def get_queryset(self):
        return Product.objects.filter(company=self.request.user.company)
        
class OrderAnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        company = request.user.company
        start_date = request.query_params.get('start')
        end_date = request.query_params.get('end')
        status_filter = request.query_params.get('status')

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
        total_amount = orders.aggregate(total=Sum('items__total'))['total'] or 0
        average_amount = orders.aggregate(avg=Avg('items__total'))['avg'] or 0

        # Группировка по статусу
        orders_by_status = orders.values('status').annotate(
            order_count=Count('id'),
            total_amount=Sum('items__total'),
            average_amount=Avg('items__total')
        )

        response_data = {
            'filters': {
                'start_date': start_date,
                'end_date': end_date,
                'status': status_filter,
            },
            'summary': {
                'total_orders': total_orders,
                'total_amount': total_amount,
                'average_order_amount': average_amount,
            },
            'orders_by_status': list(orders_by_status)
        }

        return Response(response_data)
    
    
class ClientListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ClientSerializer
    queryset = Client.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['full_name', 'phone']
    filterset_fields = ['status']

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class ClientRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ClientSerializer
    queryset = Client.objects.all()
    
    
    