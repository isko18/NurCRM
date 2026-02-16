# apps/cafe/serializers.py
import re
from django.contrib.auth import get_user_model
from django.db.models import Q
from decimal import Decimal
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction, IntegrityError

from apps.cafe.models import (
    Zone, Table, Booking, Warehouse, Purchase,
    Category, MenuItem, Ingredient,
    Order, OrderItem, CafeClient,
    OrderHistory, OrderItemHistory, KitchenTask, NotificationCafe, InventorySession, InventoryItem, Equipment, EquipmentInventoryItem, EquipmentInventorySession, Kitchen,
    CafeReceiptPrinterSettings,
)
from apps.users.models import Branch

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
        Активный филиал:
          0) ?branch=<uuid> в запросе (если филиал принадлежит компании пользователя)
          1) user.primary_branch() / user.primary_branch
          2) request.branch (если есть)
          3) None

        Всегда проверяем, что branch.company_id == company.id.
        """
        request = self.context.get("request")
        user = self._user()
        company = self._user_company()
        comp_id = getattr(company, "id", None)

        if not request or not user or not comp_id:
            return None

        # 0) ?branch=<uuid> в query-параметрах
        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=comp_id)
                # Зафиксируем на request, чтобы в остальных местах можно было использовать request.branch
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # если кривой/чужой/несуществующий branch — тихо игнорируем и идём дальше
                pass

        # 1) primary_branch как метод
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == comp_id:
                    return val
            except Exception:
                pass

        # 1b) primary_branch как атрибут
        if primary and getattr(primary, "company_id", None) == comp_id:
            return primary

        # 2) request.branch, если кто-то уже проставил (мидлварь и т.п.)
        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == comp_id:
                return b

        # 3) глобальный режим
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
    table_number = serializers.SerializerMethodField()
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

    def get_table_number(self, obj):
        return obj.order.table.number if obj.order_id and obj.order.table_id else None

    def get_guest(self, obj):
        return getattr(obj.order, 'client', None) and (obj.order.client.name or obj.order.client.phone) or ''

    def get_waiter_label(self, obj):
        w = obj.waiter
        if not w:
            return ''
        fn = getattr(w, 'get_full_name', lambda: '')() or ''
        return fn or getattr(w, 'email', '') or str(w.pk)

def _strip_null_bytes(value):
    """Убирает нуль-байты из строки (Django/CharField их не допускает)."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


