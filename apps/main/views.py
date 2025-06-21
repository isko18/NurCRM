from rest_framework import generics, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics,
    Order, Product, Review, Notification, Event, ProductBrand, ProductCategory, Warehouse, WarehouseEvent
)
from apps.main.serializers import (
    ContactSerializer, PipelineSerializer, DealSerializer, TaskSerializer,
    IntegrationSerializer, AnalyticsSerializer, OrderSerializer, ProductSerializer,
    ReviewSerializer, NotificationSerializer, EventSerializer,
    WarehouseSerializer, WarehouseEventSerializer,
    ProductCategorySerializer, ProductBrandSerializer,
    OrderItemSerializer
)


# Универсальный миксин для всех вьюх
class CompanyRestrictedMixin:
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(company=self.request.user.company)

    def get_serializer_context(self):
        return {'request': self.request}

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


# Контакты
class ContactListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'email', 'phone', 'client_company']
    filterset_fields = ['department']

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class ContactRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ContactSerializer
    queryset = Contact.objects.all()


# Воронки
class PipelineListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.all()
    filter_backends = [filters.SearchFilter]
    search_fields = ['name']

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class PipelineRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PipelineSerializer
    queryset = Pipeline.objects.all()


# Сделки
class DealListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'stage']
    filterset_fields = ['status', 'pipeline', 'assigned_to', 'contact']


class DealRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DealSerializer
    queryset = Deal.objects.all()


# Задачи
class TaskListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'description']
    filterset_fields = ['status', 'assigned_to', 'deal', 'due_date']


class TaskRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaskSerializer
    queryset = Task.objects.all()


# Интеграции
class IntegrationListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['type', 'status']


class IntegrationRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IntegrationSerializer
    queryset = Integration.objects.all()


# Аналитика
class AnalyticsListAPIView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = AnalyticsSerializer
    queryset = Analytics.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['type']


# Заказы
class OrderListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related('items__product')  # 🔍 подтягиваем товары
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['order_number', 'customer_name', 'department', 'phone']
    filterset_fields = ['status', 'date_ordered']

    def perform_create(self, serializer):
        # company уже устанавливается в сериализаторе
        serializer.save()

class OrderRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderSerializer
    queryset = Order.objects.all().prefetch_related('items__product')


# Продукты
class ProductListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'article', 'brand__name', 'category__name']
    filterset_fields = ['category', 'brand']


class ProductRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    queryset = Product.objects.all()


# Отзывы
class ReviewListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['rating', 'user']

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)


class ReviewRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReviewSerializer
    queryset = Review.objects.all()


# Уведомления
class NotificationListView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['is_read']


class NotificationDetailView(CompanyRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()


class MarkAllNotificationsReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'Все уведомления прочитаны'}, status=status.HTTP_200_OK)


# События
class EventListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.all()
    filter_backends = [DjangoFilterBackend]

    def get_serializer_context(self):
        return {'request': self.request}

class EventRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = EventSerializer
    queryset = Event.objects.all()

    def get_serializer_context(self):
        return {'request': self.request}

class WarehouseListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'location']  # Для поиска по имени склада и его расположению

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)  # Присваиваем склад компании текущего пользователя


class WarehouseRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()


# Складское событие (WarehouseEvent)
class WarehouseEventListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'client_name']  # Поиск по названию события и клиенту

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)  # Присваиваем событие компании текущего пользователя


class WarehouseEventRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WarehouseEventSerializer
    queryset = WarehouseEvent.objects.all()
    
    
class ProductCategoryListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name']

class ProductCategoryRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductCategorySerializer
    queryset = ProductCategory.objects.all()


# Бренды товаров
class ProductBrandListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name']

class ProductBrandRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductBrandSerializer
    queryset = ProductBrand.objects.all()