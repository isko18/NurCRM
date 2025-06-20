from django.contrib import admin
from mptt.admin import DraggableMPTTAdmin

from apps.main.models import (
    Contact, Pipeline, Deal, Task,
    Integration, Analytics, Order,
    Product, Review, Notification, Event,
    Warehouse, WarehouseEvent,
    ProductCategory, ProductBrand  # ✅ добавлены новые модели
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


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer_name', 'status', 'total', 'quantity', 'department', 'date_ordered')
    list_filter = ('status', 'department')
    search_fields = ('order_number', 'customer_name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'article', 'brand', 'category', 'quantity', 'price', 'created_at')
    list_filter = ('brand', 'category')
    search_fields = ('name', 'article')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ProductCategory)
class ProductCategoryAdmin(DraggableMPTTAdmin):
    mptt_indent_field = "name"
    list_display = ('tree_actions', 'indented_title',)
    search_fields = ('name',)

@admin.register(ProductBrand)
class ProductBrandAdmin(DraggableMPTTAdmin):
    mptt_indent_field = "name"
    list_display = ('tree_actions', 'indented_title',)
    search_fields = ('name',)


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
