from rest_framework import generics, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.main.models import Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review, Notification
from apps.main.serializers import *
from apps.utils import get_filtered_contacts


# Универсальный Mixin для изоляции по компании
class CompanyRestrictedMixin:
    def get_queryset(self):
        return super().get_queryset().filter(company=self.request.user.company)


# Контакты
class ContactListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ContactSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Contact.objects.all()

    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'email', 'phone', 'client_company']
    filterset_fields = ['department']

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class ContactRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ContactSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Contact.objects.all()


# Воронки
class PipelineListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = PipelineSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Pipeline.objects.all()

    filter_backends = [filters.SearchFilter]
    search_fields = ['name']

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, company=self.request.user.company)


class PipelineRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PipelineSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Pipeline.objects.all()


# Сделки
class DealListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = DealSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Deal.objects.all()

    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'stage']
    filterset_fields = ['status', 'pipeline', 'assigned_to', 'contact']

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class DealRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DealSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Deal.objects.all()


# Задачи
class TaskListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Task.objects.all()

    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['title', 'description']
    filterset_fields = ['status', 'assigned_to', 'deal', 'due_date']

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class TaskRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Task.objects.all()


# Интеграции
class IntegrationListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = IntegrationSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Integration.objects.all()

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['type', 'status']

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class IntegrationRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IntegrationSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Integration.objects.all()


# Аналитика
class AnalyticsListAPIView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = AnalyticsSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Analytics.objects.all()

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['type']


# Заказы
class OrderListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Order.objects.all()

    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['order_number', 'customer_name', 'department', 'phone']
    filterset_fields = ['status', 'date_ordered']

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class OrderRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Order.objects.all()


# Товары
class ProductListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Product.objects.all()

    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'article', 'brand', 'category']
    filterset_fields = ['category', 'brand']

    def perform_create(self, serializer):
        serializer.save(company=self.request.user.company)


class ProductRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Product.objects.all()


# Отзывы
class ReviewListCreateAPIView(CompanyRestrictedMixin, generics.ListCreateAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Review.objects.all()

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['rating', 'user']

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, company=self.request.user.company)


class ReviewRetrieveUpdateDestroyAPIView(CompanyRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Review.objects.all()


# Уведомления
class NotificationListView(CompanyRestrictedMixin, generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Notification.objects.all()

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['is_read']


class NotificationDetailView(CompanyRestrictedMixin, generics.RetrieveAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Notification.objects.all()
    lookup_field = 'pk'


class MarkAllNotificationsReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'Все уведомления помечены как прочитанные'}, status=status.HTTP_200_OK)
