# apps/cafe/admin.py
from django.contrib import admin
from django.forms.models import BaseInlineFormSet

from .models import (
    CafeClient, Order, OrderItem, Table, MenuItem,
    OrderHistory, OrderItemHistory,
)

# -----------------------------
# Inline заказа на странице клиента
# -----------------------------
class OrderInlineFormSet(BaseInlineFormSet):
    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        # клиент — это родитель инлайна
        obj.client = self.instance
        obj.company = self.instance.company
        if commit:
            obj.full_clean()  # проверит согласованность компании/стола/клиента
            obj.save()
            form.save_m2m()
        return obj

    def save_existing(self, form, instance, commit=True):
        obj = super().save_existing(form, instance, commit=False)
        # на всякий случай фиксируем соответствие при редактировании
        obj.client = self.instance
        obj.company = self.instance.company
        if commit:
            obj.full_clean()
            obj.save()
            form.save_m2m()
        return obj


class OrderInline(admin.TabularInline):
    model = Order
    formset = OrderInlineFormSet
    extra = 1
    fields = ("table", "guests", "waiter", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("table",)  # waiter оставим без автокомплита, если UserAdmin не настроен

    # фильтруем FK по компании клиента
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        obj = getattr(request, "_cafe_client_admin_obj", None)
        if obj and db_field.name == "table":
            ff.queryset = Table.objects.filter(company=obj.company)
        return ff


@admin.register(CafeClient)
class CafeClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "company")
    list_filter = ("company",)
    search_fields = ("name", "phone")
    ordering = ("company", "name")
    inlines = [OrderInline]

    def get_form(self, request, obj=None, **kwargs):
        # прокидываем текущего клиента в инлайн для фильтрации списков
        request._cafe_client_admin_obj = obj
        return super().get_form(request, obj, **kwargs)


# -----------------------------
# Inline позиций в заказе
# -----------------------------
class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ("menu_item",)
    fields = ("menu_item", "quantity")
    # company у OrderItem ставится автоматически в save()


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "client", "table", "guests", "waiter", "created_at")
    list_filter = ("company", "created_at")
    # В search_fields используем только текстовые поля
    search_fields = ("client__name", "client__phone", "waiter__email")
    ordering = ("-created_at",)
    list_select_related = ("company", "client", "table", "waiter")
    inlines = [OrderItemInline]
    readonly_fields = ("created_at",)
    autocomplete_fields = ("client", "table", "waiter")

    def save_model(self, request, obj, form, change):
        # Автопроставляем company, если не задано явно
        if not obj.company_id:
            if obj.client_id:
                obj.company = obj.client.company
            elif obj.table_id:
                obj.company = obj.table.company
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Если у редактируемого заказа уже есть компания — ограничим списки.
        Для страницы создания можно не ограничивать (или ограничить по request.user.company, если нужно).
        """
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        obj = getattr(self, "_order_admin_obj", None)
        company = getattr(obj, "company", None) if obj else None
        if company:
            if db_field.name == "table":
                ff.queryset = Table.objects.filter(company=company)
            if db_field.name == "client":
                ff.queryset = CafeClient.objects.filter(company=company)
        return ff

    def get_form(self, request, obj=None, **kwargs):
        self._order_admin_obj = obj
        return super().get_form(request, obj, **kwargs)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "menu_item", "quantity", "company")
    list_filter = ("company",)
    # текстовый поиск — по связанным текстовым полям
    search_fields = ("order__client__name", "order__client__phone", "menu_item__title")
    list_select_related = ("order", "menu_item")


# -----------------------------
# Доп. админки (удобно иметь под рукой)
# -----------------------------
@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("number", "zone", "company", "places", "status")
    list_filter = ("company", "status", "zone")
    search_fields = ("zone__title",)
    ordering = ("company", "zone", "number")
    autocomplete_fields = ()


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("title", "image", "category", "company", "price", "is_active", "created_at")
    list_filter = ("company", "is_active", "category")
    search_fields = ("title", "category__title")
    ordering = ("title",)
    list_select_related = ("category", "company")


# -----------------------------
# История заказов (архив)
# -----------------------------
class OrderItemHistoryInline(admin.TabularInline):
    model = OrderItemHistory
    extra = 0
    can_delete = False
    readonly_fields = ("menu_item", "menu_item_title", "menu_item_price", "quantity")
    fields = ("menu_item_title", "menu_item_price", "quantity")


@admin.register(OrderHistory)
class OrderHistoryAdmin(admin.ModelAdmin):
    list_display = ("original_order_id", "company", "client", "table_number", "guests", "created_at", "archived_at")
    list_filter = ("company", "created_at", "archived_at")
    search_fields = ("client__name", "client__phone")
    ordering = ("-created_at",)
    inlines = [OrderItemHistoryInline]
    readonly_fields = [f.name for f in OrderHistory._meta.fields]
