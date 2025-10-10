# apps/cafe/admin.py
from django.contrib import admin
from django.db.models import Q
from django.forms.models import BaseInlineFormSet

from .models import (
    CafeClient, Order, OrderItem, Table, MenuItem,
    OrderHistory, OrderItemHistory,
    Zone,  # для фильтров по связям
)

@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display  = ("title", "company")   # если у Zone есть branch, можно: ("title", "company", "branch")
    list_filter   = ("company",)           # и добавить "branch" при наличии поля
    search_fields = ("title",)
    ordering      = ("company", "title")
# -----------------------------
# Inline заказа на странице клиента
# -----------------------------
class OrderInlineFormSet(BaseInlineFormSet):
    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        # клиент — это родитель инлайна
        obj.client = self.instance
        obj.company = self.instance.company
        # филиал наследуем от клиента (глобальный или конкретный)
        obj.branch = getattr(self.instance, "branch", None)
        if commit:
            obj.full_clean()
            obj.save()
            form.save_m2m()
        return obj

    def save_existing(self, form, instance, commit=True):
        obj = super().save_existing(form, instance, commit=False)
        obj.client = self.instance
        obj.company = self.instance.company
        obj.branch = getattr(self.instance, "branch", None)
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
    autocomplete_fields = ("table",)  # waiter оставим без автокомплита

    # фильтруем FK по компании/филиалу клиента
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        obj = getattr(request, "_cafe_client_admin_obj", None)
        if obj and db_field.name == "table":
            # разрешаем глобальные (branch IS NULL) и этого филиала
            if getattr(obj, "branch_id", None):
                ff.queryset = Table.objects.filter(
                    company=obj.company
                ).filter(Q(branch__isnull=True) | Q(branch=obj.branch))
            else:
                ff.queryset = Table.objects.filter(company=obj.company, branch__isnull=True)
        return ff


@admin.register(CafeClient)
class CafeClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "company", "branch")
    list_filter = ("company", "branch")
    search_fields = ("name", "phone")
    ordering = ("company", "branch", "name")
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

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        order_obj = getattr(self, "_parent_order_obj", None)
        if db_field.name == "menu_item" and order_obj:
            # фильтруем позиции меню по company и видимости филиала заказа
            if getattr(order_obj, "branch_id", None):
                ff.queryset = MenuItem.objects.filter(
                    company=order_obj.company
                ).filter(Q(branch__isnull=True) | Q(branch=order_obj.branch))
            else:
                ff.queryset = MenuItem.objects.filter(company=order_obj.company, branch__isnull=True)
        return ff

    def get_formset(self, request, obj=None, **kwargs):
        # нужно, чтобы formfield_for_foreignkey знал текущий заказ
        self._parent_order_obj = obj
        return super().get_formset(request, obj, **kwargs)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "branch", "client", "table", "guests", "waiter", "created_at")
    list_filter = ("company", "branch", "created_at")
    # В search_fields используем только текстовые поля
    search_fields = ("client__name", "client__phone", "waiter__email")
    ordering = ("-created_at",)
    list_select_related = ("company", "client", "table", "waiter", "branch")
    inlines = [OrderItemInline]
    readonly_fields = ("created_at",)
    autocomplete_fields = ("client", "table", "waiter")

    def save_model(self, request, obj, form, change):
        # Автопроставляем company/branch, если не заданы явно
        if not obj.company_id:
            if obj.client_id:
                obj.company = obj.client.company
            elif obj.table_id:
                obj.company = obj.table.company
        if obj.branch_id is None:
            # унаследуем филиал: приоритет — клиент, затем стол
            if obj.client_id:
                obj.branch = getattr(obj.client, "branch", None)
            elif obj.table_id:
                obj.branch = getattr(obj.table, "branch", None)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Если у редактируемого заказа уже есть компания/филиал — ограничим списки.
        Для страницы создания можно ориентироваться на выбранного клиента/стол (после выбора).
        """
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        obj = getattr(self, "_order_admin_obj", None)
        company = getattr(obj, "company", None) if obj else None
        branch = getattr(obj, "branch", None) if obj else None

        if company:
            if db_field.name == "table":
                qs = Table.objects.filter(company=company)
                ff.queryset = qs.filter(Q(branch__isnull=True) | Q(branch=branch)) if branch \
                              else qs.filter(branch__isnull=True)
            if db_field.name == "client":
                qs = CafeClient.objects.filter(company=company)
                ff.queryset = qs.filter(Q(branch__isnull=True) | Q(branch=branch)) if branch \
                              else qs.filter(branch__isnull=True)
            if db_field.name == "waiter" and hasattr(ff, "queryset"):
                # если в User есть company_id — сузим
                try:
                    ff.queryset = ff.queryset.filter(company_id=company.id)
                except Exception:
                    pass
        return ff

    def get_form(self, request, obj=None, **kwargs):
        self._order_admin_obj = obj
        return super().get_form(request, obj, **kwargs)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "menu_item", "quantity", "company")
    list_filter = ("company", "order__branch")
    # текстовый поиск — по связанным текстовым полям
    search_fields = ("order__client__name", "order__client__phone", "menu_item__title")
    list_select_related = ("order", "menu_item")


# -----------------------------
# Доп. админки (удобно иметь под рукой)
# -----------------------------
@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("number", "zone", "company", "branch", "places", "status")
    list_filter = ("company", "branch", "status", "zone")
    search_fields = ("zone__title",)
    ordering = ("company", "branch", "zone", "number")
    autocomplete_fields = ("zone",)


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("title", "image", "category", "company", "branch", "price", "is_active", "created_at")
    list_filter = ("company", "branch", "is_active", "category")
    search_fields = ("title", "category__title")
    ordering = ("title",)
    list_select_related = ("category", "company", "branch")


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
    list_display = ("original_order_id", "company", "branch", "client", "table_number", "guests", "created_at", "archived_at")
    list_filter = ("company", "branch", "created_at", "archived_at")
    search_fields = ("client__name", "client__phone")
    ordering = ("-created_at",)
    inlines = [OrderItemHistoryInline]
    readonly_fields = [f.name for f in OrderHistory._meta.fields]
