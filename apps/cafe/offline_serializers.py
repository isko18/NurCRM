"""
Упрощённые сериализаторы для офлайн-режима кафе.

Намеренно НЕ переиспользуют тяжёлые сериализаторы из apps/cafe/serializers.py —
тут только плоские read-структуры под snapshot и валидация входной очереди sync.
"""
from decimal import Decimal

from rest_framework import serializers


def money(value) -> str:
    """Decimal -> строка с двумя знаками ('350.00'). None -> '0.00'."""
    return str((value or Decimal("0")).quantize(Decimal("0.01")))


# ============================================================
# SNAPSHOT (read-only)
# ============================================================
class OfflineCategorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(source="title")
    sort_order = serializers.SerializerMethodField()

    def get_sort_order(self, obj):
        # У модели Category нет поля sort_order — проставляем порядковый номер,
        # вычисленный во view (obj._sort_order), либо 0.
        return getattr(obj, "_sort_order", 0)


class OfflineMenuItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(source="title")
    category_id = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    unit = serializers.SerializerMethodField()
    is_available = serializers.BooleanField(source="is_active")
    image_url = serializers.SerializerMethodField()

    def get_category_id(self, obj):
        return str(obj.category_id) if obj.category_id else None

    def get_price(self, obj):
        return money(obj.price)

    def get_unit(self, obj):
        # Универсального поля «единица» в модели нет. Для весовых блюд — sale_unit (kg/g),
        # для штучных — нейтральное "шт".
        if obj.is_sold_by_weight:
            return (obj.sale_unit or "kg").strip().lower()
        return "шт"

    def get_image_url(self, obj):
        if not getattr(obj, "image", None):
            return None
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url


class OfflineTableSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()
    number = serializers.IntegerField()
    hall_id = serializers.SerializerMethodField()
    hall_name = serializers.SerializerMethodField()
    capacity = serializers.IntegerField(source="places")
    status = serializers.SerializerMethodField()

    def get_name(self, obj):
        return f"Стол {obj.number}"

    def get_hall_id(self, obj):
        return str(obj.zone_id) if obj.zone_id else None

    def get_hall_name(self, obj):
        return obj.zone.title if obj.zone_id else None

    def get_status(self, obj):
        # Модель Table знает только free/busy. occupied=busy.
        # reserved выводим из активной брони на сегодня (проставляется во view как obj._reserved).
        if obj.status == "busy":
            return "occupied"
        if getattr(obj, "_reserved", False):
            return "reserved"
        return "free"


class OfflineOrderItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    menu_item_id = serializers.SerializerMethodField()
    menu_item_name = serializers.SerializerMethodField()
    quantity = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()

    def get_menu_item_id(self, obj):
        return str(obj.menu_item_id) if obj.menu_item_id else None

    def get_menu_item_name(self, obj):
        if obj.menu_item_id:
            return obj.menu_item.title
        return obj.service_title or ""

    def get_quantity(self, obj):
        q = obj.quantity or Decimal("0")
        # целые отдаём как целые, дробные (весовые) — строкой
        return int(q) if q == q.to_integral_value() else str(q)

    def get_price(self, obj):
        from .offline_serializers import money  # self-ref ок
        if obj.unit_price is not None:
            return money(obj.unit_price)
        if obj.menu_item_id:
            return money(obj.menu_item.price)
        return "0.00"


class OfflineOrderSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    table_id = serializers.SerializerMethodField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    items = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()

    def get_table_id(self, obj):
        return str(obj.table_id) if obj.table_id else None

    def get_items(self, obj):
        rows = [it for it in obj.items.all() if not it.is_rejected]
        return OfflineOrderItemSerializer(rows, many=True, context=self.context).data

    def get_total(self, obj):
        return money(obj.total_amount)


class OfflineKitchenTaskSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    order = serializers.SerializerMethodField()
    order_item = serializers.SerializerMethodField()
    menu_item = serializers.SerializerMethodField()
    menu_item_title = serializers.CharField(source="menu_item.title")
    table_number = serializers.SerializerMethodField()
    guest = serializers.SerializerMethodField()
    waiter = serializers.SerializerMethodField()
    waiter_label = serializers.SerializerMethodField()
    cook = serializers.SerializerMethodField()
    unit_index = serializers.IntegerField()
    price = serializers.SerializerMethodField()

    def get_order(self, obj):
        return str(obj.order_id) if obj.order_id else None

    def get_order_item(self, obj):
        return str(obj.order_item_id) if obj.order_item_id else None

    def get_menu_item(self, obj):
        return str(obj.menu_item_id) if obj.menu_item_id else None

    def get_table_number(self, obj):
        if obj.order_id and obj.order.table_id:
            return obj.order.table.number
        return None

    def get_guest(self, obj):
        client = getattr(obj.order, "client", None) if obj.order_id else None
        if client:
            return client.name or client.phone or ""
        return ""

    def get_waiter(self, obj):
        return obj.waiter_id

    def get_waiter_label(self, obj):
        if not obj.waiter_id:
            return ""
        w = obj.waiter
        full = (getattr(w, "get_full_name", lambda: "")() or "").strip()
        return full or getattr(w, "email", "") or str(getattr(w, "id", ""))

    def get_cook(self, obj):
        return obj.cook_id

    def get_price(self, obj):
        return money(obj.menu_item.price if obj.menu_item_id else None)


# ============================================================
# SYNC (write — валидация входной очереди)
# ============================================================
class OfflineActionSerializer(serializers.Serializer):
    TYPES = [
        "CREATE_ORDER",
        "ADD_ITEM_TO_ORDER",
        "REMOVE_ITEM_FROM_ORDER",
        "CLOSE_ORDER",
        "CANCEL_ORDER",
        "CLAIM_TASK",
        "READY_TASK",
    ]

    type = serializers.ChoiceField(choices=TYPES)
    payload = serializers.DictField(required=False, default=dict)
    created_at = serializers.DateTimeField()


class OfflineSyncRequestSerializer(serializers.Serializer):
    actions = OfflineActionSerializer(many=True)
