from django.contrib import admin
from mptt.admin import DraggableMPTTAdmin

from apps.main.models import (
    Contact, Pipeline, Deal, Task,
    Integration, Analytics, Order, OrderItem,
    Product, Review, Notification, Event,
    Warehouse, WarehouseEvent,
    ProductCategory, ProductBrand, Client, GlobalProduct, GlobalBrand, GlobalCategory, CartItem, Cart, Sale, SaleItem, ClientDeal
)

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'company', 'department', 'owner', 'created_at')
    list_filter = ('company', 'department')
    search_fields = ('name', 'email', 'company__name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'created_at')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'value', 'pipeline', 'stage', 'contact', 'assigned_to', 'created_at')
    list_filter = ('status', 'pipeline')
    search_fields = ('title', 'contact__name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'due_date', 'assigned_to', 'deal')
    list_filter = ('status', 'due_date')
    search_fields = ('title', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ('type', 'status', 'created_at')
    list_filter = ('type', 'status')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Analytics)
class AnalyticsAdmin(admin.ModelAdmin):
    list_display = ('type', 'created_at')
    search_fields = ('data',)
    readonly_fields = ('created_at',)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    readonly_fields = ('total',)
    autocomplete_fields = ('product',)

    def save_new_objects(self, formset, commit=True):
        instances = formset.save(commit=False)
        for obj in instances:
            # Установить цену, если не задана
            obj.price = obj.product.price
            obj.total = obj.price * obj.quantity

            # Проверка на остаток
            if obj.product.quantity < obj.quantity:
                raise ValueError(f"Недостаточно товара '{obj.product.name}' на складе")

            # Вычесть количество
            obj.product.quantity -= obj.quantity
            obj.product.save()

            if commit:
                obj.save()

        formset.save_m2m()


# @admin.register(Order)
# class OrderAdmin(admin.ModelAdmin):
#     list_display = ('order_number', 'customer_name', 'status', 'total_display', 'total_quantity', 'department', 'date_ordered')
#     list_filter = ('status', 'department')
#     search_fields = ('order_number', 'customer_name')
#     readonly_fields = ('created_at', 'updated_at')
#     inlines = [OrderItemInline]

#     def total_display(self, obj):
#         return f"{obj.total:.2f}"
#     total_display.short_description = 'Сумма'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'barcode', 'price', 'quantity', 'brand', 'category')
    search_fields = ('name', 'barcode')
    
admin.site.register(GlobalProduct)
admin.site.register(GlobalCategory)
admin.site.register(GlobalBrand)
admin.site.register(ProductBrand)
admin.site.register(ProductCategory)


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    # Показываем только чтение; добавление позиций через POS, а не через админку
    readonly_fields = ("product", "quantity", "unit_price", "line_total")
    # НЕ добавляем 'line_total' в fields! Оставляем Django выбрать поля формы сам.
    # fields = ("product", "quantity", "unit_price")  # <- можно явно указать, но без line_total

    def line_total(self, obj):
        if not obj or obj.unit_price is None or obj.quantity is None:
            return "-"
        try:
            return obj.unit_price * obj.quantity
        except Exception:
            return "-"
    line_total.short_description = "Сумма"

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = (
        "id", "company", "created_by", "status",
        "subtotal", "discount_total", "tax_total", "total",
        "created_at",
    )
    list_filter = ("company", "status", "created_at")
    search_fields = ("id", "user__username", "user__email")
    inlines = [CartItemInline]

    def created_by(self, obj):
        return obj.user
    created_by.short_description = "Создал"
    created_by.admin_order_field = "user"


# ===== SALE =====
class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    can_delete = False
    # Эти поля у нас снапшоты — делаем их read-only
    readonly_fields = ("product", "name_snapshot", "barcode_snapshot", "unit_price", "quantity", "line_total")
    # НЕ указываем 'line_total' в fields!

    def line_total(self, obj):
        if not obj or obj.unit_price is None or obj.quantity is None:
            return "-"
        try:
            return obj.unit_price * obj.quantity
        except Exception:
            return "-"
    line_total.short_description = "Сумма"

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "id", "company", "user", "status",
        "subtotal", "discount_total", "tax_total", "total",
        "created_at", "paid_at",
    )
    list_filter = ("company", "status", "created_at", "paid_at")
    search_fields = ("id", "user__username", "user__email")
    inlines = [SaleItemInline]

    # Если продажи создаются только через POS — можно запретить создание в админке:
    # def has_add_permission(self, request):
    #     return False

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('user', 'rating', 'created_at')
    list_filter = ('rating',)
    search_fields = ('user__email', 'comment')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'message', 'is_read', 'created_at')
    list_filter = ('is_read', 'created_at')
    search_fields = ('message', 'user__email')
    readonly_fields = ('created_at',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'datetime', 'company', 'created_at')
    list_filter = ('company', 'datetime')
    search_fields = ('title', 'notes')
    filter_horizontal = ('participants',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'location', 'company_name', 'created_at')
    list_filter = ('company', 'location')
    search_fields = ('name', 'location')
    readonly_fields = ('created_at', 'updated_at')

    def company_name(self, obj):
        return obj.company.name
    company_name.admin_order_field = 'company__name'
    company_name.short_description = 'Компания'


