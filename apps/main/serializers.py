from rest_framework import serializers
from apps.main.models import Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review, Notification, Event, Warehouse, WarehouseEvent, ProductCategory, ProductBrand, OrderItem, Client, GlobalProduct, CartItem, ClientDeal, Bid, SocialApplications, TransactionRecord, DealInstallment, ContractorWork
from apps.construction.models import Department
from apps.users.models import User, Company
from django.db import transaction
from decimal import Decimal

class SocialApplicationsSerializers(serializers.ModelSerializer):
    class Meta:
        model = SocialApplications
        fields = ['id', 'company', 'text', 'status','created_at']


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

        # company подставляем из связанного заказа
        order = validated_data['order']
        company = order.company

        return OrderItem.objects.create(
            **validated_data,
            company=company,
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
    company = serializers.ReadOnlyField(source="company.id")

    # бренд/категория: только названия на чтение
    brand = serializers.CharField(source="brand.name", read_only=True)
    category = serializers.CharField(source="category.name", read_only=True)

    # ручной ввод названий на запись
    brand_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    category_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    # клиент
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False, allow_null=True
    )
    client_name = serializers.CharField(source="client.full_name", read_only=True)

    # статус (необязательный) + человекочитаемое значение
    status = serializers.ChoiceField(
        choices=Product.Status.choices, required=False, allow_null=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "name", "barcode",
            "brand", "brand_name",
            "category", "category_name",
            "quantity", "price", "purchase_price",
            "status", "status_display",
            "company",
            "client", "client_name",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at",
            "company", "name", "brand", "category", "client_name", "status_display",
        ]
        extra_kwargs = {
            "price": {"required": False, "default": 0},
            "purchase_price": {"required": False, "default": 0},
            "quantity": {"required": False, "default": 0},
        }

    # ---------- helpers ----------
    def _ensure_company_brand(self, company, global_brand):
        if global_brand:
            brand, _ = ProductBrand.objects.get_or_create(company=company, name=global_brand.name)
            return brand
        return None

    def _ensure_company_category(self, company, global_category):
        if global_category:
            category, _ = ProductCategory.objects.get_or_create(company=company, name=global_category.name)
            return category
        return None

    # ---------- validation ----------
    def validate_status(self, value):
        # пустую строку трактуем как None
        if value in ("", None):
            return None
        return value

    def validate_barcode(self, value):
        value = str(value).strip()
        if not value:
            raise serializers.ValidationError("Укажите штрих-код.")
        company = self.context["request"].user.company
        qs = Product.objects.filter(company=company, barcode=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("В вашей компании уже есть товар с таким штрих-кодом.")
        return value

    def validate(self, attrs):
        # клиент (если передан) должен принадлежать компании пользователя
        company = self.context["request"].user.company
        client = attrs.get("client") or (self.instance.client if self.instance else None)
        if client and client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        return attrs

    # ---------- create/update ----------
    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        company = request.user.company
        barcode = validated_data["barcode"]

        # write-only поля
        client = validated_data.pop("client", None)
        brand_name = (validated_data.pop("brand_name", "") or "").strip()
        category_name = (validated_data.pop("category_name", "") or "").strip()

        gp = GlobalProduct.objects.select_related("brand", "category").filter(barcode=barcode).first()
        if not gp:
            raise serializers.ValidationError({
                "barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."
            })

        # приоритет: явные *_name -> иначе копируем из глобальных
        if brand_name:
            brand = ProductBrand.objects.get_or_create(company=company, name=brand_name)[0]
        else:
            brand = self._ensure_company_brand(company, gp.brand)

        if category_name:
            category = ProductCategory.objects.get_or_create(company=company, name=category_name)[0]
        else:
            category = self._ensure_company_category(company, gp.category)

        product = Product.objects.create(
            company=company,
            name=gp.name,
            barcode=gp.barcode,
            brand=brand,
            category=category,
            price=validated_data.get("price", 0),
            purchase_price=validated_data.get("purchase_price", 0),
            quantity=validated_data.get("quantity", 0),
            client=client,
            status=validated_data.get("status", None),   # необязательное
        )
        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        company = self.context["request"].user.company
        brand_name = (validated_data.pop("brand_name", "") or "").strip()
        category_name = (validated_data.pop("category_name", "") or "").strip()

        if brand_name:
            instance.brand, _ = ProductBrand.objects.get_or_create(company=company, name=brand_name)
        if category_name:
            instance.category, _ = ProductCategory.objects.get_or_create(company=company, name=category_name)

        # простые поля; status и client могут быть None
        for field in ("barcode", "quantity", "price", "purchase_price", "client", "status"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        instance.save(update_fields=[
            "brand_id", "category_id", "barcode", "quantity",
            "price", "purchase_price", "client_id", "status", "updated_at"
        ])
        return instance

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
    
    
class DealInstallmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DealInstallment
        fields = ("number", "due_date", "amount", "balance_after", "paid_on")
        read_only_fields = ("number", "due_date", "amount", "balance_after")

class ClientDealSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all(), required=False)
    client_full_name = serializers.CharField(source="client.full_name", read_only=True)

    # было: source="debt_amount"/"monthly_payment"/"remaining_debt" → убрать
    debt_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    monthly_payment = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_debt = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    installments = DealInstallmentSerializer(many=True, read_only=True)

    class Meta:
        model = ClientDeal
        fields = [
            "id", "company", "client", "client_full_name",
            "title", "kind",
            "amount", "prepayment",
            "debt_months", "first_due_date",
            "debt_amount", "monthly_payment", "remaining_debt",
            "installments",
            "note", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "company", "created_at", "updated_at", "client_full_name",
            "debt_amount", "monthly_payment", "remaining_debt", "installments",
        ]
        
    def validate(self, attrs):
        request = self.context["request"]
        company = request.user.company

        client = attrs.get("client") or (self.instance.client if self.instance else None)
        if client and client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})

        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        prepayment = attrs.get("prepayment", getattr(self.instance, "prepayment", None))
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        debt_months = attrs.get("debt_months", getattr(self.instance, "debt_months", None))

        errors = {}
        if amount is not None and amount < 0:
            errors["amount"] = "Сумма не может быть отрицательной."
        if prepayment is not None and prepayment < 0:
            errors["prepayment"] = "Предоплата не может быть отрицательной."
        if amount is not None and prepayment is not None and prepayment > amount:
            errors["prepayment"] = "Предоплата не может превышать сумму договора."

        if kind == ClientDeal.Kind.DEBT:
            debt_amt = (amount or Decimal("0")) - (prepayment or Decimal("0"))
            if debt_amt <= 0:
                errors["prepayment"] = 'Для типа "Долг" сумма договора должна быть больше предоплаты.'
            if not debt_months or debt_months <= 0:
                errors["debt_months"] = "Укажите срок (в месяцах) для рассрочки."
        else:
            # всегда очищаем поля рассрочки при не-debt
            attrs["debt_months"] = None
            attrs["first_due_date"] = None

        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        validated_data["company"] = self.context["request"].user.company
        return super().create(validated_data)
    
