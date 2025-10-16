# apps/main/serializers.py
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from django.db.models import Q
from decimal import Decimal

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review,
    Notification, Event, Warehouse, WarehouseEvent, ProductCategory, ProductBrand,
    OrderItem, Client, GlobalProduct, CartItem, ClientDeal, Bid, SocialApplications,
    TransactionRecord, DealInstallment, ContractorWork, Debt, DebtPayment,
    ObjectItem, ObjectSale, ObjectSaleItem, ItemMake, ManufactureSubreal, Acceptance,
    ReturnFromAgent
)
from apps.construction.models import Department
from apps.consalting.models import ServicesConsalting
from apps.users.models import User, Company


# ===========================
# Общие утилиты (STRICT branch)
# ===========================
def _company_from_ctx(serializer: serializers.Serializer):
    req = serializer.context.get("request")
    return getattr(getattr(req, "user", None), "company", None) if req else None

def _active_branch(serializer: serializers.Serializer):
    """
    Активный филиал:
      1) user.primary_branch() / user.primary_branch
      2) request.branch
      3) None (глобальный контекст)
    """
    req = serializer.context.get("request")
    if not req:
        return None
    user = getattr(req, "user", None)

    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val:
                return val
        except Exception:
            pass
    if primary:
        return primary
    if hasattr(req, "branch"):
        return req.branch
    return None

def _restrict_pk_queryset_strict(field, base_qs, company, branch):
    """
    Сужение queryset для PK-полей по строгому правилу:
      - по company (если у модели есть поле company)
      - по branch (если у модели есть поле branch):
           если branch задан → branch == active_branch
           иначе → branch IS NULL (только глобальные)
    """
    if not field or base_qs is None or company is None:
        return
    qs = base_qs
    if hasattr(base_qs.model, "company"):
        qs = qs.filter(company=company)
    if hasattr(base_qs.model, "branch"):
        qs = qs.filter(branch=branch) if branch else qs.filter(branch__isnull=True)
    field.queryset = qs


# ===========================
# Общий миксин: company/branch
# ===========================
class CompanyBranchReadOnlyMixin:
    """
    Делает company/branch read-only наружу и проставляет их из контекста на create/update.
    Правило:
      - есть активный филиал → branch = этот филиал
      - нет филиала → branch = NULL (глобально)
    """
    def _user(self):
        req = self.context.get("request")
        return getattr(req, "user", None) if req else None

    def _user_company(self):
        u = self._user()
        return getattr(u, "company", None) or getattr(u, "owned_company", None)

    def _auto_branch(self):
        return _active_branch(self)

    def create(self, validated_data):
        user = self._user()
        if user and getattr(user, "company_id", None):
            validated_data.setdefault("company", user.company)
        if "branch" in getattr(self.Meta, "fields", []):
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        user = self._user()
        if user and getattr(user, "company_id", None):
            validated_data["company"] = user.company
        if "branch" in getattr(self.Meta, "fields", []):
            validated_data["branch"] = self._auto_branch()
        return super().update(instance, validated_data)


# ===========================
# Простые справочники без company/branch
# ===========================
class SocialApplicationsSerializers(serializers.ModelSerializer):
    class Meta:
        model = SocialApplications
        fields = ['id', 'company', 'text', 'status', 'created_at']


class BidSerializers(serializers.ModelSerializer):
    class Meta:
        model = Bid
        fields = ['id', 'full_name', 'phone', 'text', 'status', 'created_at']


# ===========================
# ProductCategory / ProductBrand (STRICT)
# ===========================
class ProductCategorySerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=ProductCategory.objects.all(),
        allow_null=True,
        required=False
    )

    class Meta:
        model = ProductCategory
        fields = ['id', 'company', 'branch', 'name', 'parent']
        read_only_fields = ['id', 'company', 'branch']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("parent"), ProductCategory.objects.all(), comp, br)


class ProductBrandSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=ProductBrand.objects.all(),
        allow_null=True,
        required=False
    )

    class Meta:
        model = ProductBrand
        fields = ['id', 'company', 'branch', 'name', 'parent']
        read_only_fields = ['id', 'company', 'branch']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("parent"), ProductBrand.objects.all(), comp, br)


# ===========================
# Contact / Pipeline / Deal / Task
# ===========================
class ContactSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    owner = serializers.ReadOnlyField(source='owner.id')
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    class Meta:
        model = Contact
        fields = [
            'id', 'company', 'branch',
            'name', 'email', 'phone', 'address', 'client_company',
            'notes', 'department', 'created_at', 'updated_at',
            'owner'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner', 'company', 'branch']

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)


class PipelineSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    owner = serializers.ReadOnlyField(source='owner.id')
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    class Meta:
        model = Pipeline
        fields = ['id', 'company', 'branch', 'name', 'stages', 'created_at', 'updated_at', 'owner']
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner', 'company', 'branch']

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)


class DealSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    pipeline = serializers.PrimaryKeyRelatedField(queryset=Pipeline.objects.all())
    contact = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.all())
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)

    class Meta:
        model = Deal
        fields = [
            'id', 'company', 'branch',
            'title', 'value', 'status',
            'pipeline', 'stage', 'contact', 'assigned_to',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company', 'branch']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("pipeline"), Pipeline.objects.all(), comp, br)
        _restrict_pk_queryset_strict(self.fields.get("contact"), Contact.objects.all(), comp, br)
        if comp and self.fields.get("assigned_to"):
            self.fields["assigned_to"].queryset = User.objects.filter(company=comp)

    def validate(self, attrs):
        user = self.context['request'].user
        company = user.company
        branch = self._auto_branch()

        pipeline = attrs.get('pipeline') or getattr(self.instance, "pipeline", None)
        contact = attrs.get('contact') or getattr(self.instance, "contact", None)
        assigned_to = attrs.get('assigned_to') or getattr(self.instance, "assigned_to", None)

        if pipeline and pipeline.company != company:
            raise serializers.ValidationError({"pipeline": "Воронка принадлежит другой компании."})
        if contact and contact.company != company:
            raise serializers.ValidationError({"contact": "Контакт принадлежит другой компании."})
        if assigned_to and assigned_to.company != company:
            raise serializers.ValidationError({"assigned_to": "Ответственный не из вашей компании."})

        # STRICT: филиал должен совпадать с активным, либо быть NULL, если филиал не выбран
        if branch is not None:
            if pipeline and pipeline.branch_id != branch.id:
                raise serializers.ValidationError({"pipeline": "Воронка другого филиала."})
            if contact and contact.branch_id != branch.id:
                raise serializers.ValidationError({"contact": "Контакт другого филиала."})
        else:
            if pipeline and pipeline.branch_id is not None:
                raise serializers.ValidationError({"pipeline": "Воронка не должна быть привязана к филиалу."})
            if contact and contact.branch_id is not None:
                raise serializers.ValidationError({"contact": "Контакт не должен быть привязан к филиалу."})
        return attrs


class TaskSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)
    deal = serializers.PrimaryKeyRelatedField(queryset=Deal.objects.all(), allow_null=True, required=False)

    class Meta:
        model = Task
        fields = [
            'id', 'company', 'branch',
            'title', 'description', 'due_date', 'status',
            'assigned_to', 'deal',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company', 'branch']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        if comp and self.fields.get("assigned_to"):
            self.fields["assigned_to"].queryset = User.objects.filter(company=comp)
        _restrict_pk_queryset_strict(self.fields.get("deal"), Deal.objects.all(), comp, br)


# ===========================
# Integration / Analytics
# ===========================
class IntegrationSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    class Meta:
        model = Integration
        fields = ['id', 'company', 'branch', 'type', 'config', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'company', 'branch']


class AnalyticsSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    class Meta:
        model = Analytics
        fields = ['id', 'company', 'branch', 'type', 'data', 'created_at']
        read_only_fields = ['id', 'created_at', 'company', 'branch']


# ===========================
# Order / OrderItem
# ===========================
class OrderItemSerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'price', 'total']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req = self.context.get("request")
        user = getattr(req, "user", None) if req else None
        comp = getattr(user, "company", None)
        br = _active_branch(self)
        if comp and self.fields.get("product"):
            qs = Product.objects.filter(company=comp)
            # STRICT
            qs = qs.filter(branch=br) if br else qs.filter(branch__isnull=True)
            self.fields["product"].queryset = qs

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

        product.quantity -= quantity
        product.save()

        order = validated_data['order']
        company = order.company
        branch = getattr(order, "branch", None)

        return OrderItem.objects.create(
            **validated_data,
            company=company,
            branch=branch,
            price=price,
            total=total
        )


class OrderSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')
    items = OrderItemSerializer(many=True)

    total = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'company', 'branch',
            'order_number', 'customer_name', 'date_ordered',
            'status', 'phone', 'department',
            'created_at', 'updated_at',
            'items', 'total', 'total_quantity'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company', 'branch', 'total', 'total_quantity']

    def get_total(self, obj):
        return obj.total

    def get_total_quantity(self, obj):
        return sum((it.quantity for it in obj.items.all()), 0)

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        order = super().create(validated_data)
        for item_data in items_data:
            item_data['order'] = order
            OrderItemSerializer(context=self.context).create(item_data)
        return order


# ===========================
# ItemMake / Product
# ===========================
class ItemMakeNestedSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemMake
        fields = ["id", "name", "price", "unit", "quantity"]


class ProductSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    brand = serializers.CharField(source="brand.name", read_only=True)
    category = serializers.CharField(source="category.name", read_only=True)

    created_by = serializers.ReadOnlyField(source="created_by.id")
    created_by_name = serializers.SerializerMethodField(read_only=True)

    item_make = ItemMakeNestedSerializer(many=True, read_only=True)
    item_make_ids = serializers.PrimaryKeyRelatedField(
        queryset=ItemMake.objects.all(), many=True, write_only=True, required=False
    )

    brand_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    category_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False, allow_null=True
    )
    client_name = serializers.CharField(source="client.full_name", read_only=True)

    status = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    date = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "company", "branch",
            "name", "barcode",
            "brand", "brand_name",
            "category", "category_name",
            "item_make", "item_make_ids",
            "quantity", "price", "purchase_price",
            "status", "status_display",
            "client", "client_name", "date",
            "created_by", "created_by_name",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at",
            "company", "branch",
            "name", "brand", "category",
            "client_name", "status_display", "item_make", "date",
            "created_by", "created_by_name",
        ]
        extra_kwargs = {
            "price": {"required": False, "default": 0},
            "purchase_price": {"required": False, "default": 0},
            "quantity": {"required": False, "default": 0},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("item_make_ids"), ItemMake.objects.all(), comp, br)
        _restrict_pk_queryset_strict(self.fields.get("client"), Client.objects.all(), comp, br)

    def get_created_by_name(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return None
        return (getattr(u, "get_full_name", lambda: "")()
                or getattr(u, "email", None)
                or getattr(u, "username", None))

    def get_date(self, obj):
        dt = getattr(obj, "date", None)
        if not dt:
            return None
        try:
            from django.utils import timezone as dj_tz
            if dj_tz.is_aware(dt):
                dt = dj_tz.localtime(dt)
        except Exception:
            pass
        return dt.date().isoformat()

    def _ensure_company_brand(self, company, brand):
        if brand is None:
            return None
        return ProductBrand.objects.get_or_create(company=company, name=brand.name)[0]

    def _ensure_company_category(self, company, category):
        if category is None:
            return None
        return ProductCategory.objects.get_or_create(company=company, name=category.name)[0]

    @transaction.atomic
    def create(self, validated_data):
        item_make_data = validated_data.pop("item_make_ids", [])
        company = self._user_company()
        branch = self._auto_branch()

        client = validated_data.pop("client", None)
        brand_name = (validated_data.pop("brand_name", "") or "").strip()
        category_name = (validated_data.pop("category_name", "") or "").strip()
        status_value = validated_data.pop("status", None)

        date_value = timezone.now()

        barcode = validated_data.get("barcode")
        gp = GlobalProduct.objects.select_related("brand", "category").filter(barcode=barcode).first()
        if not gp:
            raise serializers.ValidationError({
                "barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."
            })

        brand = (ProductBrand.objects.get_or_create(company=company, name=brand_name)[0]
                 if brand_name else self._ensure_company_brand(company, gp.brand))
        category = (ProductCategory.objects.get_or_create(company=company, name=category_name)[0]
                    if category_name else self._ensure_company_category(company, gp.category))

        product = Product.objects.create(
            company=company,
            branch=branch,  # STRICT
            name=gp.name,
            barcode=gp.barcode,
            brand=brand,
            category=category,
            price=validated_data.get("price", 0),
            purchase_price=validated_data.get("purchase_price", 0),
            quantity=validated_data.get("quantity", 0),
            client=client,
            status=status_value,
            date=date_value,
            created_by=self._user(),
        )

        if item_make_data:
            product.item_make.set(item_make_data)

        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        company = self._user_company()
        branch = self._auto_branch()
        brand_name = (validated_data.pop("brand_name", "") or "").strip()
        category_name = (validated_data.pop("category_name", "") or "").strip()

        if brand_name:
            instance.brand, _ = ProductBrand.objects.get_or_create(company=company, name=brand_name)
        if category_name:
            instance.category, _ = ProductCategory.objects.get_or_create(company=company, name=category_name)

        item_make_data = validated_data.pop("item_make_ids", None)
        if item_make_data is not None:
            instance.item_make.set(item_make_data)

        # STRICT: фиксируем branch из контекста
        instance.branch = branch

        for field in ("barcode", "quantity", "price", "purchase_price", "client", "status"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        instance.save()
        return instance


# ===========================
# Review / Notification
# ===========================
class ReviewSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.id')
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    class Meta:
        model = Review
        fields = ['id', 'company', 'branch', 'user', 'rating', 'comment', 'created_at', 'updated_at']
        read_only_fields = ['id', 'company', 'branch', 'user', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['user'] = self._user()
        return super().create(validated_data)


class NotificationSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    class Meta:
        model = Notification
        fields = ['id', 'company', 'branch', 'message', 'is_read', 'created_at']
        read_only_fields = ['id', 'company', 'branch', 'created_at']

    def create(self, validated_data):
        user = self._user()
        if user:
            validated_data.setdefault("user", user)
        return super().create(validated_data)


# ===========================
# Users short
# ===========================
class UserShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'email']


# ===========================
# Event (STRICT branch только по company для участников)
# ===========================
class EventSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    participants = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.all()
    )
    participants_detail = UserShortSerializer(source='participants', many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            'id', 'company', 'branch',
            'title', 'datetime', 'participants',
            'participants_detail', 'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'updated_at', 'participants_detail']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        if comp and self.fields.get("participants"):
            self.fields["participants"].queryset = User.objects.filter(company=comp)

    def validate_participants(self, participants):
        company = self._user_company()
        for participant in participants:
            if participant.company != company:
                raise serializers.ValidationError(
                    f"Пользователь {participant.email} не принадлежит вашей компании."
                )
        return participants

    def create(self, validated_data):
        participants = validated_data.pop('participants')
        event = super().create(validated_data)
        event.participants.set(participants)
        return event

    def update(self, instance, validated_data):
        participants = validated_data.pop('participants', None)
        instance = super().update(instance, validated_data)
        if participants is not None:
            instance.participants.set(participants)
        return instance


# ===========================
# Warehouse / WarehouseEvent
# ===========================
class WarehouseSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    class Meta:
        model = Warehouse
        fields = ['id', 'company', 'branch', 'name', 'location', 'created_at', 'updated_at']
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'updated_at']


class WarehouseEventSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    participants = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.all()
    )
    participants_detail = serializers.StringRelatedField(source='participants', many=True, read_only=True)
    responsible_person = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True)
    warehouse = serializers.PrimaryKeyRelatedField(queryset=Warehouse.objects.all())

    class Meta:
        model = WarehouseEvent
        fields = [
            'id', 'company', 'branch',
            'warehouse', 'responsible_person', 'status', 'client_name',
            'title', 'description', 'amount', 'event_date', 'participants',
            'participants_detail', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'updated_at', 'participants_detail']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        if comp:
            self.fields["participants"].queryset = User.objects.filter(company=comp)
            _restrict_pk_queryset_strict(self.fields.get("warehouse"), Warehouse.objects.all(), comp, br)
            if self.fields.get("responsible_person"):
                self.fields["responsible_person"].queryset = User.objects.filter(company=comp)

    def validate_participants(self, participants):
        company = self._user_company()
        for participant in participants:
            if participant.company != company:
                raise serializers.ValidationError(
                    f"Пользователь {participant.email} не принадлежит вашей компании."
                )
        return participants

    def create(self, validated_data):
        participants = validated_data.pop('participants')
        we = super().create(validated_data)
        we.participants.set(participants)
        return we

    def update(self, instance, validated_data):
        participants = validated_data.pop('participants', None)
        instance = super().update(instance, validated_data)
        if participants is not None:
            instance.participants.set(participants)
        return instance