class NullByteSafeCharField(serializers.CharField):
    """CharField, убирающий нуль-байты до валидации и при выводе (избегает ошибки Django)."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            data = data.replace("\x00", "")
        return super().to_internal_value(data)

    def to_representation(self, value):
        if value is not None and isinstance(value, str):
            value = value.replace("\x00", "")
        return super().to_representation(value)


class KitchenSerializer(CompanyBranchReadOnlyMixin):
    printer = NullByteSafeCharField(required=False, allow_blank=True, max_length=255)

    class Meta:
        model = Kitchen
        fields = ["id", "company", "branch", "title", "number", "printer"]
        read_only_fields = ["id", "company", "branch"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if "printer" in data and data["printer"] is not None:
            data["printer"] = _strip_null_bytes(data["printer"])
        return data

    def validate(self, attrs):
        title = (attrs.get("title") or getattr(self.instance, "title", "") or "").strip()
        if not title:
            raise serializers.ValidationError({"title": "Название кухни обязательно."})

        number = attrs.get("number", getattr(self.instance, "number", None))
        if self.instance is None and number is None:
            raise serializers.ValidationError({"number": "Обязательное поле."})

        company = self._user_company()
        if not company:
            raise serializers.ValidationError("Невозможно определить компанию пользователя.")

        auto_branch = self._auto_branch()
        branch_to_use = (
            auto_branch
            if auto_branch is not None
            else getattr(self.instance, "branch", None)
        )

        qs = Kitchen.objects.filter(company=company)
        if branch_to_use is None:
            qs = qs.filter(branch__isnull=True)
        else:
            qs = qs.filter(branch=branch_to_use)

        if number is not None:
            num_qs = qs.filter(number=number)
            if self.instance is not None:
                num_qs = num_qs.exclude(pk=self.instance.pk)
            if num_qs.exists():
                raise serializers.ValidationError({"number": "Кухня с таким номером уже существует."})

        title_qs = qs.filter(title=title)
        if self.instance is not None:
            title_qs = title_qs.exclude(pk=self.instance.pk)
        if title_qs.exists():
            raise serializers.ValidationError({"title": "Кухня с таким названием уже существует."})

        # Убираем нуль-байты из printer (в БД могли остаться, Django их не допускает)
        if "printer" in attrs:
            attrs["printer"] = _strip_null_bytes(attrs["printer"] or "")
        elif self.instance is not None:
            current = getattr(self.instance, "printer", None) or ""
            attrs["printer"] = _strip_null_bytes(current)

        return attrs

    def create(self, validated_data):
        try:
            with transaction.atomic():
                return super().create(validated_data)
        except IntegrityError as e:
            # Дружелюбная ошибка вместо 500 IntegrityError (уникальные ограничения)
            msg = str(e)
            if "uniq_kitchen_number_" in msg:
                raise serializers.ValidationError({"number": "Кухня с таким номером уже существует."})
            if "uniq_kitchen_title_" in msg:
                raise serializers.ValidationError({"title": "Кухня с таким названием уже существует."})
            raise

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                return super().update(instance, validated_data)
        except IntegrityError as e:
            msg = str(e)
            if "uniq_kitchen_number_" in msg:
                raise serializers.ValidationError({"number": "Кухня с таким номером уже существует."})
            if "uniq_kitchen_title_" in msg:
                raise serializers.ValidationError({"title": "Кухня с таким названием уже существует."})
            raise

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
        fields = ["id", "company", "branch", "title", "unit", "remainder", "minimum", "unit_price"]
        read_only_fields = ["id", "company", "branch"]

    def validate(self, attrs):
        title = attrs.get("title")
        if title is None:
            return attrs
        company = self._user_company()
        if not company:
            return attrs
        branch = self._auto_branch() if self.instance is None else (getattr(self.instance, "branch", None))
        qs = Warehouse.objects.filter(company=company, title=title)
        if branch is not None:
            qs = qs.filter(branch=branch)
        else:
            qs = qs.filter(branch__isnull=True)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            scope = "филиале" if branch else "компании (глобальный склад)"
            raise serializers.ValidationError({
                "title": f"Склад с названием «{title}» уже существует в этом {scope}."
            })
        return attrs


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
    product_unit_price = serializers.DecimalField(
        source="product.unit_price", max_digits=12, decimal_places=2, read_only=True
    )
    ingredient_cost = serializers.SerializerMethodField()

    class Meta:
        model = Ingredient
        fields = [
            "id", "product", "product_title", "product_unit",
            "product_unit_price", "amount", "ingredient_cost"
        ]
        read_only_fields = ["id", "product_title", "product_unit", "product_unit_price", "ingredient_cost"]

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

    def get_ingredient_cost(self, obj):
        """Стоимость ингредиента = количество * цена за единицу"""
        unit_price = obj.product.unit_price or Decimal("0.00")
        amount = obj.amount or Decimal("0.00")
        return (unit_price * amount).quantize(Decimal("0.01"))

    def validate(self, attrs):
        # model.clean() на связях добьёт; здесь ничего лишнего
        return attrs


class MenuItemSerializer(CompanyBranchReadOnlyMixin):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all())

    kitchen = serializers.PrimaryKeyRelatedField(
        queryset=Kitchen.objects.all(),
        required=False,
        allow_null=True
    )
    kitchen_title = serializers.CharField(source="kitchen.title", read_only=True)
    kitchen_number = serializers.IntegerField(source="kitchen.number", read_only=True)

    ingredients = IngredientInlineSerializer(many=True, required=False)
    image = serializers.ImageField(required=False, allow_null=True)
    image_url = serializers.SerializerMethodField()
    
    # Новые поля для себестоимости
    vat_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=Decimal("0.00")
    )
    other_expenses = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=Decimal("0.00")
    )
    cost_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    
    # Вычисляемые поля (read-only)
    vat_amount = serializers.SerializerMethodField()
    profit = serializers.SerializerMethodField()
    margin_percent = serializers.SerializerMethodField()
    ingredients_cost = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = [
            "id", "company", "branch",
            "title", "category",
            "kitchen", "kitchen_title", "kitchen_number",
            "price", "is_active",
            "image", "image_url",
            # Себестоимость и расходы
            "vat_percent", "other_expenses", "cost_price",
            "vat_amount", "profit", "margin_percent", "ingredients_cost",
            "created_at", "updated_at", "ingredients",
        ]
        read_only_fields = [
            "id", "company", "branch", "created_at", "updated_at",
            "cost_price", "vat_amount", "profit", "margin_percent", "ingredients_cost"
        ]

    def get_fields(self):
        fields = super().get_fields()
        fields["category"].queryset = _scope_queryset_by_context(Category.objects.all(), self)
        fields["kitchen"].queryset = _scope_queryset_by_context(Kitchen.objects.all(), self)
        return fields

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and hasattr(obj.image, "url"):
            url = obj.image.url
            return request.build_absolute_uri(url) if request else url
        return None

    def get_vat_amount(self, obj):
        """Сумма НДС от цены продажи"""
        return obj.vat_amount

    def get_profit(self, obj):
        """Прибыль = Цена продажи - Себестоимость - НДС"""
        return obj.profit

    def get_margin_percent(self, obj):
        """Маржа в процентах"""
        return obj.margin_percent

    def get_ingredients_cost(self, obj):
        """Стоимость всех ингредиентов (без прочих расходов)"""
        total = Decimal("0.00")
        for ingredient in obj.ingredients.select_related('product').all():
            unit_price = ingredient.product.unit_price or Decimal("0.00")
            amount = ingredient.amount or Decimal("0.00")
            total += unit_price * amount
        return total.quantize(Decimal("0.01"))

    def validate(self, attrs):
        company = self._user_company() or getattr(self.instance, "company", None)
        tb = self._auto_branch()

        category = attrs.get("category") or getattr(self.instance, "category", None)
        kitchen = attrs.get("kitchen") if "kitchen" in attrs else getattr(self.instance, "kitchen", None)

        if company and category and category.company_id != company.id:
            raise serializers.ValidationError({"category": "Категория принадлежит другой компании."})
        if tb is not None and category and category.branch_id not in (None, tb.id):
            raise serializers.ValidationError({"category": "Категория другого филиала."})

        if kitchen:
            if company and kitchen.company_id != company.id:
                raise serializers.ValidationError({"kitchen": "Кухня принадлежит другой компании."})
            if tb is not None and kitchen.branch_id not in (None, tb.id):
                raise serializers.ValidationError({"kitchen": "Кухня другого филиала."})

        # Валидация НДС
        vat = attrs.get("vat_percent")
        if vat is not None and vat < 0:
            raise serializers.ValidationError({"vat_percent": "НДС не может быть отрицательным."})
        if vat is not None and vat > 100:
            raise serializers.ValidationError({"vat_percent": "НДС не может быть больше 100%."})

        # Валидация прочих расходов
        other = attrs.get("other_expenses")
        if other is not None and other < 0:
            raise serializers.ValidationError({"other_expenses": "Прочие расходы не могут быть отрицательными."})

        return attrs

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

    def _recalc_and_save_cost(self, menu_item):
        """Пересчитать и сохранить себестоимость"""
        menu_item.recalc_cost_price()
        menu_item.save(update_fields=["cost_price"])

    def create(self, validated_data):
        ingredients = validated_data.pop("ingredients", [])
        obj = super().create(validated_data)
        if ingredients:
            self._upsert_ingredients(obj, ingredients)
        # Пересчитываем себестоимость после добавления ингредиентов
        self._recalc_and_save_cost(obj)
        return obj

    def update(self, instance, validated_data):
        ingredients = validated_data.pop("ingredients", None)
        obj = super().update(instance, validated_data)
        if ingredients is not None:
            instance.ingredients.all().delete()
            if ingredients:
                self._upsert_ingredients(instance, ingredients)
        # Пересчитываем себестоимость после обновления
        self._recalc_and_save_cost(obj)
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
    table_number = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "table_number", "guests", "waiter", "created_at"]

    def get_table_number(self, obj):
        return obj.table.number if obj.table_id else None


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
              "status","is_paid","paid_at","payment_method","total_amount","discount_amount", "items"
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
    table = serializers.PrimaryKeyRelatedField(
        queryset=Table.objects.all(), required=False, allow_null=True
    )
    client = serializers.PrimaryKeyRelatedField(queryset=CafeClient.objects.all(), required=False, allow_null=True)
    waiter = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), allow_null=True, required=False)
    items = OrderItemInlineSerializer(many=True, required=False)

    class Meta:
        ref_name = "CafeOrder"
        model = Order
        fields = [
        "id","company","branch","table","client","waiter","guests","created_at",
        "status","is_paid","paid_at","payment_method","total_amount","discount_amount",
        "items"
        ]
        read_only_fields = ["is_paid","paid_at","payment_method","total_amount"]

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

        discount = attrs.get("discount_amount")
        if discount is not None and discount < 0:
            raise serializers.ValidationError({"discount_amount": "Скидка не может быть отрицательной."})
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
                # Сохраняем KitchenTask, которые уже в работе (IN_PROGRESS или READY)
                # Группируем по menu_item_id и unit_index для последующего восстановления
                from .models import KitchenTask
                active_tasks = KitchenTask.objects.filter(
                    order=instance,
                    status__in=[KitchenTask.Status.IN_PROGRESS, KitchenTask.Status.READY]
                ).select_related('order_item', 'menu_item').values(
                    'menu_item_id', 'unit_index', 'status', 
                    'cook_id', 'started_at', 'finished_at'
                )
                
                # Сохраняем активные задачи по menu_item_id и unit_index
                active_tasks_by_menu = {}
                for task in active_tasks:
                    key = (task['menu_item_id'], task['unit_index'])
                    active_tasks_by_menu[key] = task
                
                # Удаляем все items (каскадом удалятся KitchenTask)
                instance.items.all().delete()
                
                # Создаем новые items
                if items:
                    self._upsert_items(instance, items)
                
                # Восстанавливаем KitchenTask для соответствующих menu_item
                if active_tasks_by_menu:
                    # Получаем созданные items, сгруппированные по menu_item_id
                    created_items_by_menu = {}
                    for item in instance.items.select_related('menu_item').all():
                        if item.menu_item_id not in created_items_by_menu:
                            created_items_by_menu[item.menu_item_id] = []
                        created_items_by_menu[item.menu_item_id].append(item)
                    
                    # Восстанавливаем активные задачи
                    tasks_to_restore = []
                    tasks_to_update = []
                    for (menu_item_id, unit_index), task_data in active_tasks_by_menu.items():
                        # Ищем соответствующий новый item по menu_item_id
                        matching_items = created_items_by_menu.get(menu_item_id, [])
                        if matching_items:
                            # Берем первый подходящий item (или можно выбрать по другим критериям)
                            matching_item = matching_items[0]
                            
                            # Проверяем, что unit_index не превышает quantity нового item
                            if unit_index <= matching_item.quantity:
                                # Проверяем, существует ли уже задача с таким order_item и unit_index
                                # (она могла быть создана сигналом со статусом PENDING)
                                existing_task = KitchenTask.objects.filter(
                                    order_item=matching_item,
                                    unit_index=unit_index
                                ).first()
                                
                                if existing_task:
                                    # Если задача уже существует (создана сигналом), обновляем её
                                    existing_task.status = task_data['status']
                                    existing_task.cook_id = task_data['cook_id']
                                    existing_task.started_at = task_data['started_at']
                                    existing_task.finished_at = task_data['finished_at']
                                    existing_task.waiter = instance.waiter
                                    tasks_to_update.append(existing_task)
                                else:
                                    # Если задачи нет, создаем новую
                                    tasks_to_restore.append(
                                        KitchenTask(
                                            company=instance.company,
                                            branch=instance.branch,
                                            order=instance,
                                            order_item=matching_item,
                                            menu_item_id=menu_item_id,
                                            waiter=instance.waiter,
                                            unit_index=unit_index,
                                            status=task_data['status'],
                                            cook_id=task_data['cook_id'],
                                            started_at=task_data['started_at'],
                                            finished_at=task_data['finished_at'],
                                        )
                                    )
                    
                    # Обновляем существующие задачи
                    if tasks_to_update:
                        KitchenTask.objects.bulk_update(
                            tasks_to_update,
                            ['status', 'cook_id', 'started_at', 'finished_at', 'waiter_id'],
                            batch_size=100
                        )
                    
                    # Создаем новые задачи
                    if tasks_to_restore:
                        KitchenTask.objects.bulk_create(tasks_to_restore, ignore_conflicts=True)
        return instance
    

class OrderPaySerializer(serializers.Serializer):
    """Оплата заказа: способ оплаты (нал/безнал), скидка, закрыть заказ."""
    payment_method = serializers.ChoiceField(
        choices=[
            ("cash", "Наличные"),
            ("card", "Безналичный (карта)"),
            ("transfer", "Безналичный (перевод)"),
        ],
        required=False,
        default="cash",
        help_text="Способ оплаты: cash — наличные, card/transfer — безнал. Обязательно указывать при оплате.",
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=Decimal("0"),
        help_text="Скидка на заказ (можно задать также при создании/редактировании заказа).",
    )
    close_order = serializers.BooleanField(required=False, default=True)

    def validate_discount_amount(self, v):
        if v is None:
            return Decimal("0")
        if v < 0:
            raise serializers.ValidationError("discount_amount не может быть отрицательным.")
        return v

class InventoryItemSerializer(serializers.ModelSerializer):
    product_title = serializers.CharField(source="product.title", read_only=True)
    product_unit = serializers.CharField(source="product.unit", read_only=True)

    class Meta:
        model = InventoryItem
        fields = ["id", "product", "product_title", "product_unit",
                  "expected_qty", "actual_qty", "difference"]
        read_only_fields = ["id", "product_title", "product_unit", "difference"]

    def get_fields(self):
        fields = super().get_fields()
        holder = getattr(self, "root", None)
        if isinstance(holder, CompanyBranchReadOnlyMixin):
            fields["product"].queryset = _scope_queryset_by_context(Warehouse.objects.all(), holder)
        else:
            fields["product"].queryset = Warehouse.objects.none()
        return fields

    def validate(self, attrs):
        exp = attrs.get("expected_qty")
        act = attrs.get("actual_qty")
        if exp is None or act is None:
            return attrs
        if exp < 0 or act < 0:
            raise serializers.ValidationError({"actual_qty": "Кол-во не может быть отрицательным."})
        return attrs


class InventorySessionSerializer(CompanyBranchReadOnlyMixin):
    items = InventoryItemSerializer(many=True)

    class Meta:
        model = InventorySession
        fields = ["id", "company", "branch", "comment", "created_by",
                  "created_at", "confirmed_at", "is_confirmed", "items"]
        read_only_fields = ["id", "company", "branch", "created_by",
                            "created_at", "confirmed_at", "is_confirmed"]

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        with transaction.atomic():
            obj = super().create(validated_data)  # company/branch проставит миксин
            obj.created_by = getattr(self.context.get("request"), "user", None)
            obj.save(update_fields=["created_by"])

            if items_data:
                seen = set()
                bulk = []
                for it in items_data:
                    product = it["product"]
                    if product.pk in seen:
                        raise serializers.ValidationError(
                            {"items": f"Товар «{product.title}» повторяется в одном акте."}
                        )
                    seen.add(product.pk)
                    exp = it["expected_qty"]
                    act = it["actual_qty"]
                    bulk.append(InventoryItem(
                        session=obj,
                        product=product,
                        expected_qty=exp,
                        actual_qty=act,
                        difference=act - exp,  # важно: bulk_create не вызовет save()
                    ))
                InventoryItem.objects.bulk_create(bulk)
        return obj


# ==========================
# SERIALIZERS: Инвентаризация оборудования
# ==========================
# ==========================
# INVENTORY (оборудование)
# ==========================
class EquipmentSerializer(CompanyBranchReadOnlyMixin):
    class Meta:
        model = Equipment
        fields = [
            "id", "company", "branch", "title", "serial_number",
            "category", "purchase_date", "price", "condition", "is_active", "notes",
        ]
        read_only_fields = ["id", "company", "branch"]


class EquipmentInventoryItemSerializer(serializers.ModelSerializer):
    equipment_title = serializers.CharField(source="equipment.title", read_only=True)
    serial_number = serializers.CharField(source="equipment.serial_number", read_only=True)

    class Meta:
        model = EquipmentInventoryItem
        fields = ["id", "equipment", "equipment_title", "serial_number",
                  "is_present", "condition", "notes"]
        read_only_fields = ["id", "equipment_title", "serial_number"]

    def get_fields(self):
        fields = super().get_fields()
        holder = getattr(self, "root", None)
        if isinstance(holder, CompanyBranchReadOnlyMixin):
            fields["equipment"].queryset = _scope_queryset_by_context(Equipment.objects.all(), holder)
        else:
            fields["equipment"].queryset = Equipment.objects.none()
        return fields


class EquipmentInventorySessionSerializer(CompanyBranchReadOnlyMixin):
    items = EquipmentInventoryItemSerializer(many=True)

    class Meta:
        model = EquipmentInventorySession
        fields = ["id", "company", "branch", "comment", "created_by",
                  "created_at", "confirmed_at", "is_confirmed", "items"]
        read_only_fields = ["id", "company", "branch", "created_by",
                            "created_at", "confirmed_at", "is_confirmed"]

    def create(self, validated_data):
        items_data = validated_data.pop("items", [])
        with transaction.atomic():
            obj = super().create(validated_data)
            obj.created_by = getattr(self.context.get("request"), "user", None)
            obj.save(update_fields=["created_by"])

            if items_data:
                seen = set()
                bulk = []
                for it in items_data:
                    eq = it["equipment"]
                    if eq.pk in seen:
                        raise serializers.ValidationError(
                            {"items": f"Оборудование «{eq.title}» повторяется в одном акте."}
                        )
                    seen.add(eq.pk)
                    bulk.append(EquipmentInventoryItem(
                        session=obj,
                        equipment=eq,
                        is_present=it.get("is_present", True),
                        condition=it.get("condition", Equipment.Condition.GOOD),
                        notes=it.get("notes", ""),
                    ))
                EquipmentInventoryItem.objects.bulk_create(bulk)
        return obj


# ===== Настройки принтера кассы (чековый принтер) =====

# IPv4: 1-3 цифр в каждой группе, 4 группы
_PRINTER_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
_PRINTER_USB_RE = re.compile(r"^[0-9a-fA-F]{1,4}:[0-9a-fA-F]{1,4}:.+$")  # vid:pid:serial


def _validate_printer_binding(value):
    """Проверка формата binding: ip/<ipv4>[:port] или usb/<vid>:<pid>:<serial>."""
    if not value or not isinstance(value, str):
        return
    s = value.strip()
    if not s:
        return
    if s.startswith("ip/"):
        rest = s[3:].strip()
        if ":" in rest:
            host, port_str = rest.rsplit(":", 1)
            host = host.strip()
            try:
                port = int(port_str.strip())
                if port < 1 or port > 65535:
                    raise serializers.ValidationError(
                        "Некорректный printer: порт должен быть от 1 до 65535."
                    )
            except ValueError:
                raise serializers.ValidationError(
                    "Некорректный printer: порт должен быть числом."
                )
        else:
            host = rest
        m = _PRINTER_IPV4_RE.match(host)
        if not m:
            raise serializers.ValidationError(
                "Некорректный printer: для Wi‑Fi укажите ip/<IPv4> или ip/<IPv4>:<порт>."
            )
        for g in m.groups():
            if int(g) > 255:
                raise serializers.ValidationError(
                    "Некорректный printer: неверный IPv4-адрес."
                )
    elif s.startswith("usb/"):
        rest = s[4:].strip()
        if not rest or ":" not in rest or rest.count(":") < 2:
            raise serializers.ValidationError(
                "Некорректный printer: для USB укажите usb/<vid>:<pid>:<serial> (например usb/1fc9:2016:noserial)."
            )
        parts = rest.split(":", 2)
        try:
            int(parts[0], 16)
            int(parts[1], 16)
        except ValueError:
            raise serializers.ValidationError(
                "Некорректный printer: vid и pid должны быть hex-числами."
            )
    else:
        raise serializers.ValidationError(
            "Некорректный printer: укажите ip/<IPv4>[:порт] или usb/<vid>:<pid>:<serial>."
        )


def _validate_bridge_url(value):
    """Если указан непустой URL — проверяем формат (http/https)."""
    if not value or not isinstance(value, str):
        return
    s = value.strip()
    if not s:
        return
    if not (s.startswith("http://") or s.startswith("https://")):
        raise serializers.ValidationError(
            "Некорректный bridge_url: укажите URL (http:// или https://)."
        )


class CafeReceiptPrinterSettingsSerializer(serializers.ModelSerializer):
    """Настройки принтера кассы: printer (binding) и bridge_url."""

    class Meta:
        model = CafeReceiptPrinterSettings
        fields = ["printer", "bridge_url", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_printer(self, value):
        if value is not None and str(value).strip():
            _validate_printer_binding(value)
        return value or ""

    def validate_bridge_url(self, value):
        if value is not None:
            _validate_bridge_url(value)
        return value or ""

    def to_representation(self, instance):
        if instance is None:
            return {"printer": "", "bridge_url": "", "updated_at": None}
        return super().to_representation(instance)
