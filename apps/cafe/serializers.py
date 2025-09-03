# apps/cafe/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import (
    Zone, Table, Booking, Warehouse, Purchase,
    Category, MenuItem, Ingredient,
    Order, OrderItem, CafeClient,
    OrderHistory, OrderItemHistory,
)

User = get_user_model()


# --------- Базовый миксин для company ---------
class CompanyReadOnlyMixin(serializers.ModelSerializer):
    """
    Делает поле company read-only (как id) и проставляет company из request.user при create/update.
    """
    company = serializers.ReadOnlyField(source="company.id")

    def _get_company(self):
        request = self.context.get("request")
        return getattr(getattr(request, "user", None), "company", None)

    def create(self, validated_data):
        company = self._get_company()
        if company is None:
            raise serializers.ValidationError("Невозможно определить компанию пользователя.")
        validated_data["company"] = company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # company не меняем руками извне, но на всякий случай закрепим из пользователя
        company = self._get_company()
        if company is None:
            return super().update(instance, validated_data)
        validated_data["company"] = company
        return super().update(instance, validated_data)


# --------- Простые справочники ---------
class ZoneSerializer(CompanyReadOnlyMixin):
    class Meta:
        model = Zone
        fields = ["id", "company", "title"]


class TableSerializer(CompanyReadOnlyMixin):
    zone = serializers.PrimaryKeyRelatedField(queryset=Zone.objects.all())

    class Meta:
        model = Table
        fields = ["id", "company", "zone", "number", "places", "status"]

    def validate_zone(self, zone):
        company = self._get_company()
        if company and zone.company_id != company.id:
            raise serializers.ValidationError("Зона принадлежит другой компании.")
        return zone


class WarehouseSerializer(CompanyReadOnlyMixin):
    class Meta:
        ref_name = "CafeWarehouse"
        model = Warehouse
        fields = ["id", "company", "title", "unit", "remainder", "minimum"]


class PurchaseSerializer(CompanyReadOnlyMixin):
    class Meta:
        model = Purchase
        fields = ["id", "company", "supplier", "positions", "price"]


class CategorySerializer(CompanyReadOnlyMixin):
    class Meta:
        model = Category
        fields = ["id", "company", "title"]


# --------- Меню и ингредиенты ---------
class IngredientInlineSerializer(serializers.ModelSerializer):
    product_title = serializers.CharField(source="product.title", read_only=True)
    product_unit = serializers.CharField(source="product.unit", read_only=True)

    class Meta:
        model = Ingredient
        fields = ["id", "product", "product_title", "product_unit", "amount"]
        read_only_fields = ["id", "product_title", "product_unit"]

    def validate_product(self, product):
        company = getattr(getattr(self.context.get("request"), "user", None), "company", None)
        if company and product.company_id != company.id:
            raise serializers.ValidationError("Товар склада принадлежит другой компании.")
        return product


