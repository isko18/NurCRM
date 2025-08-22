from rest_framework import serializers
from apps.main.models import Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review, Notification, Event, Warehouse, WarehouseEvent, ProductCategory, ProductBrand, OrderItem, Client, GlobalProduct, CartItem, ClientDeal, Bid

from apps.users.models import User, Company
from django.db import transaction

class BidSerializers(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ['id', 'full_name', 'phone', 'text', 'status','created_at']

    
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

    # бренд и категория только для чтения (локальные, маппятся из глобальной базы)
    brand = serializers.PrimaryKeyRelatedField(read_only=True)
    category = serializers.PrimaryKeyRelatedField(read_only=True)

    # ручное создание/редактирование — только для сценария без barcode
    brand_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    category_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'barcode',
            'brand', 'brand_name',
            'category', 'category_name',
            'quantity', 'price', 'company',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at',
            'company', 'name', 'brand', 'category'
        ]
        extra_kwargs = {
            'price': {'required': False, 'default': 0},
            'quantity': {'required': False, 'default': 0},
        }

    def validate_barcode(self, value):
        value = str(value).strip()
        if not value:
            raise serializers.ValidationError("Укажите штрих-код.")
        company = self.context['request'].user.company
        if Product.objects.filter(company=company, barcode=value).exists():
            raise serializers.ValidationError("В вашей компании уже есть товар с таким штрих-кодом.")
        return value

    def _ensure_company_brand(self, company, global_brand):
        """Создаёт/находит локальный бренд по глобальному бренду."""
        if global_brand:
            return ProductBrand.objects.get_or_create(company=company, name=global_brand.name)[0]
        return None

    def _ensure_company_category(self, company, global_category):
        """Создаёт/находит локальную категорию по глобальной категории."""
        if global_category:
            return ProductCategory.objects.get_or_create(company=company, name=global_category.name)[0]
        return None

    @transaction.atomic
    def create(self, validated_data):
        request = self.context['request']
        company = request.user.company
        barcode = validated_data['barcode']

        # 1. Ищем товар в глобальной базе
        gp = GlobalProduct.objects.select_related('brand', 'category').filter(barcode=barcode).first()
        if not gp:
            raise serializers.ValidationError({
                "barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."
            })

        # 2. Маппим бренд и категорию в локальную базу компании
        brand = self._ensure_company_brand(company, gp.brand)
        category = self._ensure_company_category(company, gp.category)

        # 3. Создаём товар компании
        return Product.objects.create(
            company=company,
            name=gp.name,
            barcode=gp.barcode,
            brand=brand,
            category=category,
            price=validated_data.get('price', 0),
            quantity=validated_data.get('quantity', 0),
        )
        
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
    
    
class ClientSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Client
        fields = [
            'id', 'type','full_name', 'phone', 'email', 'date', 'status',
            'company', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)
    
    
class ClientDealSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    # КЛЮЧЕВОЕ: client не обязателен — при nested-роуте его задаёт view
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(),
        required=False
    )
    client_full_name = serializers.CharField(source='client.full_name', read_only=True)

    class Meta:
        model = ClientDeal
        fields = [
            'id', 'company', 'client', 'client_full_name',
            'title', 'kind', 'amount', 'note',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'updated_at', 'client_full_name']

    def validate(self, attrs):
        # Если client придёт в теле запроса (плоский роут) — проверим компанию.
        client = attrs.get('client')
        if client:
            company = self.context['request'].user.company
            if client.company_id != company.id:
                raise serializers.ValidationError({'client': 'Клиент принадлежит другой компании.'})
        return attrs

    def create(self, validated_data):
        # company всегда от пользователя; client может быть не в validated_data —
        # его добавит view через serializer.save(client=...)
        validated_data['company'] = self.context['request'].user.company
        return super().create(validated_data)