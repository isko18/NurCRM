from rest_framework import serializers
from .models import (
    SalesFunnel, FunnelStage, Contact, Lead, Deal, Activity,
    MetaBusinessAccount, WhatsAppBusinessAccount, InstagramBusinessAccount,
    Conversation, Message, MessageTemplate
)
from apps.users.models import Company, User, Branch


class FunnelStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = FunnelStage
        fields = [
            'id', 'name', 'order', 'color', 'is_final', 'is_success',
            'created_at'
        ]


class SalesFunnelSerializer(serializers.ModelSerializer):
    stages = FunnelStageSerializer(many=True, read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)
    
    class Meta:
        model = SalesFunnel
        fields = [
            'id', 'company', 'company_name', 'name', 'description',
            'is_active', 'stages', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SalesFunnelCreateSerializer(serializers.ModelSerializer):
    stages = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
        help_text='Список стадий воронки'
    )
    
    class Meta:
        model = SalesFunnel
        fields = ['id', 'company', 'name', 'description', 'is_active', 'stages']
        read_only_fields = ['id']
    
    def create(self, validated_data):
        stages_data = validated_data.pop('stages', [])
        funnel = SalesFunnel.objects.create(**validated_data)
        
        # Создаем стадии по умолчанию, если не указаны
        if not stages_data:
            default_stages = [
                {'name': 'Новый лид', 'order': 1, 'color': '#3498db'},
                {'name': 'Квалификация', 'order': 2, 'color': '#9b59b6'},
                {'name': 'Предложение', 'order': 3, 'color': '#f39c12'},
                {'name': 'Переговоры', 'order': 4, 'color': '#e67e22'},
                {'name': 'Закрыта успешно', 'order': 5, 'color': '#27ae60', 'is_final': True, 'is_success': True},
                {'name': 'Закрыта неудачно', 'order': 6, 'color': '#e74c3c', 'is_final': True, 'is_success': False},
            ]
            stages_data = default_stages
        
        for stage_data in stages_data:
            FunnelStage.objects.create(funnel=funnel, **stage_data)
        
        return funnel


class ContactSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    full_name = serializers.CharField(read_only=True)
    
    class Meta:
        ref_name = "CrmContactSerializer"
        model = Contact
        fields = [
            'id', 'company', 'company_name', 'branch', 'branch_name',
            'owner', 'owner_name', 'first_name', 'last_name', 'middle_name',
            'full_name', 'phone', 'phone_secondary', 'email', 'whatsapp',
            'instagram', 'company_name', 'position', 'address', 'notes',
            'tags', 'source', 'is_active', 'is_client',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ContactCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = [
            'id', 'company', 'branch', 'owner', 'first_name', 'last_name',
            'middle_name', 'phone', 'phone_secondary', 'email', 'whatsapp',
            'instagram', 'company_name', 'position', 'address', 'notes',
            'tags', 'source', 'is_active', 'is_client'
        ]
        read_only_fields = ['id']


class LeadSerializer(serializers.ModelSerializer):
    contact = ContactSerializer(read_only=True)
    contact_id = serializers.UUIDField(write_only=True, required=False)
    stage_name = serializers.CharField(source='stage.name', read_only=True)
    funnel_name = serializers.CharField(source='funnel.name', read_only=True)
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True)
    
    class Meta:
        ref_name = "CrmLeadSerializer"
        model = Lead
        fields = [
            'id', 'company', 'contact', 'contact_id', 'funnel', 'funnel_name',
            'stage', 'stage_name', 'owner', 'owner_name', 'title', 'description',
            'estimated_value', 'probability', 'source', 'created_at', 'updated_at',
            'closed_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'closed_at']


class LeadCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = [
            'id', 'company', 'contact', 'funnel', 'stage', 'owner',
            'title', 'description', 'estimated_value', 'probability', 'source'
        ]
        read_only_fields = ['id']
    
    def create(self, validated_data):
        lead = Lead.objects.create(**validated_data)
        # Если стадия не указана, берем первую стадию воронки
        if not lead.stage:
            first_stage = lead.funnel.stages.order_by('order').first()
            if first_stage:
                lead.stage = first_stage
                lead.save()
        return lead


class DealSerializer(serializers.ModelSerializer):
    contact = ContactSerializer(read_only=True)
    lead_title = serializers.CharField(source='lead.title', read_only=True)
    stage_name = serializers.CharField(source='stage.name', read_only=True)
    funnel_name = serializers.CharField(source='funnel.name', read_only=True)
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True)
    
    class Meta:
        ref_name = "CrmDealSerializer"
        model = Deal
        fields = [
            'id', 'company', 'lead', 'lead_title', 'contact', 'funnel',
            'funnel_name', 'stage', 'stage_name', 'owner', 'owner_name',
            'title', 'description', 'amount', 'probability', 'is_won',
            'is_lost', 'lost_reason', 'expected_close_date', 'created_at',
            'updated_at', 'closed_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'closed_at']


class DealCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Deal
        fields = [
            'id', 'company', 'lead', 'contact', 'funnel', 'stage', 'owner',
            'title', 'description', 'amount', 'probability', 'expected_close_date'
        ]
        read_only_fields = ['id']
    
    def create(self, validated_data):
        deal = Deal.objects.create(**validated_data)
        # Если стадия не указана, берем первую стадию воронки
        if not deal.stage:
            first_stage = deal.funnel.stages.order_by('order').first()
            if first_stage:
                deal.stage = first_stage
                deal.save()
        return deal


class ActivitySerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    contact_name = serializers.CharField(source='contact.full_name', read_only=True)
    lead_title = serializers.CharField(source='lead.title', read_only=True)
    deal_title = serializers.CharField(source='deal.title', read_only=True)
    activity_type_display = serializers.CharField(
        source='get_activity_type_display',
        read_only=True
    )
    
    class Meta:
        model = Activity
        fields = [
            'id', 'company', 'user', 'user_name', 'contact', 'contact_name',
            'lead', 'lead_title', 'deal', 'deal_title', 'activity_type',
            'activity_type_display', 'title', 'description', 'activity_date',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']


# ==================== META INTEGRATION SERIALIZERS ====================


class MetaBusinessAccountSerializer(serializers.ModelSerializer):
    """Сериализатор для Meta Business Account"""
    company_name = serializers.CharField(source='company.name', read_only=True)
    whatsapp_accounts_count = serializers.IntegerField(
        source='whatsapp_accounts.count', 
        read_only=True
    )
    instagram_accounts_count = serializers.IntegerField(
        source='instagram_accounts.count', 
        read_only=True
    )
    
    class Meta:
        model = MetaBusinessAccount
        fields = [
            'id', 'company', 'company_name', 'business_id', 'business_name',
            'access_token', 'webhook_verify_token', 'webhook_secret',
            'is_active', 'is_verified', 'metadata',
            'whatsapp_accounts_count', 'instagram_accounts_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'access_token': {'write_only': True},
            'webhook_secret': {'write_only': True},
        }


class MetaBusinessAccountCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания Meta Business Account"""
    
    class Meta:
        model = MetaBusinessAccount
        fields = [
            'id', 'company', 'business_id', 'business_name',
            'access_token', 'webhook_verify_token', 'webhook_secret'
        ]
        read_only_fields = ['id']


class WhatsAppBusinessAccountSerializer(serializers.ModelSerializer):
    """Сериализатор для WhatsApp Business Account"""
    company_name = serializers.CharField(
        source='meta_account.company.name', 
        read_only=True
    )
    meta_business_name = serializers.CharField(
        source='meta_account.business_name', 
        read_only=True
    )
    quality_rating_display = serializers.CharField(
        source='get_quality_rating_display',
        read_only=True
    )
    
    class Meta:
        model = WhatsAppBusinessAccount
        fields = [
            'id', 'meta_account', 'company_name', 'meta_business_name',
            'waba_id', 'phone_number_id', 'phone_number', 'display_name',
            'quality_rating', 'quality_rating_display', 'messaging_limit',
            'is_active', 'is_verified', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class InstagramBusinessAccountSerializer(serializers.ModelSerializer):
    """Сериализатор для Instagram Business Account"""
    company_name = serializers.CharField(
        source='meta_account.company.name', 
        read_only=True
    )
    meta_business_name = serializers.CharField(
        source='meta_account.business_name', 
        read_only=True
    )
    
    class Meta:
        model = InstagramBusinessAccount
        fields = [
            'id', 'meta_account', 'company_name', 'meta_business_name',
            'instagram_id', 'facebook_page_id', 'username', 'name',
            'profile_picture_url', 'followers_count', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ConversationListSerializer(serializers.ModelSerializer):
    """Сериализатор для списка переписок (краткий)"""
    contact_name = serializers.CharField(source='contact.full_name', read_only=True)
    assigned_to_name = serializers.CharField(
        source='assigned_to.get_full_name', 
        read_only=True
    )
    channel_display = serializers.CharField(source='get_channel_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_window_open = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Conversation
        fields = [
            'id', 'channel', 'channel_display', 'participant_id',
            'participant_name', 'participant_username',
            'contact', 'contact_name', 'lead',
            'assigned_to', 'assigned_to_name',
            'status', 'status_display',
            'unread_count', 'messages_count',
            'last_message_text', 'last_message_at',
            'is_window_open', 'window_expires_at',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class ConversationDetailSerializer(serializers.ModelSerializer):
    """Сериализатор для детальной переписки"""
    contact_name = serializers.CharField(source='contact.full_name', read_only=True)
    contact_phone = serializers.CharField(source='contact.phone', read_only=True)
    assigned_to_name = serializers.CharField(
        source='assigned_to.get_full_name', 
        read_only=True
    )
    channel_display = serializers.CharField(source='get_channel_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    is_window_open = serializers.BooleanField(read_only=True)
    
    # Информация об аккаунте
    whatsapp_account_phone = serializers.CharField(
        source='whatsapp_account.phone_number', 
        read_only=True
    )
    instagram_account_username = serializers.CharField(
        source='instagram_account.username', 
        read_only=True
    )
    
    class Meta:
        model = Conversation
        fields = [
            'id', 'company', 'channel', 'channel_display',
            'whatsapp_account', 'whatsapp_account_phone',
            'instagram_account', 'instagram_account_username',
            'participant_id', 'participant_name', 'participant_username',
            'contact', 'contact_name', 'contact_phone', 'lead',
            'assigned_to', 'assigned_to_name',
            'status', 'status_display',
            'unread_count', 'messages_count',
            'last_message_text', 'last_message_at',
            'is_window_open', 'window_expires_at',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class MessageSerializer(serializers.ModelSerializer):
    """Сериализатор для сообщений"""
    direction_display = serializers.CharField(source='get_direction_display', read_only=True)
    message_type_display = serializers.CharField(source='get_message_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    sender_user_name = serializers.CharField(source='sender_user.get_full_name', read_only=True)
    
    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'meta_message_id',
            'direction', 'direction_display',
            'sender_user', 'sender_user_name',
            'message_type', 'message_type_display',
            'text', 'media_id', 'media_url', 'media_mime_type',
            'media_filename', 'media_caption',
            'location_latitude', 'location_longitude',
            'location_name', 'location_address',
            'reply_to', 'context',
            'status', 'status_display', 'error_code', 'error_message',
            'is_read', 'read_at',
            'timestamp', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class SendMessageSerializer(serializers.Serializer):
    """Сериализатор для отправки сообщения"""
    conversation_id = serializers.UUIDField(required=True)
    message_type = serializers.ChoiceField(
        choices=['text', 'image', 'video', 'audio', 'document', 'template'],
        default='text'
    )
    text = serializers.CharField(required=False, allow_blank=True)
    media_url = serializers.URLField(required=False)
    media_caption = serializers.CharField(required=False, allow_blank=True)
    
    # Для шаблонов
    template_name = serializers.CharField(required=False)
    template_language = serializers.CharField(required=False, default='ru')
    template_components = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list
    )
    
    # Ответ на сообщение
    reply_to_message_id = serializers.CharField(required=False)
    
    def validate(self, data):
        message_type = data.get('message_type', 'text')
        
        if message_type == 'text' and not data.get('text'):
            raise serializers.ValidationError({'text': 'Текст обязателен для текстовых сообщений'})
        
        if message_type in ['image', 'video', 'audio', 'document'] and not data.get('media_url'):
            raise serializers.ValidationError({'media_url': f'URL медиа обязателен для типа {message_type}'})
        
        if message_type == 'template':
            if not data.get('template_name'):
                raise serializers.ValidationError({'template_name': 'Название шаблона обязательно'})
        
        return data


class MessageTemplateSerializer(serializers.ModelSerializer):
    """Сериализатор для шаблонов сообщений"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    whatsapp_account_phone = serializers.CharField(
        source='whatsapp_account.phone_number', 
        read_only=True
    )
    
    class Meta:
        model = MessageTemplate
        fields = [
            'id', 'whatsapp_account', 'whatsapp_account_phone',
            'template_id', 'name', 'language',
            'category', 'category_display',
            'status', 'status_display', 'rejection_reason',
            'components', 'example_values', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