# ===========================
# Client / ClientDeal / DealInstallment
# ===========================
class ClientSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    branch = serializers.ReadOnlyField(source='branch.id')

    salesperson = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), required=False, allow_null=True
    )
    service = serializers.PrimaryKeyRelatedField(
        queryset=ServicesConsalting.objects.all(), required=False, allow_null=True
    )
    salesperson_display = serializers.SerializerMethodField(read_only=True)
    service_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Client
        fields = [
            'id', 'company', 'branch',
            'type', 'full_name', 'phone', 'email', 'date', 'status',
            'llc', 'inn', 'okpo', 'score', 'bik', 'address',
            'salesperson', 'salesperson_display',
            'service', 'service_display',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'branch', 'created_at', 'updated_at',
                            'salesperson_display', 'service_display']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        if comp and self.fields.get("salesperson"):
            self.fields["salesperson"].queryset = User.objects.filter(company=comp)

    def get_salesperson_display(self, obj):
        if obj.salesperson:
            return f"{obj.salesperson.first_name} {obj.salesperson.last_name}"
        return None

    def get_service_display(self, obj):
        if obj.service:
            return obj.service.name
        return None


class DealInstallmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DealInstallment
        fields = ("number", "due_date", "amount", "balance_after", "paid_on")
        read_only_fields = ("number", "due_date", "amount", "balance_after")


class ClientDealSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all(), required=False)
    client_full_name = serializers.CharField(source="client.full_name", read_only=True)

    debt_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    monthly_payment = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_debt = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    installments = DealInstallmentSerializer(many=True, read_only=True)

    class Meta:
        model = ClientDeal
        fields = [
            "id", "company", "branch",
            "client", "client_full_name",
            "title", "kind",
            "amount", "prepayment",
            "debt_months", "first_due_date",
            "debt_amount", "monthly_payment", "remaining_debt",
            "installments",
            "note", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "company", "branch", "created_at", "updated_at", "client_full_name",
            "debt_amount", "monthly_payment", "remaining_debt", "installments",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("client"), Client.objects.all(), comp, br)

    def validate(self, attrs):
        request = self.context["request"]
        company = request.user.company
        branch = self._auto_branch()

        client = attrs.get("client") or (self.instance.client if self.instance else None)
        if client and client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})

        # STRICT branch
        if branch is not None:
            if client and client.branch_id != branch.id:
                raise serializers.ValidationError({"client": "Клиент другого филиала."})
        else:
            if client and client.branch_id is not None:
                raise serializers.ValidationError({"client": "Глобальный режим: клиент должен быть без филиала."})

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
            attrs["debt_months"] = None
            attrs["first_due_date"] = None

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


