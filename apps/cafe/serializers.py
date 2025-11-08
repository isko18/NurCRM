# apps/cafe/serializers.py
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from .models import (
    Zone, Table, Booking, Warehouse, Purchase,
    Category, MenuItem, Ingredient,
    Order, OrderItem, CafeClient,
    OrderHistory, OrderItemHistory, KitchenTask, NotificationCafe
)

User = get_user_model()


# ===== company/branch mixin (как в барбере/букинге) =====
class CompanyBranchReadOnlyMixin(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    def _user(self):
        return getattr(self.context.get("request"), "user", None)

    def _user_company(self):
        u = self._user()
        return getattr(u, "company", None) or getattr(u, "owned_company", None)

    def _auto_branch(self):
        """
        1) user.primary_branch() / user.primary_branch
        2) request.branch (если есть)
        3) None
        Только если branch принадлежит компании пользователя.
        """
        request = self.context.get("request")
        user = self._user()
        comp_id = getattr(self._user_company(), "id", None)
        if not request or not user or not comp_id:
            return None

        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == comp_id:
                    return val
            except Exception:
                pass
        if primary and getattr(primary, "company_id", None) == comp_id:
            return primary

        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == comp_id:
                return b

        return None

    def create(self, validated_data):
        company = self._user_company()
        if not company:
            raise serializers.ValidationError("Невозможно определить компанию пользователя.")
        validated_data["company"] = company

        # если у модели есть branch — проставим
        if "branch" in getattr(self.Meta, "fields", []):
            auto_branch = self._auto_branch()
            validated_data["branch"] = auto_branch if auto_branch is not None else None
        return super().create(validated_data)

    def update(self, instance, validated_data):
        company = self._user_company()
        if company:
            validated_data["company"] = company
        # не перетираем branch, если не смогли определить
        if "branch" in getattr(self.Meta, "fields", []):
            auto_branch = self._auto_branch()
            if auto_branch is not None:
                validated_data["branch"] = auto_branch
        return super().update(instance, validated_data)


def _scope_queryset_by_context(qs, serializer: CompanyBranchReadOnlyMixin):
    company = serializer._user_company()
    if not company:
        return qs.none()
    qs = qs.filter(company=company)

    # было: if hasattr(qs.model, "branch"):
    has_branch = any(getattr(f, "name", None) == "branch" for f in qs.model._meta.get_fields())
    if has_branch:
        b = serializer._auto_branch()
        if b is not None:
            qs = qs.filter(Q(branch=b) | Q(branch__isnull=True))
        else:
            qs = qs.filter(branch__isnull=True)
    return qs



class KitchenTaskSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    table_number = serializers.IntegerField(source='order.table.number', read_only=True)
    guest = serializers.SerializerMethodField()
    waiter_label = serializers.SerializerMethodField()
    menu_item_title = serializers.CharField(source='menu_item.title', read_only=True)
    price = serializers.DecimalField(source='menu_item.price', max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = KitchenTask
        fields = [
            'id', 'company', 'branch',
            'status', 'created_at', 'started_at', 'finished_at',
            'order', 'order_item', 'menu_item',
            'table_number', 'guest', 'waiter', 'waiter_label',
            'cook', 'unit_index', 'menu_item_title', 'price',
        ]
        read_only_fields = [
            'id', 'company', 'branch', 'created_at', 'started_at', 'finished_at',
            'order', 'order_item', 'menu_item', 'table_number', 'guest',
            'waiter', 'waiter_label', 'menu_item_title', 'price'
        ]

    def get_guest(self, obj):
        return getattr(obj.order, 'client', None) and (obj.order.client.name or obj.order.client.phone) or ''

    def get_waiter_label(self, obj):
        w = obj.waiter
        if not w:
            return ''
        fn = getattr(w, 'get_full_name', lambda: '')() or ''
        return fn or getattr(w, 'email', '') or str(w.pk)


# --- НОВОЕ: Notification (если нужно выводить списком) ---
class NotificationCafeSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationCafe
        fields = ['id', 'type', 'message', 'payload', 'is_read', 'created_at']
        read_only_fields = ['id', 'type', 'message', 'payload', 'is_read', 'created_at']



# --------- Простые справочники ---------
class ZoneSerializer(CompanyBranchReadOnlyMixin):
    class Meta:
        model = Zone
        fields = ["id", "company", "branch", "title"]
        read_only_fields = ["id", "company", "branch"]


class TableSerializer(CompanyBranchReadOnlyMixin):
    zone = serializers.PrimaryKeyRelatedField(queryset=Zone.objects.all())

    class Meta:
        model = Table
        fields = ["id", "company", "branch", "zone", "number", "places", "status"]
        read_only_fields = ["id", "company", "branch"]

    def get_fields(self):
        fields = super().get_fields()
        fields["zone"].queryset = _scope_queryset_by_context(Zone.objects.all(), self)
        return fields

    def validate(self, attrs):
        zone = attrs.get("zone") or getattr(self.instance, "zone", None)
        tb = self._auto_branch()
        company = self._user_company()
        if company and zone and zone.company_id != company.id:
            raise serializers.ValidationError({"zone": "Зона принадлежит другой компании."})
        if tb is not None and zone and zone.branch_id not in (None, tb.id):
            raise serializers.ValidationError({"zone": "Зона принадлежит другому филиалу."})
        return attrs


class WarehouseSerializer(CompanyBranchReadOnlyMixin):
    class Meta:
        ref_name = "CafeWarehouse"
        model = Warehouse
        fields = ["id", "company", "branch", "title", "unit", "remainder", "minimum"]
        read_only_fields = ["id", "company", "branch"]


class PurchaseSerializer(CompanyBranchReadOnlyMixin):
    class Meta:
        model = Purchase
        fields = ["id", "company", "branch", "supplier", "positions", "price"]
        read_only_fields = ["id", "company", "branch"]


class CategorySerializer(CompanyBranchReadOnlyMixin):
    class Meta:
        model = Category
        fields = ["id", "company", "branch", "title"]
        read_only_fields = ["id", "company", "branch"]


# --------- Меню и ингредиенты ---------
class IngredientInlineSerializer(serializers.ModelSerializer):
    product_title = serializers.CharField(source="product.title", read_only=True)
    product_unit = serializers.CharField(source="product.unit", read_only=True)

    class Meta:
        model = Ingredient
        fields = ["id", "product", "product_title", "product_unit", "amount"]
        read_only_fields = ["id", "product_title", "product_unit"]

    def get_fields(self):
        fields = super().get_fields()
        # Раньше: self.parent.parent  →  падало в Swagger
        holder = getattr(self, "root", None)
        if isinstance(holder, CompanyBranchReadOnlyMixin):
            fields["product"].queryset = _scope_queryset_by_context(Warehouse.objects.all(), holder)
        else:
            # нет контекста/корня (Swagger) — безопасно отдаём пусто
            fields["product"].queryset = Warehouse.objects.none()
        return fields

    def validate(self, attrs):
        # model.clean() на связях добьёт; здесь ничего лишнего
        return attrs


class MenuItemSerializer(CompanyBranchReadOnlyMixin):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all())
    ingredients = IngredientInlineSerializer(many=True, required=False)
    image = serializers.ImageField(required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            "id", "company", "branch", "title", "category", "price", "is_active",
            "image", "image_url",
            "created_at", "updated_at", "ingredients",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at"]

    def get_fields(self):
        fields = super().get_fields()
        fields["category"].queryset = _scope_queryset_by_context(Category.objects.all(), self)
        return fields

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and hasattr(obj.image, "url"):
            url = obj.image.url
            return request.build_absolute_uri(url) if request else url
        return None

    def _upsert_ingredients(self, menu_item, ing_list):
        to_create = []
        for ing in ing_list:
            to_create.append(Ingredient(
                menu_item=menu_item,
                product=ing["product"],
                amount=ing["amount"]
            ))
        if to_create:
            Ingredient.objects.bulk_create(to_create)

    def create(self, validated_data):
        ingredients = validated_data.pop("ingredients", [])
        obj = super().create(validated_data)
        if ingredients:
            self._upsert_ingredients(obj, ingredients)
        return obj

    def update(self, instance, validated_data):
        ingredients = validated_data.pop("ingredients", None)
        obj = super().update(instance, validated_data)
        if ingredients is not None:
            instance.ingredients.all().delete()
            if ingredients:
                self._upsert_ingredients(instance, ingredients)
        return obj


# --------- Бронь ---------
class BookingSerializer(CompanyBranchReadOnlyMixin):
    table = serializers.PrimaryKeyRelatedField(queryset=Table.objects.all())

    class Meta:
        model = Booking
        fields = [
            "id", "company", "branch",
            "guest", "phone", "date", "time", "guests", "table",
            "status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at"]

    def get_fields(self):
        fields = super().get_fields()
        fields["table"].queryset = _scope_queryset_by_context(Table.objects.all(), self)
        return fields

    def validate(self, attrs):
        table = attrs.get("table") or getattr(self.instance, "table", None)
        tb = self._auto_branch()
        if tb is not None and table and table.branch_id not in (None, tb.id):
            raise serializers.ValidationError({"table": "Стол принадлежит другому филиалу."})
        return attrs


# --------- Заказы ---------
class OrderItemInlineSerializer(serializers.ModelSerializer):
    menu_item_title = serializers.CharField(source="menu_item.title", read_only=True)
    menu_item_price = serializers.DecimalField(source="menu_item.price", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "menu_item", "menu_item_title", "menu_item_price", "quantity"]
        read_only_fields = ["id", "menu_item_title", "menu_item_price"]

    def get_fields(self):
        fields = super().get_fields()
        # Раньше: order_serializer = self.parent.parent
        holder = getattr(self, "root", None)
        if isinstance(holder, CompanyBranchReadOnlyMixin):
            fields["menu_item"].queryset = _scope_queryset_by_context(MenuItem.objects.all(), holder)
        else:
            fields["menu_item"].queryset = MenuItem.objects.none()
        return fields


class OrderBriefSerializer(serializers.ModelSerializer):
    table_number = serializers.IntegerField(source="table.number", read_only=True)

    class Meta:
        model = Order
        fields = ["id", "table_number", "guests", "waiter", "created_at"]


class OrderItemHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemHistory
        fields = ["id", "menu_item", "menu_item_title", "menu_item_price", "quantity"]
        read_only_fields = ["id", "menu_item", "menu_item_title", "menu_item_price", "quantity"]


class OrderHistorySerializer(serializers.ModelSerializer):
    items = OrderItemHistorySerializer(many=True, read_only=True)

    class Meta:
        model = OrderHistory
        fields = [
            "id", "original_order_id", "company", "branch", "client",
            "table", "table_number", "waiter", "waiter_label",
            "guests", "created_at", "archived_at", "items",
        ]
        read_only_fields = fields


class CafeClientSerializer(CompanyBranchReadOnlyMixin):
    orders = OrderBriefSerializer(many=True, read_only=True)
    history = OrderHistorySerializer(source="order_history", many=True, read_only=True)

    class Meta:
        model = CafeClient
        fields = ["id", "company", "branch", "name", "phone", "notes", "orders", "history"]
        read_only_fields = ["id", "company", "branch", "orders", "history"]

    def validate_phone(self, value):
        # лёгкая нормализация
        if not value:
            return value
        return ''.join(ch for ch in value if ch.isdigit() or ch == '+')


class OrderSerializer(CompanyBranchReadOnlyMixin):
    table = serializers.PrimaryKeyRelatedField(queryset=Table.objects.all())
    client = serializers.PrimaryKeyRelatedField(queryset=CafeClient.objects.all(), required=False, allow_null=True)
    waiter = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True, required=False)
    items = OrderItemInlineSerializer(many=True, required=False)

    class Meta:
        ref_name = "CafeOrder"
        model = Order
        fields = ["id", "company", "branch", "table", "client", "waiter", "guests", "created_at", "items"]
        read_only_fields = ["id", "company", "branch", "created_at"]

    def get_fields(self):
        fields = super().get_fields()
        fields["table"].queryset = _scope_queryset_by_context(Table.objects.all(), self)
        fields["client"].queryset = _scope_queryset_by_context(CafeClient.objects.all(), self)
        # сузим официантов по компании, если есть такое поле у User
        company = self._user_company()
        if company and hasattr(User, "company_id"):
            fields["waiter"].queryset = User.objects.filter(company_id=company.id)
        return fields

    def validate(self, attrs):
        company = self._user_company() or getattr(self.instance, "company", None)
        tb = self._auto_branch()
        table = attrs.get("table") or getattr(self.instance, "table", None)
        waiter = attrs.get("waiter") or getattr(self.instance, "waiter", None)
        client = attrs.get("client") or getattr(self.instance, "client", None)

        if company and table and table.company_id != company.id:
            raise serializers.ValidationError({"table": "Стол принадлежит другой компании."})
        if company and waiter and getattr(waiter, "company_id", None) not in (None, company.id):
            raise serializers.ValidationError({"waiter": "Официант принадлежит другой компании."})
        if company and client and client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})

        if tb is not None:
            tb_id = tb.id
            if table and table.branch_id not in (None, tb_id):
                raise serializers.ValidationError({"table": "Стол другого филиала."})
            if client and client.branch_id not in (None, tb_id):
                raise serializers.ValidationError({"client": "Клиент другого филиала."})
        return attrs

    def _upsert_items(self, order, items):
        for it in items:
            menu_item = it["menu_item"]
            qty = it.get("quantity", 1)
            existing = order.items.filter(menu_item=menu_item).first()
            if existing:
                existing.quantity += qty
                existing.save(update_fields=["quantity"])
            else:
                OrderItem.objects.create(order=order, menu_item=menu_item, quantity=qty, company=order.company)

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        obj = super().create(validated_data)
        if items:
            self._upsert_items(obj, items)
        return obj

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if items is not None:
                instance.items.all().delete()  # каскадом удалит KitchenTask
                if items:
                    self._upsert_items(instance, items)
        return instance