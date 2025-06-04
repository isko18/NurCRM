from rest_framework import serializers
from apps.main.models import Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review
from apps.users.models import User


class ContactSerializer(serializers.ModelSerializer):
    owner = serializers.ReadOnlyField(source='owner.id')  # Показываем ID владельца, но не редактируем

    class Meta:
        model = Contact
        fields = [
            'id',
            'name',
            'email',
            'phone',
            'address',
            'company',
            'notes',
            'department',
            'created_at',
            'updated_at',
            'owner',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner']


class PipelineSerializer(serializers.ModelSerializer):
    owner = serializers.ReadOnlyField(source='owner.id')

    class Meta:
        model = Pipeline
        fields = [
            'id', 'name', 'stages', 'created_at', 'updated_at', 'owner'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner']
        
        
class DealSerializer(serializers.ModelSerializer):
    pipeline = serializers.PrimaryKeyRelatedField(queryset=Pipeline.objects.all())
    contact = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.all())
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)

    class Meta:
        model = Deal
        fields = [
            'id', 'title', 'value', 'status',
            'pipeline', 'stage', 'contact', 'assigned_to',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        user = self.context['request'].user

        if attrs['pipeline'].owner != user:
            raise serializers.ValidationError("Вы не владеете этой воронкой.")
        if attrs['contact'].owner != user:
            raise serializers.ValidationError("Вы не владеете этим контактом.")

        return attrs
    
class TaskSerializer(serializers.ModelSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)
    deal = serializers.PrimaryKeyRelatedField(queryset=Deal.objects.all(), allow_null=True, required=False)

    class Meta:
        model = Task
        fields = [
            'id',
            'title',
            'description',
            'due_date',
            'status',
            'assigned_to',
            'deal',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        
        
class IntegrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Integration
        fields = [
            'id', 'type', 'config', 'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        
class AnalyticsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Analytics
        fields = ['id', 'type', 'data', 'created_at']
        read_only_fields = ['id', 'created_at']
        
class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_name', 'date_ordered',
            'status', 'phone', 'department', 'total', 'quantity',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        
class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            'id', 'name', 'article', 'brand', 'category',
            'quantity', 'price', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        
class ReviewSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.id')  # или .email, если нужно отображать

    class Meta:
        model = Review
        fields = [
            'id', 'user', 'rating', 'comment',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']