@admin.register(WarehouseEvent)
class WarehouseEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'client_name', 'event_date', 'status', 'responsible_person', 'warehouse_name', 'created_at')
    list_filter = ('status', 'event_date', 'warehouse')
    search_fields = ('title', 'client_name', 'description')
    filter_horizontal = ('participants',)
    readonly_fields = ('created_at', 'updated_at')

    def warehouse_name(self, obj):
        return obj.warehouse.name
    warehouse_name.admin_order_field = 'warehouse__name'
    warehouse_name.short_description = 'Склад'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'price', 'total')
    search_fields = ('order__order_number', 'product__name')
    readonly_fields = ('price', 'total')


class CompanyFilteredAdmin(admin.ModelAdmin):
    """
    Ограничивает queryset записями компании пользователя (кроме суперпользователя)
    и проставляет company при создании.
    """
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        user_company_id = getattr(getattr(request, "user", None), "company_id", None)
        if user_company_id:
            qs = qs.filter(company_id=user_company_id)
        return qs

    def save_model(self, request, obj, form, change):
        if not change and not getattr(obj, "company_id", None):
            obj.company_id = getattr(getattr(request, "user", None), "company_id", None)
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        # company редактируем только суперпользователю
        if not request.user.is_superuser:
            ro.append("company")
        return ro


# --- Inline для сделок внутри клиента ---
class ClientDealInline(admin.TabularInline):
    model = ClientDeal
    extra = 0
    fields = ("title", "kind", "amount", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        user_company_id = getattr(getattr(request, "user", None), "company_id", None)
        if user_company_id:
            qs = qs.filter(company_id=user_company_id)
        return qs

    def has_add_permission(self, request, obj=None):
        return True

    def save_new_instance(self, form, commit=True):
        """
        (используется Django >=5 для inline formset) — проставим company из родителя.
        """
        instance = super().save_new_instance(form, commit=False)
        if not getattr(instance, "company_id", None) and instance.client_id:
            instance.company_id = instance.client.company_id
        if commit:
            instance.save()
        return instance

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for inst in instances:
            if not getattr(inst, "company_id", None):
                inst.company_id = getattr(form.instance, "company_id", None)
        super().save_formset(request, form, formset, change)


@admin.register(Client)
class ClientAdmin(CompanyFilteredAdmin):
    list_display = ("full_name", "phone", "email", "date", "status", "created_at")
    list_filter = ("status", ("date", admin.DateFieldListFilter), ("created_at", admin.DateFieldListFilter))
    search_fields = ("full_name", "phone", "email")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "date"
    ordering = ("-created_at",)
    inlines = [ClientDealInline]
    list_per_page = 50

    # чтобы в форме нельзя было выбрать клиентов другой компании (на всякий случай для FK)
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ClientDeal)
class ClientDealAdmin(CompanyFilteredAdmin):
    list_display = ("title", "client", "kind", "amount", "created_at")
    list_filter = ("kind", ("created_at", admin.DateFieldListFilter))
    search_fields = ("title", "client__full_name", "client__phone")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)
    list_select_related = ("client",)
    list_per_page = 50
    autocomplete_fields = ("client",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Фильтруем список клиентов по компании пользователя.
        """
        if db_field.name == "client" and not request.user.is_superuser:
            user_company_id = getattr(getattr(request, "user", None), "company_id", None)
            if user_company_id:
                kwargs["queryset"] = Client.objects.filter(company_id=user_company_id)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)