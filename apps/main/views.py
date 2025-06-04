from rest_framework import generics, permissions
from apps.main.models import Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review
from apps.main.serializers import ContactSerializer, PipelineSerializer, DealSerializer, TaskSerializer, IntegrationSerializer, AnalyticsSerializer, OrderSerializer, ProductSerializer, ReviewSerializer
from apps.utils import get_filtered_contacts  


class ContactListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ContactSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        base_qs = Contact.objects.filter(owner=self.request.user)
        return get_filtered_contacts(base_qs, self.request.query_params)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

# Получение, обновление, удаление конкретного контакта
class ContactRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ContactSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Contact.objects.filter(owner=self.request.user)


class PipelineListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = PipelineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Pipeline.objects.filter(owner=self.request.user)
        name = self.request.query_params.get('name')
        if name:
            qs = qs.filter(name__icontains=name)
        return qs

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class PipelineRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PipelineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Pipeline.objects.filter(owner=self.request.user)
    
    
class DealListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = DealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Deal.objects.filter(assigned_to=user)

        # Фильтры
        status = self.request.query_params.get('status')
        pipeline = self.request.query_params.get('pipeline')
        stage = self.request.query_params.get('stage')
        assigned_to = self.request.query_params.get('assigned_to')

        if status:
            queryset = queryset.filter(status=status)
        if pipeline:
            queryset = queryset.filter(pipeline__id=pipeline)
        if stage:
            queryset = queryset.filter(stage__icontains=stage)
        if assigned_to:
            queryset = queryset.filter(assigned_to__id=assigned_to)

        return queryset

    def perform_create(self, serializer):
        serializer.save()
        

class DealRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Deal.objects.filter(assigned_to=self.request.user)
    
    
class TaskListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Task.objects.filter(assigned_to=self.request.user)

        # Фильтры
        status = self.request.query_params.get('status')
        due_date = self.request.query_params.get('due_date')
        assigned_to = self.request.query_params.get('assigned_to')

        if status:
            queryset = queryset.filter(status=status)
        if due_date:
            queryset = queryset.filter(due_date__date=due_date)
        if assigned_to:
            queryset = queryset.filter(assigned_to__id=assigned_to)

        return queryset

    def perform_create(self, serializer):
        serializer.save()


class TaskRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Task.objects.filter(assigned_to=self.request.user)
    
    
class IntegrationListCreateAPIView(generics.ListCreateAPIView):
    queryset = Integration.objects.all()
    serializer_class = IntegrationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = self.queryset
        type_ = self.request.query_params.get('type')
        status = self.request.query_params.get('status')

        if type_:
            queryset = queryset.filter(type=type_)
        if status:
            queryset = queryset.filter(status=status)

        return queryset


class IntegrationRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Integration.objects.all()
    serializer_class = IntegrationSerializer
    permission_classes = [permissions.IsAuthenticated]
    
class AnalyticsListAPIView(generics.ListAPIView):
    serializer_class = AnalyticsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Analytics.objects.all()

        # Фильтры по типу и дате
        type_ = self.request.query_params.get('type')
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if type_:
            queryset = queryset.filter(type=type_)

        if start_date and end_date:
            queryset = queryset.filter(data__date__gte=start_date, data__date__lte=end_date)

        return queryset
    
    
class OrderListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Order.objects.all()

        # Фильтрация
        status = self.request.query_params.get('status')
        customer_name = self.request.query_params.get('customer_name')
        department = self.request.query_params.get('department')

        if status:
            queryset = queryset.filter(status=status)
        if customer_name:
            queryset = queryset.filter(customer_name__icontains=customer_name)
        if department:
            queryset = queryset.filter(department__icontains=department)

        return queryset


class OrderRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    
class ProductListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Product.objects.all()

        # Фильтрация
        name = self.request.query_params.get('name')
        brand = self.request.query_params.get('brand')
        category = self.request.query_params.get('category')

        if name:
            queryset = queryset.filter(name__icontains=name)
        if brand:
            queryset = queryset.filter(brand__icontains=brand)
        if category:
            queryset = queryset.filter(category__icontains=category)

        return queryset


class ProductRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    
class ReviewListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Review.objects.all()
        rating = self.request.query_params.get('rating')
        user_id = self.request.query_params.get('user')

        if rating:
            queryset = queryset.filter(rating=rating)
        if user_id:
            queryset = queryset.filter(user__id=user_id)

        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ReviewRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Review.objects.filter(user=self.request.user)