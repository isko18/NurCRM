from rest_framework import serializers
from apps.main.models import Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review, Notification, Event, Warehouse, WarehouseEvent, ProductCategory, ProductBrand, OrderItem
from apps.users.models import User, Company


class ProductCategorySerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=ProductCategory.objects.all(),
        allow_null=True,
        required=False  
    )

    class Meta:
        model = ProductCategory
        fields = ['id', 'name', 'parent']
        read_only_fields = ['id']

        
class ProductBrandSerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=ProductBrand.objects.all(),
        allow_null=True,
        required=False  
    )

    class Meta:
        model = ProductBrand
        fields = ['id', 'name', 'parent']
        read_only_fields = ['id']

        
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

class IntegrationSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Integration
        fields = ['id', 'type', 'config', 'status', 'company', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


class AnalyticsSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Analytics
        fields = ['id', 'type', 'data', 'company', 'created_at']
        read_only_fields = ['id', 'created_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)

class OrderItemSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'price', 'total']

    def validate(self, data):
        product = data['product']
        quantity = data['quantity']
        if product.quantity < quantity:
            raise serializers.ValidationError(
                f"Недостаточно товара на складе для '{product.name}'. Доступно: {product.quantity}"
            )
        return data

    def create(self, validated_data):
        product = validated_data['product']
        quantity = validated_data['quantity']
        price = product.price
        total = price * quantity

        # Списание товара
        product.quantity -= quantity
        product.save()

        return OrderItem.objects.create(
            **validated_data,
            price=price,
            total=total
        )
class OrderSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    items = OrderItemSerializer(many=True)

    total = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_name', 'date_ordered',
            'status', 'phone', 'department',
            'company', 'created_at', 'updated_at',
            'items', 'total', 'total_quantity'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company', 'total', 'total_quantity']

    def get_total(self, obj):
        return obj.total

    def get_total_quantity(self, obj):
        return obj.total_quantity

    def create(self, validated_data):
        request = self.context['request']
        validated_data['company'] = request.user.company

        items_data = validated_data.pop('items')
        order = Order.objects.create(**validated_data)

        for item_data in items_data:
            item_data['order'] = order
            OrderItemSerializer(context=self.context).create(item_data)

        return order



class ProductSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    brand = serializers.PrimaryKeyRelatedField(queryset=ProductBrand.objects.all(), allow_null=True)
    category = serializers.PrimaryKeyRelatedField(queryset=ProductCategory.objects.all(), allow_null=True)

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


class NotificationSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Notification
        fields = ['id', 'message', 'is_read', 'company', 'created_at']
        read_only_fields = ['id', 'created_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


class UserShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'email']


class EventSerializer(serializers.ModelSerializer):
    participants = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.all()  # Теперь не ограничиваем здесь, валидируем вручную
    )
    participants_detail = UserShortSerializer(source='participants', many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            'id', 'company', 'title', 'datetime', 'participants',
            'participants_detail', 'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'updated_at', 'participants_detail']

    def validate_participants(self, participants):
        """
        Проверяем, что все участники из компании текущего пользователя.
        """
        request = self.context['request']
        company = request.user.company

        for participant in participants:
            if participant.company != company:
                raise serializers.ValidationError(
                    f"Пользователь {participant.email} не принадлежит вашей компании."
                )
        return participants

    def create(self, validated_data):
        participants = validated_data.pop('participants')
        event = Event.objects.create(**validated_data)
        event.participants.set(participants)
        return event

    def update(self, instance, validated_data):
        participants = validated_data.pop('participants', None)
        instance = super().update(instance, validated_data)
        if participants is not None:
            instance.participants.set(participants)
        return instance


class WarehouseSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Warehouse
        fields = ['id', 'name', 'location', 'company', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)


# Сериализатор для модели WarehouseEvent (Складское событие)
class WarehouseEventSerializer(serializers.ModelSerializer):
    STATUS_CHOICES = [
        ('draf', 'Черновик'),
        ('conducted', 'Проведен'),
        ('cancelled', 'Отменен'),
    ]

    participants = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.all()  # Здесь валидируем, что участники принадлежат компании текущего пользователя
    )
    participants_detail = serializers.StringRelatedField(source='participants', many=True, read_only=True)
    
    responsible_person = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)

    class Meta:
        model = WarehouseEvent
        fields = [
            'id', 'warehouse', 'responsible_person', 'status', 'client_name',
            'title', 'description', 'amount', 'event_date', 'participants',
            'participants_detail', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'participants_detail']

    def validate_participants(self, participants):
        """
        Проверяем, что все участники из компании текущего пользователя.
        """
        request = self.context['request']
        company = request.user.company

        for participant in participants:
            if participant.company != company:
                raise serializers.ValidationError(
                    f"Пользователь {participant.email} не принадлежит вашей компании."
                )
        return participants

    def create(self, validated_data):
        participants = validated_data.pop('participants')
        warehouse_event = WarehouseEvent.objects.create(**validated_data)
        warehouse_event.participants.set(participants)
        return warehouse_event

    def update(self, instance, validated_data):
        participants = validated_data.pop('participants', None)
        instance = super().update(instance, validated_data)
        if participants is not None:
            instance.participants.set(participants)
        return instance