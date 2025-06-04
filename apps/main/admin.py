from django.contrib import admin
from apps.main.models import (
    Contact, Pipeline, Deal, Task,
    Integration, Analytics, Order,
    Product, Review
)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'company', 'department', 'owner', 'created_at')
    list_filter = ('company', 'department')
    search_fields = ('name', 'email', 'company')


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'created_at')
    search_fields = ('name',)


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'value', 'pipeline', 'stage', 'contact', 'assigned_to', 'created_at')
    list_filter = ('status', 'pipeline')
    search_fields = ('title', 'contact__name')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'due_date', 'assigned_to', 'deal')
    list_filter = ('status', 'due_date')
    search_fields = ('title', 'description')


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ('type', 'status', 'created_at')
    list_filter = ('type', 'status')


@admin.register(Analytics)
class AnalyticsAdmin(admin.ModelAdmin):
    list_display = ('type', 'created_at')
    search_fields = ('data',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer_name', 'status', 'total', 'quantity', 'department', 'date_ordered')
    list_filter = ('status', 'department')
    search_fields = ('order_number', 'customer_name')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'article', 'brand', 'category', 'quantity', 'price', 'created_at')
    list_filter = ('brand', 'category')
    search_fields = ('name', 'article')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('user', 'rating', 'created_at')
    list_filter = ('rating',)
    search_fields = ('user__email', 'comment')
