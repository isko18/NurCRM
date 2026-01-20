from django.contrib import admin
from .models import (
    SalesFunnel, FunnelStage, Contact, Lead, Deal,
    WazzuppAccount, WazzuppMessage, Activity
)


@admin.register(SalesFunnel)
class SalesFunnelAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'company__name']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(FunnelStage)
class FunnelStageAdmin(admin.ModelAdmin):
    list_display = ['name', 'funnel', 'order', 'is_final', 'is_success']
    list_filter = ['funnel', 'is_final', 'is_success']
    search_fields = ['name', 'funnel__name']
    ordering = ['funnel', 'order']
    readonly_fields = ['id', 'created_at']


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'phone', 'email', 'company', 'owner', 'is_active', 'is_client', 'created_at']
    list_filter = ['is_active', 'is_client', 'source', 'created_at', 'company']
    search_fields = ['first_name', 'last_name', 'phone', 'email', 'company_name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    def full_name(self, obj):
        return obj.full_name
    full_name.short_description = 'ФИО'


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ['title', 'contact', 'funnel', 'stage', 'owner', 'estimated_value', 'probability', 'created_at']
    list_filter = ['funnel', 'stage', 'source', 'created_at', 'company']
    search_fields = ['title', 'description', 'contact__first_name', 'contact__phone']
    readonly_fields = ['id', 'created_at', 'updated_at', 'closed_at']


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ['title', 'contact', 'funnel', 'stage', 'owner', 'amount', 'is_won', 'is_lost', 'created_at']
    list_filter = ['funnel', 'stage', 'is_won', 'is_lost', 'created_at', 'company']
    search_fields = ['title', 'description', 'contact__first_name', 'contact__phone']
    readonly_fields = ['id', 'created_at', 'updated_at', 'closed_at']


@admin.register(WazzuppAccount)
class WazzuppAccountAdmin(admin.ModelAdmin):
    list_display = ['company', 'integration_type', 'instance_id', 'is_active', 'is_connected', 'last_sync']
    list_filter = ['integration_type', 'is_active', 'is_connected', 'created_at']
    search_fields = ['company__name', 'instance_id']
    readonly_fields = ['id', 'created_at', 'updated_at', 'last_sync']


@admin.register(WazzuppMessage)
class WazzuppMessageAdmin(admin.ModelAdmin):
    list_display = ['message_id', 'account', 'contact', 'from_number', 'message_type', 'is_incoming', 'status', 'timestamp']
    list_filter = ['account', 'message_type', 'is_incoming', 'status', 'timestamp']
    search_fields = ['message_id', 'from_number', 'to_number', 'text']
    readonly_fields = ['id', 'created_at']


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ['title', 'activity_type', 'contact', 'lead', 'deal', 'user', 'activity_date']
    list_filter = ['activity_type', 'activity_date', 'company']
    search_fields = ['title', 'description']
    readonly_fields = ['id', 'created_at']