class TransactionRecordSerializer(serializers.ModelSerializer):
    # компания только для чтения (id)
    company = serializers.ReadOnlyField(source="company.id")

    # новое поле: отдел
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        required=False,
        allow_null=True
    )
    # опционально удобно отдавать имя отдела
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = TransactionRecord
        fields = [
            "id",
            "company",
            "description",
            "department",
            "department_name",
            "name",
            "amount",
            "status",
            "date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "department_name", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ограничим выбор отделов компанией текущего пользователя
        request = self.context.get("request")
        user = getattr(request, "user", None)
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        if company and "department" in self.fields:
            self.fields["department"].queryset = Department.objects.filter(company=company)

    def validate_department(self, department):
        """
        Защитимся от скрещивания разных компаний.
        """
        if department is None:
            return department
        request = self.context.get("request")
        user = getattr(request, "user", None)
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        if company and department.company_id != company.id:
            raise serializers.ValidationError("Отдел принадлежит другой компании.")
        return department

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        department = validated_data.get("department")

        # Если отдел указан — сверим компанию; если у пользователя компании нет, возьмём из отдела
        if department:
            if company and department.company_id != company.id:
                raise serializers.ValidationError({"department": "Отдел принадлежит другой компании."})
            if not company:
                company = department.company

        if not company:
            raise serializers.ValidationError("Невозможно определить компанию пользователя.")

        validated_data["company"] = company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """
        company остаётся как есть (фиксируется на компанию пользователя по вью/фильтрам).
        При смене department проверяем согласованность.
        """
        validated_data.pop("company", None)

        new_department = validated_data.get("department", getattr(instance, "department", None))
        if new_department:
            # проверим ещё раз на всякий случай
            request = self.context.get("request")
            user = getattr(request, "user", None)
            company = getattr(user, "owned_company", None) or getattr(user, "company", None)
            # если компания пользователя известна — сверим
            if company and new_department.company_id != company.id:
                raise serializers.ValidationError({"department": "Отдел принадлежит другой компании."})
            # если по каким-то причинам экземпляр без company (не должно случиться) — подставим
            if not instance.company_id:
                validated_data["company"] = new_department.company

        return super().update(instance, validated_data)
    
    
class ContractorWorkSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")

    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    department_name = serializers.CharField(source="department.name", read_only=True)

    duration_days = serializers.IntegerField(read_only=True)  # @property из модели

    class Meta:
        model = ContractorWork
        fields = [
            "id", "company",
            "title",
            "contractor_name", "contractor_phone",
            "contractor_entity_type", "contractor_entity_name",
            "amount",
            "department", "department_name",
            "start_date", "end_date",
            "planned_completion_date", "work_calendar_date",
            "description",
            "duration_days",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "department_name", "duration_days", "created_at", "updated_at"]

    def validate(self, attrs):
        company = self.context["request"].user.company
        dep = attrs.get("department") or (self.instance.department if self.instance else None)
        if dep and dep.company_id != company.id:
            raise serializers.ValidationError({"department": "Отдел принадлежит другой компании."})

        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        planned = attrs.get("planned_completion_date", getattr(self.instance, "planned_completion_date", None))

        errors = {}
        if start and end and end < start:
            errors["end_date"] = "Дата окончания не может быть раньше даты начала."
        if planned and start and planned < start:
            errors["planned_completion_date"] = "Плановая дата завершения не может быть раньше начала."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        validated_data["company"] = self.context["request"].user.company
        return super().create(validated_data)