from rest_framework import serializers
from apps.main.models import Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review, Notification
from apps.users.models import User, Company

# Контакты
class ContactSerializer(serializers.ModelSerializer):
    owner = serializers.ReadOnlyField(source='owner.id')
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Contact
        fields = [
            'id', 'name', 'email', 'phone', 'address', 'client_company',
            'notes', 'department', 'created_at', 'updated_at',
            'owner', 'company'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner', 'company']

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Воронка
class PipelineSerializer(serializers.ModelSerializer):
    owner = serializers.ReadOnlyField(source='owner.id')
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Pipeline
        fields = ['id', 'name', 'stages', 'created_at', 'updated_at', 'owner', 'company']
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner', 'company']

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Сделка
class DealSerializer(serializers.ModelSerializer):
    pipeline = serializers.PrimaryKeyRelatedField(queryset=Pipeline.objects.all())
    contact = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.all())
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Deal
        fields = [
            'id', 'title', 'value', 'status',
            'pipeline', 'stage', 'contact', 'assigned_to',
            'company', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def validate(self, attrs):
        user = self.context['request'].user
        company = user.company

        if attrs['pipeline'].company != company:
            raise serializers.ValidationError("Воронка принадлежит другой компании.")
        if attrs['contact'].company != company:
            raise serializers.ValidationError("Контакт принадлежит другой компании.")
        if attrs['assigned_to'].company != company:
            raise serializers.ValidationError("Ответственный не из вашей компании.")

        return attrs

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Задача
class TaskSerializer(serializers.ModelSerializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)
    deal = serializers.PrimaryKeyRelatedField(queryset=Deal.objects.all(), allow_null=True, required=False)
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'due_date', 'status',
            'assigned_to', 'deal', 'company',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Интеграции
class IntegrationSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Integration
        fields = ['id', 'type', 'config', 'status', 'company', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Аналитика
class AnalyticsSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Analytics
        fields = ['id', 'type', 'data', 'company', 'created_at']
        read_only_fields = ['id', 'created_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Заказы
class OrderSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_name', 'date_ordered',
            'status', 'phone', 'department', 'total', 'quantity',
            'company', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Товары
class ProductSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'article', 'brand', 'category',
            'quantity', 'price', 'company', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Отзывы
class ReviewSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.id')
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Review
        fields = ['id', 'user', 'rating', 'comment', 'company', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'company', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['user'] = self.context['request'].user
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Уведомления
class NotificationSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Notification
        fields = ['id', 'message', 'is_read', 'company', 'created_at']
        read_only_fields = ['id', 'created_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)
