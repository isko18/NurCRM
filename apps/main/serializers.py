from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from django.db.models import Q, Sum, Value as V, Prefetch
from django.db.models.functions import Coalesce
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict
from datetime import date as _date, datetime as _datetime
from django.utils import timezone as dj_tz
from django.utils.dateparse import parse_datetime, parse_date

from apps.main.models import (
    Contact, Pipeline, Deal, Task, Integration, Analytics, Order, Product, Review,
    Notification, Event, Warehouse, WarehouseEvent, ProductCategory, ProductBrand,
    OrderItem, Client, GlobalProduct, CartItem, ClientDeal, Bid, SocialApplications,
    TransactionRecord, DealInstallment, ContractorWork, Debt, DebtPayment,
    Sale,
    ObjectItem, ObjectSale, ObjectSaleItem, ItemMake, ManufactureSubreal, Acceptance,
    ReturnFromAgent, ProductImage, PromoRule, AgentRequestCart, AgentRequestItem, ProductPackage, ProductCharacteristics, DealPayment, AgentSaleAllocation
)

from apps.consalting.models import ServicesConsalting
from apps.users.models import User, Company, Branch


# ===========================
# Общие утилиты (STRICT branch)
# ===========================
def _company_from_ctx(serializer: serializers.Serializer):
    """
    Компания пользователя:
      - для суперюзера -> None (без ограничения по компании);
      - иначе owned_company или company.
    """
    req = serializer.context.get("request")
    user = getattr(req, "user", None) if req else None
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return None
    return getattr(user, "owned_company", None) or getattr(user, "company", None)


def _active_branch(serializer: serializers.Serializer):
    """
    Активный филиал:

      1) "жёстко" назначенный филиал пользователя
         (user.primary_branch() / user.primary_branch / user.branch / request.branch),
         если он принадлежит компании

      2) ?branch=<uuid> в запросе (если принадлежит компании и нет жёсткого филиала)

      3) None — нет филиала, работаем по всей компании (без фильтра по branch)
    """
    req = serializer.context.get("request")
    if not req:
        return None

    user = getattr(req, "user", None)
    company = getattr(user, "owned_company", None) or getattr(user, "company", None)
    company_id = getattr(company, "id", None)

    if not user or not getattr(user, "is_authenticated", False) or not company_id:
        return None

    # ----- 1. Жёстко назначенный филиал -----
    # 1a) user.primary_branch() как метод
    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                setattr(req, "branch", val)
                return val
        except Exception:
            pass

    # 1b) user.primary_branch как атрибут
    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        setattr(req, "branch", primary)
        return primary

    # 1c) user.branch
    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            setattr(req, "branch", b)
            return b

    # 1d) request.branch (если уже проставила middleware)
    if hasattr(req, "branch"):
        b = getattr(req, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    # ----- 2. Разрешаем ?branch=... ТОЛЬКО если нет жёсткого филиала -----
    branch_id = None
    if hasattr(req, "query_params"):
        branch_id = req.query_params.get("branch")
    elif hasattr(req, "GET"):
        branch_id = req.GET.get("branch")

    if branch_id and branch_id.strip():
        try:
            from apps.users.models import Branch  # на случай круговой импорта
            br = Branch.objects.get(id=branch_id, company_id=company_id)
            setattr(req, "branch", br)
            return br
        except (Branch.DoesNotExist, ValueError):
            pass

    # ----- 3. Глобальный режим по компании -----
    return None


def _restrict_pk_queryset_strict(field, base_qs, company, branch):
    """
    Было: если branch None -> показываем только branch__isnull=True.

    Теперь:
      - фильтруем по company (если есть поле company),
      - по branch фильтруем ТОЛЬКО если branch не None;
      - если branch is None -> не фильтруем по branch вообще.
    """
    if not field or base_qs is None or company is None:
        return
    qs = base_qs
    if hasattr(base_qs.model, "company"):
        qs = qs.filter(company=company)
    if hasattr(base_qs.model, "branch") and branch is not None:
        qs = qs.filter(branch=branch)
    field.queryset = qs


class ProductImageReadSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "image_url", "alt", "is_primary", "created_at"]
        read_only_fields = fields  # всё только на чтение тут

    def get_image_url(self, obj):
        """
        Делаем абсолютный URL, чтобы фронту было удобно.
        """
        request = self.context.get("request")
        if not obj.image:
            return None
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url


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
        company = self._user_company()
        if company:
            validated_data.setdefault("company", company)
        if "branch" in getattr(self.Meta, "fields", []):
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        company = self._user_company()
        if company:
            validated_data["company"] = company
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
        ref_name = "MainContactSerializer"
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
        ref_name = "MainDealSerializer"
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

        # STRICT: филиал должен совпадать с активным, если он есть
        if branch is not None:
            if pipeline and pipeline.branch_id != branch.id:
                raise serializers.ValidationError({"pipeline": "Воронка другого филиала."})
            if contact and contact.branch_id != branch.id:
                raise serializers.ValidationError({"contact": "Контакт другого филиала."})
        # если branch is None — не проверяем филиал (видим всю компанию)
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
        comp = (
            getattr(user, "company", None)
            or getattr(user, "owned_company", None)
            or getattr(getattr(user, "branch", None), "company", None)
        )
        br = _active_branch(self)
        if comp and self.fields.get("product"):
            qs = Product.objects.filter(company=comp)
            if br is not None:
                qs = qs.filter(branch=br)
            # если br is None — оставляем все товары компании, независимо от филиала
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


class ProductImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "image", "image_url", "alt", "is_primary", "created_at"]
        read_only_fields = ["id", "image_url", "created_at"]

    def get_image_url(self, obj):
        req = self.context.get("request")
        if obj.image and hasattr(obj.image, "url"):
            return req.build_absolute_uri(obj.image.url) if req else obj.image.url
        return None
    
class ProductCharacteristicsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCharacteristics
        fields = [
            "id",
            "height_cm",
            "width_cm",
            "depth_cm",
            "factual_weight_kg",
            "description",
        ]
        read_only_fields = ["id"]


class ProductPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductPackage
        fields = [
            "id",
            "name",
            "quantity_in_package",
            "unit",
        ]
        read_only_fields = ["id"]


class ProductSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    # ====== базовые поля компании/филиала ======
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    # ====== бренд / категория ======
    brand = serializers.CharField(source="brand.name", read_only=True)
    category = serializers.CharField(source="category.name", read_only=True)

    brand_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    category_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    # ====== кто создал ======
    created_by = serializers.ReadOnlyField(source="created_by.id")
    created_by_name = serializers.SerializerMethodField(read_only=True)

    # ====== единицы товара ======
    item_make = ItemMakeNestedSerializer(many=True, read_only=True)
    item_make_ids = serializers.PrimaryKeyRelatedField(
        queryset=ItemMake.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    # ====== клиент ======
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(),
        required=False,
        allow_null=True,
    )
    client_name = serializers.CharField(source="client.full_name", read_only=True)

    # ====== статус ======
    status = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    # ====== дата (как и раньше) ======
    date = serializers.SerializerMethodField(read_only=True)

    # ====== картинки ======
    images = ProductImageSerializer(many=True, read_only=True)

    stock = serializers.BooleanField(required=False)

    # ====== новые поля модели ======
    code = serializers.CharField(read_only=True)  # генерится в модели
    article = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    unit = serializers.CharField(required=False, allow_blank=True)
    is_weight = serializers.BooleanField(required=False)

    # allow_null=True чтобы PATCH мог "очищать" значения
    purchase_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    # model.Product.markup_percent имеет decimal_places=4
    markup_percent = serializers.DecimalField(max_digits=12, decimal_places=4, required=False, allow_null=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    discount_percent = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)

    country = serializers.CharField(required=False, allow_blank=True)
    expiration_date = serializers.DateField(required=False, allow_null=True)

    # ==== ПЛУ ====  — ТОЛЬКО READ-ONLY!
    plu = serializers.IntegerField(read_only=True)

    # ==== ДАННЫЕ С ВЕСОВ ====
    weight_kg = serializers.SerializerMethodField(read_only=True)
    total_price = serializers.SerializerMethodField(read_only=True)

    # ==== связанные модели ====
    characteristics = ProductCharacteristicsSerializer(read_only=True)

    # READ-ONLY — то, что отдаём фронту
    packages = ProductPackageSerializer(many=True, read_only=True)
    # WRITE-ONLY — то, что принимаем с фронта
    packages_input = ProductPackageSerializer(many=True, write_only=True, required=False)

    kind = serializers.ChoiceField(
        choices=Product.Kind.choices,
        required=False,
        default=Product.Kind.PRODUCT,
    )

    class Meta:
        model = Product
        fields = [
            "id", "company", "branch",
            "kind",
            "code", "article",
            "name", "description", "barcode",
            "brand", "brand_name",
            "category", "category_name",
            "unit", "is_weight",
            "item_make", "item_make_ids",
            "quantity",
            "purchase_price",
            "markup_percent",
            "price",
            "discount_percent",
            "plu",
            "country",
            "expiration_date",
            "status", "status_display",
            "client", "client_name",
            "stock", "date",
            "created_by", "created_by_name",
            "created_at", "updated_at",
            "images",
            "characteristics",
            "packages",
            "packages_input",
            "weight_kg",
            "total_price",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at",
            "company", "branch",
            "brand", "category",
            "client_name", "status_display",
            "item_make", "date",
            "created_by", "created_by_name",
            "images",
            "code",
            "characteristics",
            "packages",
            "plu",
            "weight_kg", "total_price",
        ]
        extra_kwargs = {
            "kind": {"required": False, "default": Product.Kind.PRODUCT},
            "purchase_price": {"required": False, "default": 0, "allow_null": True},
            "markup_percent": {"required": False, "default": 0, "allow_null": True},
            "discount_percent": {"required": False, "default": 0, "allow_null": True},
            "quantity": {"required": False, "default": 0},
            "unit": {"required": False, "default": "шт."},
            "is_weight": {"required": False, "default": False},
            "price": {"required": False, "allow_null": True},
            "description": {"required": False, "allow_blank": True, "allow_null": True},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("item_make_ids"), ItemMake.objects.all(), comp, br)
        _restrict_pk_queryset_strict(self.fields.get("client"), Client.objects.all(), comp, br)

    # ==== ДАННЫЕ С ВЕСОВ ====

    def get_weight_kg(self, obj):
        scale_data = self.context.get("scale_data")
        if not scale_data:
            return None
        if not obj.is_weight:
            return None
        return scale_data.get("weight_kg")

    def get_total_price(self, obj):
        scale_data = self.context.get("scale_data")
        if not obj.is_weight or not scale_data:
            return obj.price

        weight_kg = scale_data.get("weight_kg")
        if not weight_kg:
            return obj.price

        total = Decimal(obj.price) * Decimal(str(weight_kg))
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---- helpers ----

    def get_created_by_name(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return None
        return (
            getattr(u, "get_full_name", lambda: "")()
            or getattr(u, "email", None)
            or getattr(u, "username", None)
        )

    def get_date(self, obj):
        val = getattr(obj, "date", None)
        if not val:
            return None

        from datetime import datetime as _datetime, date as _date

        if isinstance(val, _datetime):
            try:
                if dj_tz.is_naive(val):
                    val = dj_tz.make_aware(val)
                val = dj_tz.localtime(val)
            except Exception:
                pass
            return val.date().isoformat()

        if isinstance(val, _date):
            return val.isoformat()

        try:
            dt = parse_datetime(str(val))
            if dt:
                if dj_tz.is_naive(dt):
                    dt = dj_tz.make_aware(dt)
                return dj_tz.localtime(dt).date().isoformat()
            d = parse_date(str(val))
            if d:
                return d.isoformat()
        except Exception:
            pass

        return None

    @staticmethod
    def _normalize_status(raw):
        if raw in (None, "", "null"):
            return None
        v = str(raw).strip().lower()
        mapping = {
            "pending": "pending", "ожидание": "pending",
            "accepted": "accepted", "принят": "accepted",
            "rejected": "rejected", "отказ": "rejected",
        }
        return mapping.get(v, v)

    def _ensure_company_brand(self, company, brand):
        if brand is None:
            return None
        return ProductBrand.objects.get_or_create(company=company, name=brand.name)[0]

    def _ensure_company_category(self, company, category):
        if category is None:
            return None
        return ProductCategory.objects.get_or_create(company=company, name=category.name)[0]

    # ====== PRICE <-> MARKUP helper ======

    _Q2 = Decimal("0.01")
    _Q4 = Decimal("0.0001")

    @staticmethod
    def _to_dec(v, default=Decimal("0")):
        if v in (None, "", "null"):
            return default
        return Decimal(str(v))

    @classmethod
    def _calc_price(cls, purchase_price: Decimal, markup_percent: Decimal) -> Decimal:
        price = purchase_price * (Decimal("1") + markup_percent / Decimal("100"))
        return price.quantize(cls._Q2, rounding=ROUND_HALF_UP)

    @classmethod
    def _calc_markup(cls, purchase_price: Decimal, price: Decimal) -> Decimal:
        if purchase_price <= 0:
            return Decimal("0.00")
        mp = (price / purchase_price - Decimal("1")) * Decimal("100")
        # Храним/считаем наценку точнее (4 знака), чтобы обратный пересчёт цены
        # не давал «копейки» из-за округления процента.
        return mp.quantize(cls._Q4, rounding=ROUND_HALF_UP)

    # ====== SAFE DECIMAL OUTPUT ======
    @staticmethod
    def _safe_decimal(val, quant: Decimal):
        """
        Возвращает декремент для сериализации или None, если значение некорректное
        (NaN/inf/строка/пусто). Делает quantize с указанной точностью.
        """
        if val in (None, "", "null"):
            return None
        try:
            dec = Decimal(val)
            if not dec.is_finite():
                return None
            return dec.quantize(quant, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _sanitize_decimal_fields(self, instance):
        """Чистим потенциально битые Decimal, чтобы DRF не падал на quantize()."""
        for field, quant in (
            ("quantity", self._Q2),
            ("purchase_price", self._Q2),
            ("markup_percent", self._Q4),
            ("price", self._Q2),
            ("discount_percent", self._Q2),
        ):
            safe = self._safe_decimal(getattr(instance, field, None), quant)
            setattr(instance, field, safe)

        # характеристики товара (OneToOne)
        chars = getattr(instance, "characteristics", None)
        if chars:
            for field, quant in (
                ("height_cm", Decimal("0.01")),
                ("width_cm", Decimal("0.01")),
                ("depth_cm", Decimal("0.01")),
                ("factual_weight_kg", Decimal("0.001")),
            ):
                safe = self._safe_decimal(getattr(chars, field, None), quant)
                setattr(chars, field, safe)

        # пакеты (prefetch_related уже есть, будет без доп. запросов)
        try:
            packages = list(instance.packages.all())
        except Exception:
            packages = []
        for pkg in packages:
            safe = self._safe_decimal(getattr(pkg, "quantity_in_package", None), Decimal("0.001"))
            setattr(pkg, "quantity_in_package", safe)

    def to_representation(self, instance):
        # чистим проблемные Decimal перед сериализацией, чтобы избежать InvalidOperation
        self._sanitize_decimal_fields(instance)
        try:
            return super().to_representation(instance)
        except InvalidOperation:
            # на всякий случай повторно чистим и сериализуем
            self._sanitize_decimal_fields(instance)
            return super().to_representation(instance)

    # ==== CREATE / UPDATE ====

    @transaction.atomic
    def create(self, validated_data):
        item_make_data = validated_data.pop("item_make_ids", [])
        packages_data = validated_data.pop("packages_input", [])

        company = self._user_company()
        branch = self._auto_branch()

        client = validated_data.pop("client", None)
        brand_name = (validated_data.pop("brand_name", "") or "").strip()
        category_name = (validated_data.pop("category_name", "") or "").strip()
        status_value = self._normalize_status(validated_data.pop("status", None))

        article = (validated_data.pop("article", "") or "").strip()
        unit = (validated_data.pop("unit", None) or "шт.").strip()
        is_weight = validated_data.pop("is_weight", False)

        description = (validated_data.pop("description", "") or "").strip()

        # ===== цены: двусторонняя логика =====
        purchase_price = self._to_dec(validated_data.pop("purchase_price", Decimal("0")))
        markup_percent = self._to_dec(validated_data.pop("markup_percent", Decimal("0")))
        price_in = validated_data.pop("price", None)

        if price_in not in (None, ""):
            price = self._to_dec(price_in)
            markup_percent = self._calc_markup(purchase_price, price)
        else:
            price = self._calc_price(purchase_price, markup_percent)

        discount_percent = self._to_dec(validated_data.pop("discount_percent", Decimal("0")))

        country = (validated_data.pop("country", "") or "").strip()
        expiration_date = validated_data.pop("expiration_date", None)

        date_value = dj_tz.now()

        barcode = validated_data.get("barcode")
        gp = (
            GlobalProduct.objects
            .select_related("brand", "category")
            .filter(barcode=barcode)
            .first()
        )
        if not gp:
            raise serializers.ValidationError({
                "barcode": "Товар с таким штрих-кодом не найден в глобальной базе. Заполните карточку вручную."
            })

        brand = (
            ProductBrand.objects.get_or_create(company=company, name=brand_name)[0]
            if brand_name else self._ensure_company_brand(company, gp.brand)
        )
        category = (
            ProductCategory.objects.get_or_create(company=company, name=category_name)[0]
            if category_name else self._ensure_company_category(company, gp.category)
        )

        product = Product.objects.create(
            company=company,
            branch=branch,
            kind=validated_data.get("kind", Product.Kind.PRODUCT),

            name=gp.name,
            barcode=gp.barcode,

            brand=brand,
            category=category,

            article=article,
            description=description,
            unit=unit,
            is_weight=is_weight,

            purchase_price=purchase_price,
            markup_percent=markup_percent,
            price=price,
            discount_percent=discount_percent,

            quantity=validated_data.get("quantity", 0),

            country=country,
            expiration_date=expiration_date,

            client=client,
            status=status_value,
            date=date_value,
            created_by=self._user(),
            stock=validated_data.get("stock", False),
        )

        if item_make_data:
            product.item_make.set(item_make_data)

        for pkg in packages_data:
            ProductPackage.objects.create(
                product=product,
                name=(pkg.get("name") or "").strip(),
                quantity_in_package=pkg.get("quantity_in_package"),
                unit=(pkg.get("unit") or "").strip(),
            )

        return product

    @transaction.atomic
    def update(self, instance, validated_data):
        company = self._user_company()
        branch = self._auto_branch()

        packages_data = validated_data.pop("packages_input", None)

        # бренд/категория через *_name
        brand_name = (validated_data.pop("brand_name", "") or "").strip()
        category_name = (validated_data.pop("category_name", "") or "").strip()
        if brand_name:
            instance.brand, _ = ProductBrand.objects.get_or_create(company=company, name=brand_name)
        if category_name:
            instance.category, _ = ProductCategory.objects.get_or_create(company=company, name=category_name)

        # item_make
        item_make_data = validated_data.pop("item_make_ids", None)
        if item_make_data is not None:
            instance.item_make.set(item_make_data)

        # если у тебя бизнес-логика такая — оставляем:
        instance.branch = branch

        # ===== цены: двусторонняя логика =====
        has_purchase = "purchase_price" in validated_data
        has_markup = "markup_percent" in validated_data
        has_price = "price" in validated_data

        purchase_price = self._to_dec(validated_data.get("purchase_price", instance.purchase_price))
        markup_percent = self._to_dec(validated_data.get("markup_percent", instance.markup_percent))
        price = self._to_dec(validated_data.get("price", instance.price))

        # 1) пришёл price -> считаем markup
        if has_price and validated_data.get("price") not in (None, ""):
            price = self._to_dec(validated_data["price"])
            markup_percent = self._calc_markup(purchase_price, price)

        # 2) иначе пришёл markup -> считаем price
        elif has_markup and validated_data.get("markup_percent") not in (None, ""):
            markup_percent = self._to_dec(validated_data["markup_percent"])
            price = self._calc_price(purchase_price, markup_percent)

        # 3) иначе пришёл только purchase_price -> пересчитываем разумно
        elif has_purchase:
            if Decimal(instance.markup_percent or 0) != Decimal("0"):
                price = self._calc_price(purchase_price, markup_percent)
            else:
                markup_percent = self._calc_markup(purchase_price, price)

        instance.purchase_price = purchase_price
        instance.markup_percent = markup_percent
        instance.price = price

        # ===== PATCH-friendly обновление остальных полей =====
        updatable_fields = (
            "name",
            "barcode",
            "description",
            "quantity",
            "discount_percent",
            "article",
            "unit",
            "is_weight",
            "country",
            "expiration_date",
            "client",
            "kind",
        )
        for field in updatable_fields:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        if "stock" in validated_data:
            instance.stock = validated_data["stock"]

        if "status" in validated_data:
            instance.status = self._normalize_status(validated_data["status"])

        if instance.is_weight is False:
            instance.plu = None

        # дата (опционально)
        raw_date = None
        if isinstance(getattr(self, "initial_data", None), dict):
            raw_date = self.initial_data.get("date") or self.initial_data.get("date_raw")

        if raw_date not in (None, ""):
            dt = parse_datetime(str(raw_date))
            if dt:
                if dj_tz.is_naive(dt):
                    dt = dj_tz.make_aware(dt)
                instance.date = dt
            else:
                d = parse_date(str(raw_date))
                if d:
                    from datetime import datetime as _datetime
                    instance.date = dj_tz.make_aware(_datetime(d.year, d.month, d.day))
                else:
                    raise serializers.ValidationError({
                        "date": "Неверный формат даты. Используйте YYYY-MM-DD или ISO datetime."
                    })

        instance.save()

        # PACKAGES перезаписываем ТОЛЬКО если реально пришли в PATCH
        if packages_data is not None:
            instance.packages.all().delete()
            for pkg in packages_data:
                ProductPackage.objects.create(
                    product=instance,
                    name=(pkg.get("name") or "").strip(),
                    quantity_in_package=pkg.get("quantity_in_package"),
                    unit=(pkg.get("unit") or "").strip(),
                )

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
        read_only_fields = [
            'id', 'company', 'branch', 'created_at', 'updated_at',
            'salesperson_display', 'service_display'
        ]

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
class DealInstallmentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    deal = serializers.ReadOnlyField(source="deal.id")

    # ВАЖНО: source НЕ НУЖЕН, потому что имя поля совпадает с property модели
    remaining_for_period = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = DealInstallment
        fields = (
            "id",
            "company",
            "branch",
            "deal",
            "number",
            "due_date",
            "amount",
            "balance_after",
            "paid_on",
            "paid_amount",
            "remaining_for_period",
        )
        read_only_fields = fields


# ===== Payments =====
class DealPaymentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    deal = serializers.ReadOnlyField(source="deal.id")
    installment = serializers.ReadOnlyField(source="installment.id")
    installment_number = serializers.IntegerField(source="installment.number", read_only=True)

    created_by = serializers.ReadOnlyField(source="created_by.id")

    class Meta:
        model = DealPayment
        fields = (
            "id",
            "company",
            "branch",
            "deal",
            "installment",
            "installment_number",
            "kind",
            "amount",
            "paid_date",
            "idempotency_key",
            "created_by",
            "note",
            "created_at",
        )
        read_only_fields = fields


# ===== Deals =====
class ClientDealSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    # client может прийти из URL /clients/<client_id>/deals/ -> required=False
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all(), required=False)
    client_full_name = serializers.CharField(source="client.full_name", read_only=True)

    debt_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    monthly_payment = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_debt = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    installments = DealInstallmentSerializer(many=True, read_only=True)
    payments = DealPaymentSerializer(many=True, read_only=True)

    auto_schedule = serializers.BooleanField(required=False)

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
            "payments",
            "auto_schedule",
            "note", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "company", "branch",
            "created_at", "updated_at",
            "client_full_name",
            "debt_amount", "monthly_payment", "remaining_debt",
            "installments", "payments",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        comp = self._user_company()
        br = self._auto_branch()

        _restrict_pk_queryset_strict(
            self.fields.get("client"),
            Client.objects.all(),
            comp,
            br,
        )

    def validate(self, attrs):
        request = self.context["request"]
        company = request.user.company
        branch = self._auto_branch()
        instance = getattr(self, "instance", None)

        # client обязателен, если не передан через URL
        view = self.context.get("view")
        client_id_from_url = getattr(view, "kwargs", {}).get("client_id") if view else None

        client = attrs.get("client") or (instance.client if instance else None)

        if not client and not client_id_from_url:
            raise serializers.ValidationError({"client": "Укажите клиента."})

        # scope проверки клиента (компания/филиал)
        if client:
            if client.company_id != company.id:
                raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})

            # клиент может быть общий (branch=None)
            if branch is not None and client.branch_id not in (None, branch.id):
                raise serializers.ValidationError({"client": "Клиент другого филиала."})

        # прод: если уже есть платежи — условия сделки нельзя менять
        if instance and instance.pk and instance.payments.exists():
            allowed = {"title", "note"}  # максимально безопасно
            illegal = [k for k in attrs.keys() if k not in allowed]
            if illegal:
                raise serializers.ValidationError({
                    "detail": (
                        "Нельзя менять тип/суммы/срок/дату/график: по сделке уже есть платежи. "
                        "Разрешено менять только title и note."
                    )
                })

        amount = attrs.get("amount", getattr(instance, "amount", None))
        prepayment = attrs.get("prepayment", getattr(instance, "prepayment", None))
        kind = attrs.get("kind", getattr(instance, "kind", None))
        debt_months = attrs.get("debt_months", getattr(instance, "debt_months", None))

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
            # не долг -> чистим всё, как в модели
            attrs["debt_months"] = None
            attrs["first_due_date"] = None
            attrs["auto_schedule"] = False

        if errors:
            raise serializers.ValidationError(errors)

        return attrs


