from django.contrib import admin
from .models import (
    SalesFunnel, FunnelStage, Contact, Lead, Deal, Activity,
    MetaBusinessAccount, WhatsAppBusinessAccount, InstagramBusinessAccount,
    Conversation, Message, MessageTemplate
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


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ['title', 'activity_type', 'contact', 'lead', 'deal', 'user', 'activity_date']
    list_filter = ['activity_type', 'activity_date', 'company']
    search_fields = ['title', 'description']
    readonly_fields = ['id', 'created_at']


# ==================== META INTEGRATION ADMIN ====================


@admin.register(MetaBusinessAccount)
class MetaBusinessAccountAdmin(admin.ModelAdmin):
    list_display = ['business_name', 'business_id', 'company', 'is_active', 'is_verified', 'created_at']
    list_filter = ['is_active', 'is_verified', 'created_at']
    search_fields = ['business_name', 'business_id', 'company__name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Основное', {
            'fields': ('id', 'company', 'business_id', 'business_name', 'is_active', 'is_verified')
        }),
        ('Токены и безопасность', {
            'fields': ('access_token', 'webhook_verify_token', 'webhook_secret'),
            'classes': ('collapse',),
        }),
        ('Метаданные', {
            'fields': ('metadata', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(WhatsAppBusinessAccount)
class WhatsAppBusinessAccountAdmin(admin.ModelAdmin):
    list_display = [
        'display_name', 'phone_number', 'waba_id', 
        'quality_rating', 'is_active', 'is_verified', 'created_at'
    ]
    list_filter = ['is_active', 'is_verified', 'quality_rating', 'created_at']
    search_fields = ['display_name', 'phone_number', 'waba_id', 'phone_number_id']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Основное', {
            'fields': ('id', 'meta_account', 'display_name', 'phone_number', 'is_active', 'is_verified')
        }),
        ('Meta ID', {
            'fields': ('waba_id', 'phone_number_id'),
        }),
        ('Качество', {
            'fields': ('quality_rating', 'messaging_limit'),
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(InstagramBusinessAccount)
class InstagramBusinessAccountAdmin(admin.ModelAdmin):
    list_display = ['username', 'name', 'instagram_id', 'followers_count', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['username', 'name', 'instagram_id']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Основное', {
            'fields': ('id', 'meta_account', 'username', 'name', 'is_active')
        }),
        ('Meta ID', {
            'fields': ('instagram_id', 'facebook_page_id'),
        }),
        ('Профиль', {
            'fields': ('profile_picture_url', 'followers_count'),
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = [
        'participant_name', 'channel', 'status', 'contact', 
        'assigned_to', 'unread_count', 'last_message_at'
    ]
    list_filter = ['channel', 'status', 'created_at']
    search_fields = ['participant_name', 'participant_id', 'participant_username']
    readonly_fields = ['id', 'created_at', 'updated_at', 'messages_count']
    raw_id_fields = ['contact', 'lead', 'assigned_to', 'whatsapp_account', 'instagram_account']
    
    fieldsets = (
        ('Основное', {
            'fields': ('id', 'company', 'channel', 'status')
        }),
        ('Участник', {
            'fields': ('participant_id', 'participant_name', 'participant_username'),
        }),
        ('Аккаунты', {
            'fields': ('whatsapp_account', 'instagram_account'),
        }),
        ('CRM', {
            'fields': ('contact', 'lead', 'assigned_to'),
        }),
        ('Статистика', {
            'fields': ('unread_count', 'messages_count', 'last_message_text', 'last_message_at'),
        }),
        ('Окно сообщений', {
            'fields': ('window_expires_at',),
            'classes': ('collapse',),
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = [
        'short_text', 'conversation', 'direction', 
        'message_type', 'status', 'timestamp'
    ]
    list_filter = ['direction', 'message_type', 'status', 'timestamp']
    search_fields = ['text', 'meta_message_id']
    readonly_fields = ['id', 'created_at']
    raw_id_fields = ['conversation', 'sender_user', 'reply_to']
    
    def short_text(self, obj):
        if obj.text:
            return obj.text[:50] + '...' if len(obj.text) > 50 else obj.text
        return f'[{obj.message_type}]'
    short_text.short_description = 'Сообщение'
    
    fieldsets = (
        ('Основное', {
            'fields': ('id', 'conversation', 'meta_message_id', 'direction', 'sender_user')
        }),
        ('Контент', {
            'fields': ('message_type', 'text'),
        }),
        ('Медиа', {
            'fields': ('media_id', 'media_url', 'media_mime_type', 'media_filename', 'media_caption'),
            'classes': ('collapse',),
        }),
        ('Локация', {
            'fields': ('location_latitude', 'location_longitude', 'location_name', 'location_address'),
            'classes': ('collapse',),
        }),
        ('Ответ', {
            'fields': ('reply_to', 'context'),
            'classes': ('collapse',),
        }),
        ('Статус', {
            'fields': ('status', 'error_code', 'error_message', 'is_read', 'read_at'),
        }),
        ('Метаданные', {
            'fields': ('metadata', 'timestamp', 'created_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'language', 'category', 'status', 'whatsapp_account', 'is_active']
    list_filter = ['category', 'status', 'language', 'is_active']
    search_fields = ['name', 'template_id']
    readonly_fields = ['id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Основное', {
            'fields': ('id', 'whatsapp_account', 'name', 'language', 'is_active')
        }),
        ('Meta', {
            'fields': ('template_id', 'category', 'status', 'rejection_reason'),
        }),
        ('Контент', {
            'fields': ('components', 'example_values'),
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