class MenuItemSerializer(CompanyReadOnlyMixin):
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all())
    ingredients = IngredientInlineSerializer(many=True, required=False)

    class Meta:
        model = MenuItem
        fields = [
            "id", "company", "title", "category", "price", "is_active",
            "created_at", "updated_at", "ingredients",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_category(self, category):
        company = self._get_company()
        if company and category.company_id != company.id:
            raise serializers.ValidationError("Категория принадлежит другой компании.")
        return category

    def _upsert_ingredients(self, menu_item, ing_list, company):
        to_create = []
        for ing in ing_list:
            product = ing["product"]
            amount = ing["amount"]
            if company and product.company_id != company.id:
                raise serializers.ValidationError("Товар склада принадлежит другой компании.")
            to_create.append(Ingredient(menu_item=menu_item, product=product, amount=amount))
        if to_create:
            Ingredient.objects.bulk_create(to_create)

    def create(self, validated_data):
        ingredients = validated_data.pop("ingredients", [])
        obj = super().create(validated_data)
        if ingredients:
            self._upsert_ingredients(obj, ingredients, obj.company)
        return obj

    def update(self, instance, validated_data):
        ingredients = validated_data.pop("ingredients", None)
        obj = super().update(instance, validated_data)
        if ingredients is not None:
            instance.ingredients.all().delete()
            if ingredients:
                self._upsert_ingredients(instance, ingredients, instance.company)
        return obj


# --------- Бронь ---------
class BookingSerializer(CompanyReadOnlyMixin):
    table = serializers.PrimaryKeyRelatedField(queryset=Table.objects.all())

    class Meta:
        model = Booking
        fields = [
            "id", "company", "guest", "phone", "date", "time", "guests", "table",
            "status", "created_at", "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_table(self, table):
        company = self._get_company()
        if company and table.company_id != company.id:
            raise serializers.ValidationError("Стол принадлежит другой компании.")
        return table


# --------- Заказы ---------
class OrderItemInlineSerializer(serializers.ModelSerializer):
    menu_item_title = serializers.CharField(source="menu_item.title", read_only=True)
    menu_item_price = serializers.DecimalField(source="menu_item.price", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "menu_item", "menu_item_title", "menu_item_price", "quantity"]
        read_only_fields = ["id", "menu_item_title", "menu_item_price"]

    def validate_menu_item(self, menu_item):
        company = getattr(getattr(self.context.get("request"), "user", None), "company", None)
        if company and menu_item.company_id != company.id:
            raise serializers.ValidationError("Позиция меню принадлежит другой компании.")
        return menu_item


# краткая карточка заказа для вложенного чтения в клиенте
class OrderBriefSerializer(serializers.ModelSerializer):
    table_number = serializers.IntegerField(source="table.number", read_only=True)

    class Meta:
        model = Order
        fields = ["id", "table_number", "guests", "waiter", "created_at"]


# --------- История заказов (архив) ---------
class OrderItemHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemHistory
        fields = ["id", "menu_item", "menu_item_title", "menu_item_price", "quantity"]
        read_only_fields = fields = ["id", "menu_item", "menu_item_title", "menu_item_price", "quantity"]


class OrderHistorySerializer(serializers.ModelSerializer):
    items = OrderItemHistorySerializer(many=True, read_only=True)

    class Meta:
        model = OrderHistory
        fields = [
            "id", "original_order_id", "company", "client",
            "table", "table_number", "waiter", "waiter_label",
            "guests", "created_at", "archived_at", "items",
        ]
        read_only_fields = fields


# клиент кафе: с вложенными заказами (read-only) и историей (read-only)
class CafeClientSerializer(CompanyReadOnlyMixin):
    orders = OrderBriefSerializer(many=True, read_only=True)
    history = OrderHistorySerializer(source="order_history", many=True, read_only=True)

    class Meta:
        model = CafeClient
        fields = ["id", "company", "name", "phone", "notes", "orders", "history"]


class OrderSerializer(CompanyReadOnlyMixin):
    table = serializers.PrimaryKeyRelatedField(queryset=Table.objects.all())
    client = serializers.PrimaryKeyRelatedField(
        queryset=CafeClient.objects.all(), required=False, allow_null=True
    )
    waiter = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),  # при необходимости сузьте: .filter(role='waiter')
        allow_null=True,
        required=False
    )
    items = OrderItemInlineSerializer(many=True, required=False)

    class Meta:
        ref_name = "CafeOrder"
        model = Order
        fields = ["id", "company", "table", "client", "waiter", "guests", "created_at", "items"]
        read_only_fields = ["created_at"]

    def validate(self, attrs):
        company = (self.instance.company if self.instance else self._get_company())
        table = attrs.get("table", getattr(self.instance, "table", None))
        waiter = attrs.get("waiter", getattr(self.instance, "waiter", None))
        client = attrs.get("client", getattr(self.instance, "client", None))

        if company and table and table.company_id != company.id:
            raise serializers.ValidationError({"table": "Стол принадлежит другой компании."})
        if company and waiter and getattr(waiter, "company_id", None) != company.id:
            raise serializers.ValidationError({"waiter": "Официант принадлежит другой компании."})
        if company and client and client.company_id != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        return attrs

    def _upsert_items(self, order, items, company):
        for it in items:
            menu_item = it["menu_item"]
            qty = it.get("quantity", 1)
            if company and menu_item.company_id != company.id:
                raise serializers.ValidationError("Позиция меню принадлежит другой компании.")
            existing = order.items.filter(menu_item=menu_item).first()
            if existing:
                existing.quantity += qty
                existing.save(update_fields=["quantity"])
            else:
                OrderItem.objects.create(
                    order=order,
                    menu_item=menu_item,
                    quantity=qty,
                    **({"company": order.company} if hasattr(OrderItem, "company") else {})
                )

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        obj = super().create(validated_data)
        if items:
            self._upsert_items(obj, items, obj.company)
        return obj

    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        obj = super().update(instance, validated_data)
        if items is not None:
            instance.items.all().delete()
            if items:
                self._upsert_items(instance, items, instance.company)
        return obj