# ===== Inputs for pay/refund endpoints =====
class DealPayInputSerializer(serializers.Serializer):
    installment_id = serializers.UUIDField(required=False)  # если нет — возьмём первый не полностью оплаченный
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    date = serializers.DateField(required=False)
    idempotency_key = serializers.UUIDField(required=True)
    note = serializers.CharField(required=False, allow_blank=True)


class DealRefundInputSerializer(serializers.Serializer):
    installment_id = serializers.UUIDField(required=False)  # если нет — возьмём последний оплаченный/частично
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)  # если нет — вернём всё
    date = serializers.DateField(required=False)
    idempotency_key = serializers.UUIDField(required=True)
    note = serializers.CharField(required=False, allow_blank=True)

# ===========================
# TransactionRecord
# ===========================
class TransactionRecordSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = TransactionRecord
        fields = [
            "id", "company", "branch",
            "description",
            "name", "amount", "status", "date",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at"]

# ===========================
# ContractorWork
# ===========================
class ContractorWorkSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    duration_days = serializers.IntegerField(read_only=True)

    class Meta:
        model = ContractorWork
        fields = [
            "id", "company", "branch",
            "title",
            "contractor_name", "contractor_phone",
            "contractor_entity_type", "contractor_entity_name",
            "amount",
            "start_date", "end_date",
            "planned_completion_date", "work_calendar_date",
            "description",
            "duration_days",
            "created_at", "updated_at",
            "status",
        ]
        read_only_fields = [
            "id", "company", "branch",
            "duration_days",
            "created_at", "updated_at",
        ]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        planned = attrs.get(
            "planned_completion_date",
            getattr(self.instance, "planned_completion_date", None),
        )

        errors = {}
        if start and end and end < start:
            errors["end_date"] = "Дата окончания не может быть раньше даты начала."
        if planned and start and planned < start:
            errors["planned_completion_date"] = "Плановая дата завершения не может быть раньше начала."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

def normalize_phone(v: str) -> str:
    v = (v or "").strip()
    # минимум: убрать пробелы
    v = v.replace(" ", "")
    return v


# ===========================
# Debt / DebtPayment
# ===========================
class DebtSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    paid_total = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Debt
        fields = [
            "id", "company", "branch",
            "name", "phone", "amount", "due_date",
            "paid_total", "balance",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "paid_total", "balance", "created_at", "updated_at"]

    def get_paid_total(self, obj) -> Decimal:
        # берём из queryset-аннотации если она есть
        v = getattr(obj, "paid_total_db", None)
        if v is None:
            v = obj.paid_total  # fallback (но это будет отдельный запрос)
        return Decimal(v).quantize(Decimal("0.01"))

    def get_balance(self, obj) -> Decimal:
        v = getattr(obj, "balance_db", None)
        if v is None:
            v = obj.balance
        return Decimal(v).quantize(Decimal("0.01"))

    def validate_phone(self, value: str) -> str:
        value = normalize_phone(value)

        # важно: company у тебя read-only, значит берём компанию из миксина/контекста
        company = self._user_company()  # предполагаю, что CompanyBranchReadOnlyMixin это даёт
        qs = Debt.objects.filter(company=company, phone=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError("В вашей компании уже есть долг с таким телефоном.")

        return value


class DebtPaymentSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    debt = serializers.ReadOnlyField(source="debt.id")

    class Meta:
        model = DebtPayment
        fields = ["id", "company", "branch", "debt", "amount", "paid_at", "note", "created_at"]
        read_only_fields = ["id", "company", "branch", "debt", "created_at"]

    def create(self, validated_data):
        """
        Правильнее создавать оплату через debt.add_payment(), чтобы:
        - сработала блокировка/транзакция (если ты её сделал в модели)
        - вся бизнес-логика была в одном месте
        """
        debt = self.context.get("debt")
        if debt is None:
            raise serializers.ValidationError({"debt": "Debt обязателен (передай его в serializer context)."})

        amount = validated_data["amount"]
        paid_at = validated_data.get("paid_at")
        note = validated_data.get("note", "")

        return debt.add_payment(amount=amount, paid_at=paid_at, note=note)

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
        if branch is not None and client.branch_id != branch.id:
            raise serializers.ValidationError("Клиент другого филиала.")
        # branch None — клиент из любого филиала компании
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


# Лёгкий сериализатор для списка товаров (минимальный набор полей)
class ProductListSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "code", "article", "price", "quantity", "brand", "category", "image_url"]

    def get_image_url(self, obj):
        imgs = getattr(obj, "images", None)
        if not imgs:
            return None
        try:
            first = imgs.all()[0] if hasattr(imgs, "all") else imgs[0]
        except Exception:
            first = None
        if not first or not getattr(first, "image", None):
            return None
        req = self.context.get("request")
        try:
            return req.build_absolute_uri(first.image.url) if req else first.image.url
        except Exception:
            return None


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
    branch = serializers.ReadOnlyField(source="branch.id")

    product_name = serializers.ReadOnlyField(source="product.name")

    # Поля агента: имя, фамилия и номер машины
    agent_first_name = serializers.ReadOnlyField(source="agent.first_name")
    agent_last_name = serializers.ReadOnlyField(source="agent.last_name")
    agent_track_number = serializers.ReadOnlyField(source="agent.track_number")

    # вычисляемые — делаем через SerializerMethodField, чтобы не падать на None
    qty_remaining = serializers.SerializerMethodField()
    qty_on_agent = serializers.SerializerMethodField()

    # “пилорама” на уровне конкретной передачи (разрешим только при create)
    is_sawmill = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = ManufactureSubreal
        fields = [
            "id", "company", "branch",
            "user",
            "agent", "agent_first_name", "agent_last_name", "agent_track_number",
            "product", "product_name",
            "is_sawmill",
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

    # ---- computed getters ----
    def get_qty_remaining(self, obj) -> int:
        return int(getattr(obj, "qty_remaining", 0) or 0)

    def get_qty_on_agent(self, obj) -> int:
        return int(getattr(obj, "qty_on_agent", 0) or 0)

    # ---- init: ограничим queryset-ы по компании/филиалу ----
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("product"), Product.objects.all(), comp, br)
        if comp and self.fields.get("agent"):
            self.fields["agent"].queryset = User.objects.filter(company=comp)

    # ---- validation ----
    def validate(self, attrs):
        company = self._user_company()
        branch = self._auto_branch()

        # Для create — product обязателен
        if self.instance is None and attrs.get("product") is None:
            raise serializers.ValidationError({"product": "Обязательное поле."})

        product = attrs.get("product")
        if product is not None and product.company_id != getattr(company, "id", None):
            raise serializers.ValidationError({"product": "Товар другой компании."})

        # строгая проверка филиала: при наличии активного филиала товар либо глобальный, либо этого филиала
        if branch is not None and product is not None and product.branch_id not in (None, branch.id):
            raise serializers.ValidationError({"product": "Товар другого филиала."})

        agent = attrs.get("agent")
        if agent is not None:
            agent_company_id = getattr(agent, "company_id", None)
            if agent_company_id and agent_company_id != getattr(company, "id", None):
                raise serializers.ValidationError({"agent": "Агент другой компании."})

        # qty_transferred обязателен и >= 1 при создании
        qty = attrs.get("qty_transferred")
        if self.instance is None and qty is None:
            raise serializers.ValidationError({"qty_transferred": "Обязательное поле."})
        if qty is not None and qty < 1:
            raise serializers.ValidationError({"qty_transferred": "Минимум 1."})

        return attrs

    def create(self, validated_data):
        # сервер выставляет связь с компанией/филиалом/пользователем
        validated_data["user"] = self._user()
        validated_data["company"] = self._user_company()
        validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # запрещаем менять флаг после создания
        validated_data.pop("is_sawmill", None)
        return super().update(instance, validated_data)


class AcceptanceCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Acceptance
        fields = ["subreal", "qty"]
        extra_kwargs = {"subreal": {"queryset": ManufactureSubreal.objects.all()}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req = self.context.get("request")
        comp = getattr(getattr(req, "user", None), "company", None)
        br = _active_branch(self)
        if comp and self.fields.get("subreal"):
            qs = ManufactureSubreal.objects.filter(company=comp)
            if br is not None:
                qs = qs.filter(branch=br)
            # если br None — все передачи компании
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

    @staticmethod
    def _full_or_username(user):
        if not user:
            return None
        fn = getattr(user, "get_full_name", lambda: "")() or None
        return fn or getattr(user, "username", str(user))

    def get_agent(self, obj):
        return self._full_or_username(getattr(obj.subreal, "agent", None))

    def get_accepted_by_name(self, obj):
        return self._full_or_username(getattr(obj, "accepted_by", None))


class ReturnCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReturnFromAgent
        fields = ["subreal", "qty"]
        extra_kwargs = {"subreal": {"queryset": ManufactureSubreal.objects.all()}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req = self.context.get("request")
        comp = getattr(getattr(req, "user", None), "company", None)
        usr = getattr(req, "user", None)
        br = _active_branch(self)
        if comp and self.fields.get("subreal"):
            # Возврат создаёт агент, поэтому ограничиваем subreal-ы его передачами.
            qs = ManufactureSubreal.objects.filter(company=comp)
            if usr and getattr(usr, "id", None):
                qs = qs.filter(agent_id=usr.id)
            if br is not None:
                qs = qs.filter(branch=br)
            # если br None — все передачи компании
            self.fields["subreal"].queryset = qs

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        user_company_id = getattr(user, "company_id", None)
        br = _active_branch(self)

        sub = attrs.get("subreal")
        qty = attrs.get("qty")
        if not sub:
            raise serializers.ValidationError({"subreal": "Обязательное поле."})
        if qty is None or qty < 1:
            raise serializers.ValidationError({"qty": "Минимум 1."})

        # Безопасность: subreal должен быть из компании пользователя
        if user_company_id and sub.company_id != user_company_id:
            raise serializers.ValidationError({"subreal": "Передача из другой компании."})

        # ВАЖНО: "на руках" считается по партиям (subreal) + продажи.
        # Если фронт/пользователь выбрал subreal с нулём, попробуем автоматически подобрать
        # другую партию агента по этому же товару, где есть остаток.
        company_id = user_company_id or sub.company_id
        agent_id = getattr(user, "id", None)
        if not agent_id:
            raise serializers.ValidationError({"returned_by": "Пользователь не определён."})

        candidates = ManufactureSubreal.objects.filter(
            company_id=company_id,
            agent_id=agent_id,
            product_id=sub.product_id,
        )
        if br is not None:
            candidates = candidates.filter(branch=br)

        candidates = candidates.annotate(
            sold_qty=Coalesce(
                Sum(
                    "sale_allocations__qty",
                    filter=Q(
                        sale_allocations__company_id=company_id,
                        sale_allocations__sale__status__in=[Sale.Status.PAID, Sale.Status.DEBT],
                    ),
                ),
                V(0),
            )
        ).annotate(
            reserved_qty=Coalesce(
                Sum(
                    "returns__qty",
                    filter=Q(returns__company_id=company_id, returns__status=ReturnFromAgent.Status.PENDING),
                ),
                V(0),
            )
        ).order_by("-created_at")

        picked = None
        total_on_hand = 0
        for s in candidates:
            accepted = int(s.qty_accepted or 0)
            returned = int(s.qty_returned or 0)
            sold = int(getattr(s, "sold_qty", 0) or 0)
            reserved = int(getattr(s, "reserved_qty", 0) or 0)
            on_hand = max(accepted - returned - sold - reserved, 0)
            if on_hand > 0:
                total_on_hand += on_hand
            if picked is None and on_hand >= int(qty):
                picked = s

        if picked is None:
            raise serializers.ValidationError({"qty": f"На руках {total_on_hand}."})

        # Подменяем subreal на реальную партию, где хватает остатка.
        attrs["subreal"] = picked
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

    @staticmethod
    def _full_or_username(user):
        if not user:
            return None
        fn = getattr(user, "get_full_name", lambda: "")() or None
        return fn or getattr(user, "username", str(user))

    def get_agent(self, obj):
        return self._full_or_username(getattr(obj.subreal, "agent", None))

    def get_returned_by_name(self, obj):
        return self._full_or_username(getattr(obj, "returned_by", None))

    def get_accepted_by_name(self, obj):
        return self._full_or_username(getattr(obj, "accepted_by", None))


class ReturnApproveSerializer(serializers.Serializer):
    def save(self, **kwargs):
        ret: ReturnFromAgent = self.context["return_obj"]
        user = self.context["request"].user
        ret.accept(by_user=user)
        return ret


class ReturnRejectSerializer(serializers.Serializer):
    def save(self, **kwargs):
        ret: ReturnFromAgent = self.context["return_obj"]
        user = self.context["request"].user
        ret.reject(by_user=user)
        return ret


# ===========================
# BULK выдача агенту
# ===========================
class BulkSubrealItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    qty_transferred = serializers.IntegerField(min_value=1)
    is_sawmill = serializers.BooleanField(required=False, default=False)


class BulkSubrealCreateSerializer(serializers.Serializer):
    agent = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    items = BulkSubrealItemSerializer(many=True, allow_empty=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        req = self.context.get("request")
        comp = getattr(getattr(req, "user", None), "company", None)
        br = _active_branch(self)
        if comp:
            self.fields["agent"].queryset = User.objects.filter(company=comp)
            prod_qs = Product.objects.filter(company=comp)
            if br is not None:
                prod_qs = prod_qs.filter(branch=br)
            # если br None — все товары компании
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
        merged: Dict[Any, Dict[str, Any]] = {}
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

        # убираем позиции с нулём и убеждаемся, что что-то осталось
        attrs["items"] = [it for it in merged.values() if int(it["qty_transferred"]) > 0]
        if not attrs["items"]:
            raise serializers.ValidationError({"items": "Нет позиций с количеством > 0."})

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


class AgentInfoSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    track_number = serializers.CharField(allow_blank=True, allow_null=True)


class AgentWithProductsSerializer(serializers.Serializer):
    agent = AgentInfoSerializer()
    products = AgentProductOnHandSerializer(many=True)


class GlobalProductReadSerializer(serializers.ModelSerializer):
    brand = serializers.CharField(source="brand.name", read_only=True)
    category = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = GlobalProduct
        fields = ["id", "name", "barcode", "brand", "category"]


class PromoRuleSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    # Покажем удобные подписи
    product_name = serializers.ReadOnlyField(source="product.name")
    brand_name = serializers.ReadOnlyField(source="brand.name")
    category_name = serializers.ReadOnlyField(source="category.name")

    class Meta:
        model = PromoRule
        fields = [
            "id", "company", "branch",
            "title",
            "product", "product_name",
            "brand", "brand_name",
            "category", "category_name",
            "min_qty", "gift_qty", "inclusive",
            "priority",
            "active_from", "active_to", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "company", "branch",
            "product_name", "brand_name", "category_name",
            "created_at", "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comp = self._user_company()
        br = self._auto_branch()
        # Ограничим product / brand / category в выпадашках так же, как ты делаешь в других местах:
        _restrict_pk_queryset_strict(self.fields.get("product"), Product.objects.all(), comp, br)
        _restrict_pk_queryset_strict(self.fields.get("brand"), ProductBrand.objects.all(), comp, br)
        _restrict_pk_queryset_strict(self.fields.get("category"), ProductCategory.objects.all(), comp, br)

    def validate(self, attrs):
        """
        Правила:
          - только один таргет: product ИЛИ brand ИЛИ category ИЛИ ни одного (глобальное правило)
          - min_qty >= 1, gift_qty >= 1
          - объект должен принадлежать той же компании и (если есть филиал) филиалу
        Остальное уже проверяет модель .clean(), но мы словим пораньше.
        """
        product = attrs.get("product") or getattr(self.instance, "product", None)
        brand = attrs.get("brand") or getattr(self.instance, "brand", None)
        category = attrs.get("category") or getattr(self.instance, "category", None)

        chosen = [product, brand, category]
        if sum(bool(x) for x in chosen) > 1:
            raise serializers.ValidationError("Укажите только product ИЛИ brand ИЛИ category (или ничего).")

        min_qty = attrs.get("min_qty", getattr(self.instance, "min_qty", None))
        gift_qty = attrs.get("gift_qty", getattr(self.instance, "gift_qty", None))

        if min_qty is not None and min_qty < 1:
            raise serializers.ValidationError({"min_qty": "Порог должен быть ≥ 1."})
        if gift_qty is not None and gift_qty < 1:
            raise serializers.ValidationError({"gift_qty": "Подарок должен быть ≥ 1."})

        return attrs


class AgentRequestCartSubmitSerializer(serializers.Serializer):
    """
    Агент нажимает 'отправить заявку владельцу'.
    cart.submit() делает:
      - фиксирует подарки (gift_quantity/total_quantity/price_snapshot),
      - ставит status='submitted', submitted_at=...
    """
    def save(self, **kwargs):
        cart: AgentRequestCart = self.context["cart_obj"]
        cart.submit()
        return cart


class AgentRequestCartApproveSerializer(serializers.Serializer):
    """
    Владелец/админ нажимает 'одобрить'.
    cart.approve(by_user=request.user) делает:
      - проверяет остатки,
      - списывает Product.quantity,
      - создаёт ManufactureSubreal под агента с is_sawmill=True,
      - связывает эти subreal с позициями,
      - ставит status='approved'
    """
    def save(self, **kwargs):
        cart: AgentRequestCart = self.context["cart_obj"]
        user = self.context["request"].user
        cart.approve(by_user=user)
        return cart


class AgentRequestCartRejectSerializer(serializers.Serializer):
    """
    Владелец/админ отклоняет.
    cart.reject(by_user=request.user) просто помечает статус='rejected'
    """
    def save(self, **kwargs):
        cart: AgentRequestCart = self.context["cart_obj"]
        user = self.context["request"].user
        cart.reject(by_user=user)
        return cart


class AgentRequestItemSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source="product.name")
    product_barcode = serializers.ReadOnlyField(source="product.barcode")
    subreal_id = serializers.ReadOnlyField(source="subreal.id")
    product_image_url = serializers.SerializerMethodField()

    class Meta:
        model = AgentRequestItem
        fields = [
            "id",
            "cart",
            "product", "product_name", "product_barcode",
            "product_image_url",
            "quantity_requested",
            "gift_quantity",
            "total_quantity",
            "price_snapshot",
            "subreal", "subreal_id",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id",
            "gift_quantity",
            "total_quantity",
            "price_snapshot",
            "subreal", "subreal_id",
            "created_at", "updated_at",
            "product_image_url",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        req = self.context.get("request")
        user = getattr(req, "user", None) if req else None
        comp = getattr(user, "company", None)
        br = _active_branch(self)

        # Ограничим queryset для product и cart по company/branch и по правам агента
        if comp and self.fields.get("product"):
            prod_qs = Product.objects.filter(company=comp)
            if br is not None:
                prod_qs = prod_qs.filter(branch=br)
            # если br None — все товары компании
            self.fields["product"].queryset = prod_qs

        if comp and self.fields.get("cart"):
            cart_qs = AgentRequestCart.objects.filter(company=comp)
            # корзины берем по компании без ограничения по филиалу:
            # доступ к чужому филиалу все равно ограничен на уровне view/queryset

            # агент может создавать строки только в своих корзинах
            if user and not getattr(user, "is_superuser", False) and not getattr(user, "is_owner", False):
                cart_qs = cart_qs.filter(agent=user)

            self.fields["cart"].queryset = cart_qs

    def get_product_image_url(self, obj):
        """
        Возвращаем URL главной фотки товара (is_primary=True),
        если нет главной — просто первую.
        """
        product = getattr(obj, "product", None)
        if not product:
            return None

        # у продукта есть related_name="images"
        images_qs = getattr(product, "images", None)
        if images_qs is None:
            return None

        # сначала пытаемся взять основную
        primary = images_qs.filter(is_primary=True).first()
        img_obj = primary or images_qs.first()
        if not img_obj or not img_obj.image:
            return None

        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(img_obj.image.url)
        return img_obj.image.url

    def validate(self, data):
        """
        Проверяем:
          - cart в статусе draft
          - product из той же компании/филиала
          - quantity_requested >=1
          - есть ли столько товара на складе
        """
        cart = data.get("cart") or getattr(self.instance, "cart", None)
        product = data.get("product") or getattr(self.instance, "product", None)
        qty = data.get("quantity_requested", getattr(self.instance, "quantity_requested", None))

        if not cart:
            raise serializers.ValidationError({"cart": "Обязательное поле."})

        if cart.status not in (AgentRequestCart.Status.DRAFT, AgentRequestCart.Status.SUBMITTED):
            raise serializers.ValidationError("Нельзя менять позиции: корзина не в черновике или отправлена.")

        if not product:
            raise serializers.ValidationError({"product": "Обязательное поле."})

        if product.company_id != cart.company_id:
            raise serializers.ValidationError({"product": "Товар другой компании."})
        if cart.branch_id and product.branch_id not in (None, cart.branch_id):
            raise serializers.ValidationError({"product": "Товар другого филиала."})

        if qty is None or qty < 1:
            raise serializers.ValidationError({"quantity_requested": "Количество должно быть ≥ 1."})

        # мягкая проверка склада
        if product.quantity < qty:
            raise serializers.ValidationError(
                {"quantity_requested": f"Недостаточно '{product.name}' на складе. Доступно: {product.quantity}"}
            )

        return data

    @transaction.atomic
    def create(self, validated_data):
        cart = validated_data["cart"]
        if cart.status not in (AgentRequestCart.Status.DRAFT, AgentRequestCart.Status.SUBMITTED):
            raise serializers.ValidationError("Добавлять можно только в черновик или отправленную заявку.")
        # вручную подарки не ставим — они рассчитываются на submit()
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        cart = instance.cart
        if cart.status not in (AgentRequestCart.Status.DRAFT, AgentRequestCart.Status.SUBMITTED):
            raise serializers.ValidationError("Редактировать можно только черновик или отправленную заявку.")
        instance.product = validated_data.get("product", instance.product)
        instance.quantity_requested = validated_data.get("quantity_requested", instance.quantity_requested)
        instance.save(update_fields=["product", "quantity_requested", "updated_at"])
        return instance


class AgentRequestCartSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    agent = serializers.ReadOnlyField(source="agent.id")
    agent_name = serializers.SerializerMethodField(read_only=True)

    client_name = serializers.ReadOnlyField(source="client.full_name")

    approved_by_name = serializers.SerializerMethodField(read_only=True)

    items = AgentRequestItemSerializer(many=True, read_only=True)

    total_requested = serializers.SerializerMethodField()
    total_gift = serializers.SerializerMethodField()
    total_all = serializers.SerializerMethodField()

    class Meta:
        model = AgentRequestCart
        fields = [
            "id", "company", "branch",
            "agent", "agent_name",
            "client", "client_name",
            "status", "note",
            "submitted_at", "approved_at", "approved_by", "approved_by_name",
            "items",
            "total_requested", "total_gift", "total_all",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "company", "branch",
            "agent", "agent_name",
            "client_name",
            "status",
            "submitted_at", "approved_at", "approved_by", "approved_by_name",
            "items",
            "total_requested", "total_gift", "total_all",
            "created_at", "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ограничим выбор клиента: как в других местах
        comp = self._user_company()
        br = self._auto_branch()
        _restrict_pk_queryset_strict(self.fields.get("client"), Client.objects.all(), comp, br)

    def get_agent_name(self, obj):
        """
        Возвращаем человеко-читаемое имя агента:
        - сначала Имя + Фамилия
        - потом track_number (если вдруг у него нет имени)
        - потом email как самый последний fallback
        """
        u = getattr(obj, "agent", None)
        if not u:
            return ""

        full = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip()
        if full:
            return full

        if getattr(u, "track_number", None):
            return u.track_number

        # last fallback: email или id
        return u.email or str(u.id)

    def get_agent_track_number(self, obj):
        u = getattr(obj, "agent", None)
        return getattr(u, "track_number", None)

    def get_approved_by_name(self, obj):
        u = getattr(obj, "approved_by", None)
        if not u:
            return None
        full = getattr(u, "get_full_name", lambda: "")() or ""
        return full or getattr(u, "username", None) or str(u.id)

    def get_total_requested(self, obj):
        # сумма quantity_requested по всем позициям
        return sum((it.quantity_requested for it in obj.items.all()), 0)

    def get_total_gift(self, obj):
        # сумма gift_quantity — рассчитывается и фиксируется на submit()
        return sum((it.gift_quantity for it in obj.items.all()), 0)

    def get_total_all(self, obj):
        # сумма total_quantity (requested + gift)
        return sum((it.total_quantity for it in obj.items.all()), 0)

    def validate_client(self, client):
        """
        Повторяем branch-валидацию как в других сериализаторах (ClientDeal, ObjectSale и т.д.)
        """
        if client is None:
            return None
        company = self._user_company()
        branch = self._auto_branch()
        if client.company_id != company.id:
            raise serializers.ValidationError("Клиент принадлежит другой компании.")
        if branch is not None and client.branch_id != branch.id:
            raise serializers.ValidationError("Клиент другого филиала.")
        # branch None — клиент из любого филиала компании
        return client

    def create(self, validated_data):
        """
        создаём черновик. agent = текущий пользователь.
        company/branch нам уже зафигачит CompanyBranchReadOnlyMixin.create(),
        но agent нужно подставить явно.
        """
        user = self.context["request"].user
        validated_data["agent"] = user
        cart = super().create(validated_data)
        return cart

    def update(self, instance, validated_data):
        """
        В draft агент может менять только:
          - client
          - note
        Статусы и системные поля руками менять нельзя.
        """
        if instance.status != AgentRequestCart.Status.DRAFT:
            raise serializers.ValidationError("Редактировать можно только черновик.")

        # client уже прошёл validate_client
        if "client" in validated_data:
            instance.client = validated_data["client"]

        if "note" in validated_data:
            instance.note = validated_data["note"]

        # branch/company ставит миксин update() сам, но status трогать нельзя
        super().update(instance, {})  # чтобы миксин прописал company/branch
        instance.save(update_fields=["client", "note", "branch", "updated_at"])
        return instance