# ===========================
# TransactionRecord
# ===========================
class TransactionRecordSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        required=False,
        allow_null=True
    )
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = TransactionRecord
        fields = [
            "id", "company", "branch",
            "description",
            "department", "department_name",
            "name", "amount", "status", "date",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "department_name", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req = self.context.get("request")
        user = getattr(req, "user", None)
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        branch = _active_branch(self)
        if company and "department" in self.fields:
            # STRICT по Department: только этот филиал ИЛИ глобальные при отсутствии филиала у пользователя
            qs = Department.objects.filter(company=company)
            qs = qs.filter(branch=branch) if branch else qs.filter(branch__isnull=True)
            self.fields["department"].queryset = qs

    def validate_department(self, department):
        if department is None:
            return department
        company = self._user_company()
        branch = self._auto_branch()
        if company and department.company_id != company.id:
            raise serializers.ValidationError("Отдел принадлежит другой компании.")
        if branch is not None:
            if department.branch_id != branch.id:
                raise serializers.ValidationError("Отдел другого филиала.")
        else:
            if department.branch_id is not None:
                raise serializers.ValidationError("Глобальный режим: отдел должен быть без филиала.")
        return department


# ===========================
# ContractorWork
# ===========================
class ContractorWorkSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    department_name = serializers.CharField(source="department.name", read_only=True)

    duration_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = ContractorWork
        fields = [
            "id", "company", "branch",
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
            "status"
        ]
        read_only_fields = ["id", "company", "branch", "department_name", "duration_days", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        if comp:
            qs = Department.objects.filter(company=comp)
            qs = qs.filter(branch=br) if br else qs.filter(branch__isnull=True)
            self.fields["department"].queryset = qs

    def validate(self, attrs):
        company = self._user_company()
        branch = self._auto_branch()
        dep = attrs.get("department") or (self.instance.department if self.instance else None)
        if dep and dep.company_id != company.id:
            raise serializers.ValidationError({"department": "Отдел принадлежит другой компании."})
        # STRICT branch
        if branch is not None:
            if dep and dep.branch_id != branch.id:
                raise serializers.ValidationError({"department": "Отдел другого филиала."})
        else:
            if dep and dep.branch_id is not None:
                raise serializers.ValidationError({"department": "Глобальный режим: отдел должен быть без филиала."})

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


# ===========================
# Debt / DebtPayment
# ===========================
class DebtSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    paid_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Debt
        fields = [
            "id", "company", "branch",
            "name", "phone", "amount", "due_date",
            "paid_total", "balance",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "paid_total", "balance", "created_at", "updated_at"]

    def validate_phone(self, value):
        value = value.strip()
        company = self._user_company()
        qs = Debt.objects.filter(company=company, phone=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("В вашей компании уже есть долг с таким телефоном.")
        return value


class DebtPaymentSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    debt = serializers.ReadOnlyField(source="debt.id")

    class Meta:
        model = DebtPayment
        fields = ["id", "company", "debt", "amount", "paid_at", "note", "created_at"]
        read_only_fields = ["id", "company", "debt", "created_at"]


# ===========================
# ObjectItem / ObjectSale / ObjectSaleItem
# ===========================
class ObjectItemSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = ObjectItem
        fields = ["id", "company", "branch", "name", "description", "price", "date", "quantity", "created_at", "updated_at"]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at"]


class ObjectSaleItemSerializer(serializers.ModelSerializer):
    object_name = serializers.ReadOnlyField(source="name_snapshot")

    class Meta:
        model = ObjectSaleItem
        fields = ["id", "object_item", "object_name", "unit_price", "quantity"]


class ObjectSaleSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    client_name = serializers.ReadOnlyField(source="client.full_name")
    items = ObjectSaleItemSerializer(many=True, read_only=True)

    class Meta:
        model = ObjectSale
        fields = ["id", "company", "branch", "client", "client_name", "status", "sold_at", "note", "subtotal", "items", "created_at"]
        read_only_fields = ["id", "company", "branch", "client_name", "subtotal", "created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        if comp and self.fields.get("client"):
            _restrict_pk_queryset_strict(self.fields["client"], Client.objects.all(), comp, br)

    def validate_client(self, client):
        company = self._user_company()
        branch = self._auto_branch()
        if client.company_id != company.id:
            raise serializers.ValidationError("Клиент принадлежит другой компании.")
        # STRICT branch
        if branch is not None:
            if client.branch_id != branch.id:
                raise serializers.ValidationError("Клиент другого филиала.")
        else:
            if client.branch_id is not None:
                raise serializers.ValidationError("Глобальный режим: клиент должен быть без филиала.")
        return client


# ===========================
# BulkIds — утилита
# ===========================
class BulkIdsSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.UUIDField(format='hex_verbose'),
        allow_empty=False
    )
    soft = serializers.BooleanField(required=False, default=False)
    require_all = serializers.BooleanField(required=False, default=False)


# ===========================
# ItemMake — плоский список + связанные продукты (read-only)
# ===========================
class ProductNestedSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "barcode", "quantity", "price"]


class ItemMakeSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    products = ProductNestedSerializer(many=True, read_only=True)

    class Meta:
        model = ItemMake
        fields = [
            "id", "company", "branch",
            "name", "price", "unit", "quantity",
            "products",
            "created_at", "updated_at",
        ]


# ===========================
# Subreal / Acceptance / ReturnFromAgent
# ===========================
class ManufactureSubrealSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch  = serializers.ReadOnlyField(source="branch.id")

    product_name = serializers.ReadOnlyField(source="product.name")

    # Поля агента: имя, фамилия и номер машины
    agent_first_name  = serializers.ReadOnlyField(source="agent.first_name")
    agent_last_name   = serializers.ReadOnlyField(source="agent.last_name")
    agent_track_number = serializers.ReadOnlyField(source="agent.track_number")

    # вычисляемые
    qty_remaining = serializers.ReadOnlyField()
    qty_on_agent  = serializers.ReadOnlyField()

    # ⬇️ НОВОЕ поле — “пилорама” на уровне КОНКРЕТНОЙ передачи
    is_sawmill = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = ManufactureSubreal
        fields = [
            "id", "company", "branch",
            "user",
            "agent", "agent_first_name", "agent_last_name", "agent_track_number",
            "product", "product_name",
            "is_sawmill",                 # ← добавили
            "qty_transferred", "qty_accepted", "qty_returned",
            "qty_remaining", "qty_on_agent",
            "status", "created_at",
        ]
        read_only_fields = [
            "id", "company", "branch", "user",
            "agent_first_name", "agent_last_name", "agent_track_number",
            "product_name",
            "qty_remaining", "qty_on_agent",
            "status", "created_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br   = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("product"), Product.objects.all(), comp, br)
        if comp and self.fields.get("agent"):
            self.fields["agent"].queryset = User.objects.filter(company=comp)

    def validate(self, attrs):
        company = self._user_company()
        branch  = self._auto_branch()

        product = attrs.get("product")
        if product is not None and product.company_id != getattr(company, "id", None):
            raise serializers.ValidationError({"product": "Товар другой компании."})

        # строгая проверка филиала (если у вас режим “строгих филиалов”)
        if branch is not None and product is not None and product.branch_id not in (None, branch.id):
            raise serializers.ValidationError({"product": "Товар другого филиала."})

        agent = attrs.get("agent")
        if agent is not None:
            agent_company_id = getattr(agent, "company_id", None)
            if agent_company_id and agent_company_id != getattr(company, "id", None):
                raise serializers.ValidationError({"agent": "Агент другой компании."})

        # qty_transferred > 0 — обычно требование для создания передачи
        qty = attrs.get("qty_transferred")
        if qty is not None and qty < 1:
            raise serializers.ValidationError({"qty_transferred": "Минимум 1."})

        return attrs

    def create(self, validated_data):
        # сервер выставляет связь с компанией/филиалом/пользователем
        validated_data["user"]    = self._user()
        validated_data["company"] = self._user_company()
        validated_data["branch"]  = self._auto_branch()
        return super().create(validated_data)


class AcceptanceCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Acceptance
        fields = ["subreal", "qty"]
        extra_kwargs = {"subreal": {"queryset": ManufactureSubreal.objects.all()}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req  = self.context.get("request")
        comp = getattr(getattr(req, "user", None), "company", None)
        br   = _active_branch(self)
        if comp and self.fields.get("subreal"):
            qs = ManufactureSubreal.objects.filter(company=comp)
            qs = qs.filter(branch=br) if br else qs.filter(branch__isnull=True)
            self.fields["subreal"].queryset = qs

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        user_company_id = getattr(user, "company_id", None)

        sub = attrs.get("subreal")
        qty = attrs.get("qty")
        if not sub:
            raise serializers.ValidationError({"subreal": "Обязательное поле."})
        if qty is None or qty < 1:
            raise serializers.ValidationError({"qty": "Минимум 1."})
        if qty > sub.qty_remaining:
            raise serializers.ValidationError({"qty": f"Доступно к приёму {sub.qty_remaining}."})
        if user_company_id and sub.company_id != user_company_id:
            raise serializers.ValidationError({"subreal": "Передача из другой компании."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company_id = getattr(user, "company_id", None)
        if not company_id:
            raise serializers.ValidationError({"company": "У пользователя не задана компания."})
        validated_data["company_id"] = company_id
        validated_data["accepted_by"] = user
        return super().create(validated_data)


class AcceptanceReadSerializer(serializers.ModelSerializer):
    subreal_id = serializers.UUIDField(source="subreal.id", read_only=True)
    product = serializers.CharField(source="subreal.product.name", read_only=True)
    agent = serializers.SerializerMethodField()
    accepted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Acceptance
        fields = [
            "id", "company", "subreal_id", "product", "agent",
            "accepted_by", "accepted_by_name", "qty", "accepted_at",
        ]

    def get_agent(self, obj):
        a = obj.subreal.agent
        fn = getattr(a, "get_full_name", lambda: "")() or None
        return fn or getattr(a, "username", str(a))

    def get_accepted_by_name(self, obj):
        fn = getattr(obj.accepted_by, "get_full_name", lambda: "")() or None
        return fn or getattr(obj.accepted_by, "username", str(obj.accepted_by))


class ReturnCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReturnFromAgent
        fields = ["subreal", "qty"]
        extra_kwargs = {"subreal": {"queryset": ManufactureSubreal.objects.all()}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req  = self.context.get("request")
        comp = getattr(getattr(req, "user", None), "company", None)
        br   = _active_branch(self)
        if comp and self.fields.get("subreal"):
            qs = ManufactureSubreal.objects.filter(company=comp)
            qs = qs.filter(branch=br) if br else qs.filter(branch__isnull=True)
            self.fields["subreal"].queryset = qs

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        user_company_id = getattr(user, "company_id", None)

        sub = attrs.get("subreal")
        qty = attrs.get("qty")
        if not sub:
            raise serializers.ValidationError({"subreal": "Обязательное поле."})
        if qty is None or qty < 1:
            raise serializers.ValidationError({"qty": "Минимум 1."})
        if qty > sub.qty_on_agent:
            raise serializers.ValidationError({"qty": f"На руках {sub.qty_on_agent}."})
        if user_company_id and sub.company_id != user_company_id:
            raise serializers.ValidationError({"subreal": "Передача из другой компании."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company_id = getattr(user, "company_id", None)
        if not company_id:
            raise serializers.ValidationError({"company": "У пользователя не задана компания."})
        validated_data["company_id"] = company_id
        validated_data["returned_by"] = user
        return super().create(validated_data)


class ReturnReadSerializer(serializers.ModelSerializer):
    subreal_id = serializers.UUIDField(source="subreal.id", read_only=True)
    product = serializers.CharField(source="subreal.product.name", read_only=True)
    agent = serializers.SerializerMethodField()
    returned_by_name = serializers.SerializerMethodField()
    accepted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ReturnFromAgent
        fields = [
            "id", "company", "subreal_id", "product", "agent",
            "qty", "status",
            "returned_by", "returned_by_name",
            "accepted_by", "accepted_by_name",
            "returned_at", "accepted_at",
        ]

    def get_agent(self, obj):
        a = obj.subreal.agent
        fn = getattr(a, "get_full_name", lambda: "")() or None
        return fn or getattr(a, "username", str(a))

    def get_returned_by_name(self, obj):
        fn = getattr(obj.returned_by, "get_full_name", lambda: "")() or None
        return fn or getattr(obj.returned_by, "username", str(obj.returned_by))

    def get_accepted_by_name(self, obj):
        u = obj.accepted_by
        if not u:
            return None
        fn = getattr(u, "get_full_name", lambda: "")() or None
        return fn or getattr(u, "username", str(u))


class ReturnApproveSerializer(serializers.Serializer):
    def save(self, **kwargs):
        ret: ReturnFromAgent = self.context["return_obj"]
        user = self.context["request"].user
        ret.accept(by_user=user)
        return ret


# ===========================
# BULK выдача агенту
# ===========================
class BulkSubrealItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    qty_transferred = serializers.IntegerField(min_value=1)
    # ⬇️ НОВОЕ поле для item в bulk-выдаче
    is_sawmill = serializers.BooleanField(required=False, default=False)


class BulkSubrealCreateSerializer(serializers.Serializer):
    agent = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    items = BulkSubrealItemSerializer(many=True, allow_empty=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req  = self.context.get("request")
        comp = getattr(getattr(req, "user", None), "company", None)
        br   = _active_branch(self)
        if comp:
            self.fields["agent"].queryset = User.objects.filter(company=comp)
            prod_qs = Product.objects.filter(company=comp)
            # STRICT
            prod_qs = prod_qs.filter(branch=br) if br else prod_qs.filter(branch__isnull=True)
            self.fields["items"].child.fields["product"].queryset = prod_qs

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company_id = getattr(user, "company_id", None)
        if not company_id:
            raise serializers.ValidationError("У пользователя не задана компания.")

        agent = attrs["agent"]
        agent_company_id = getattr(agent, "company_id", None)
        if agent_company_id and agent_company_id != company_id:
            raise serializers.ValidationError({"agent": "Агент принадлежит другой компании."})

        for i, item in enumerate(attrs["items"]):
            prod = item["product"]
            if prod.company_id != company_id:
                raise serializers.ValidationError({"items": {i: {"product": "Товар другой компании."}}})

        # сжимаем дубликаты по product, суммируя qty и OR по is_sawmill
        merged = {}
        for item in attrs["items"]:
            key = item["product"].pk
            prev = merged.get(key)
            if prev is None:
                merged[key] = {
                    "product": item["product"],
                    "qty_transferred": int(item["qty_transferred"]),
                    "is_sawmill": bool(item.get("is_sawmill", False)),
                }
            else:
                prev["qty_transferred"] += int(item["qty_transferred"])
                # если хотя бы один из дублей is_sawmill=True — считаем TRUE
                prev["is_sawmill"] = prev["is_sawmill"] or bool(item.get("is_sawmill", False))

        if len(merged) != len(attrs["items"]):
            attrs["items"] = list(merged.values())

        return attrs


# ===========================
# Аггрегированные ответы для агента
# ===========================
class AgentSubrealSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    qty_transferred = serializers.IntegerField()
    qty_accepted = serializers.IntegerField()
    qty_returned = serializers.IntegerField()


class AgentProductOnHandSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    product_name = serializers.CharField()
    qty_on_hand = serializers.IntegerField()
    last_movement_at = serializers.DateTimeField(allow_null=True)
    subreals = AgentSubrealSerializer(many=True)
