from rest_framework import serializers
from .models import (
    SalesFunnel, FunnelStage, Contact, Lead, Deal,
    WazzuppAccount, WazzuppMessage, Activity
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


class WazzuppAccountSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True)
    integration_type_display = serializers.CharField(
        source='get_integration_type_display',
        read_only=True
    )
    
    class Meta:
        model = WazzuppAccount
        fields = [
            'id', 'company', 'company_name', 'api_key', 'api_url',
            'instance_id', 'integration_type', 'integration_type_display',
            'is_active', 'is_connected', 'last_sync', 'metadata',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_sync']
        extra_kwargs = {
            'api_key': {'write_only': True},
        }


class WazzuppMessageSerializer(serializers.ModelSerializer):
    account_info = serializers.CharField(source='account.instance_id', read_only=True)
    contact_name = serializers.CharField(source='contact.full_name', read_only=True)
    message_type_display = serializers.CharField(
        source='get_message_type_display',
        read_only=True
    )
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True
    )
    
    class Meta:
        model = WazzuppMessage
        fields = [
            'id', 'account', 'account_info', 'contact', 'contact_name',
            'lead', 'message_id', 'from_number', 'to_number',
            'message_type', 'message_type_display', 'text', 'media_url',
            'caption', 'is_incoming', 'is_read', 'status', 'status_display',
            'metadata', 'timestamp', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


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
