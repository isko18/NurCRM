from rest_framework import generics, viewsets, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters import rest_framework as filters
from django.db.models import Q, Count, Sum, Avg
from django.utils import timezone
from django.http import HttpResponse
import json
import logging

from .models import (
    SalesFunnel, FunnelStage, Contact, Lead, Deal, Activity,
    MetaBusinessAccount, WhatsAppBusinessAccount, InstagramBusinessAccount,
    Conversation, Message, MessageTemplate
)
from .serializers import (
    SalesFunnelSerializer, SalesFunnelCreateSerializer,
    FunnelStageSerializer, ContactSerializer, ContactCreateSerializer,
    LeadSerializer, LeadCreateSerializer, DealSerializer, DealCreateSerializer,
    ActivitySerializer,
    MetaBusinessAccountSerializer, MetaBusinessAccountCreateSerializer,
    WhatsAppBusinessAccountSerializer, InstagramBusinessAccountSerializer,
    ConversationListSerializer, ConversationDetailSerializer,
    MessageSerializer, SendMessageSerializer, MessageTemplateSerializer
)
from .services_meta import (
    MetaWebhookService, WhatsAppService, InstagramService,
    ConversationService, WebhookProcessor, MetaAPIError
)

logger = logging.getLogger(__name__)


class CompanyQuerysetMixin:
    """Миксин для фильтрации по компании пользователя"""
    
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        
        if user.is_authenticated and hasattr(user, 'company_id') and user.company_id:
            queryset = queryset.filter(company_id=user.company_id)
        elif not user.is_staff and not user.is_superuser:
            queryset = queryset.none()
        
        return queryset


# ==================== ФИЛЬТРЫ ====================

class ContactFilter(filters.FilterSet):
    phone = filters.CharFilter(lookup_expr='icontains')
    email = filters.CharFilter(lookup_expr='icontains')
    first_name = filters.CharFilter(lookup_expr='icontains')
    last_name = filters.CharFilter(lookup_expr='icontains')
    is_active = filters.BooleanFilter()
    is_client = filters.BooleanFilter()
    source = filters.CharFilter(lookup_expr='icontains')
    owner = filters.UUIDFilter()
    branch = filters.UUIDFilter()
    created_at = filters.DateTimeFromToRangeFilter()
    
    class Meta:
        model = Contact
        fields = ['phone', 'email', 'first_name', 'last_name', 'is_active', 'is_client', 'source', 'owner', 'branch']


class LeadFilter(filters.FilterSet):
    title = filters.CharFilter(lookup_expr='icontains')
    funnel = filters.UUIDFilter()
    stage = filters.UUIDFilter()
    owner = filters.UUIDFilter()
    source = filters.CharFilter(lookup_expr='icontains')
    created_at = filters.DateTimeFromToRangeFilter()
    
    class Meta:
        model = Lead
        fields = ['title', 'funnel', 'stage', 'owner', 'source']


class DealFilter(filters.FilterSet):
    title = filters.CharFilter(lookup_expr='icontains')
    funnel = filters.UUIDFilter()
    stage = filters.UUIDFilter()
    owner = filters.UUIDFilter()
    is_won = filters.BooleanFilter()
    is_lost = filters.BooleanFilter()
    expected_close_date = filters.DateFromToRangeFilter()
    created_at = filters.DateTimeFromToRangeFilter()
    
    class Meta:
        model = Deal
        fields = ['title', 'funnel', 'stage', 'owner', 'is_won', 'is_lost', 'expected_close_date']


class ConversationFilter(filters.FilterSet):
    channel = filters.ChoiceFilter(choices=Conversation.CHANNEL_CHOICES)
    status = filters.ChoiceFilter(choices=Conversation.STATUS_CHOICES)
    assigned_to = filters.UUIDFilter()
    contact = filters.UUIDFilter()
    has_unread = filters.BooleanFilter(method='filter_has_unread')
    
    def filter_has_unread(self, queryset, name, value):
        if value:
            return queryset.filter(unread_count__gt=0)
        return queryset.filter(unread_count=0)
    
    class Meta:
        model = Conversation
        fields = ['channel', 'status', 'assigned_to', 'contact']


# ==================== ВОРОНКИ ПРОДАЖ ====================

class SalesFunnelViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Воронки продаж"""
    queryset = SalesFunnel.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'name']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'create':
            return SalesFunnelCreateSerializer
        return SalesFunnelSerializer
    
    @action(detail=True, methods=['get'])
    def statistics(self, request, pk=None):
        """Статистика по воронке"""
        funnel = self.get_object()
        
        leads_count = funnel.leads.count()
        deals_count = funnel.deals.count()
        won_deals = funnel.deals.filter(is_won=True).count()
        lost_deals = funnel.deals.filter(is_lost=True).count()
        
        total_amount = funnel.deals.filter(is_won=True).aggregate(
            total=Sum('amount')
        )['total'] or 0
        
        # Статистика по стадиям
        stages_stats = []
        for stage in funnel.stages.all():
            leads_in_stage = funnel.leads.filter(stage=stage).count()
            deals_in_stage = funnel.deals.filter(stage=stage).count()
            stages_stats.append({
                'stage_id': str(stage.id),
                'stage_name': stage.name,
                'leads_count': leads_in_stage,
                'deals_count': deals_in_stage,
            })
        
        return Response({
            'funnel_id': str(funnel.id),
            'funnel_name': funnel.name,
            'leads_count': leads_count,
            'deals_count': deals_count,
            'won_deals': won_deals,
            'lost_deals': lost_deals,
            'total_amount': float(total_amount),
            'stages_statistics': stages_stats,
        })


class FunnelStageViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Стадии воронки"""
    queryset = FunnelStage.objects.all()
    serializer_class = FunnelStageSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['funnel']
    ordering_fields = ['order']
    ordering = ['order']


# ==================== КОНТАКТЫ ====================

class ContactViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Контакты"""
    queryset = Contact.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ContactFilter
    search_fields = ['first_name', 'last_name', 'phone', 'email', 'company_name']
    ordering_fields = ['created_at', 'first_name']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ContactCreateSerializer
        return ContactSerializer
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'company_id') and user.company_id:
            serializer.save(company_id=user.company_id, owner=user)
        else:
            serializer.save()


# ==================== ЛИДЫ ====================

class LeadViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Лиды"""
    queryset = Lead.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = LeadFilter
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'estimated_value']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'create':
            return LeadCreateSerializer
        return LeadSerializer
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'company_id') and user.company_id:
            serializer.save(company_id=user.company_id, owner=user)
        else:
            serializer.save()
    
    @action(detail=True, methods=['post'])
    def move_to_stage(self, request, pk=None):
        """Переместить лид в другую стадию"""
        lead = self.get_object()
        stage_id = request.data.get('stage_id')
        
        if not stage_id:
            return Response({'detail': 'stage_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            stage = FunnelStage.objects.get(id=stage_id, funnel=lead.funnel)
            lead.stage = stage
            lead.save()
            
            # Создаем активность
            Activity.objects.create(
                company=lead.company,
                user=request.user,
                lead=lead,
                activity_type='stage_change',
                title=f'Лид перемещен в стадию "{stage.name}"',
                activity_date=timezone.now()
            )
            
            return Response(LeadSerializer(lead).data)
        except FunnelStage.DoesNotExist:
            return Response({'detail': 'Stage not found'}, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=True, methods=['post'])
    def convert_to_deal(self, request, pk=None):
        """Конвертировать лид в сделку"""
        lead = self.get_object()
        
        # Проверяем, нет ли уже сделки для этого лида
        if hasattr(lead, 'deal'):
            return Response({'detail': 'Deal already exists for this lead'}, status=status.HTTP_400_BAD_REQUEST)
        
        deal = Deal.objects.create(
            company=lead.company,
            lead=lead,
            contact=lead.contact,
            funnel=lead.funnel,
            stage=lead.stage,
            owner=lead.owner,
            title=lead.title,
            description=lead.description,
            amount=lead.estimated_value,
            probability=lead.probability,
        )
        
        return Response(DealSerializer(deal).data, status=status.HTTP_201_CREATED)


# ==================== СДЕЛКИ ====================

class DealViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Сделки"""
    queryset = Deal.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = DealFilter
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'amount', 'expected_close_date']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'create':
            return DealCreateSerializer
        return DealSerializer
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'company_id') and user.company_id:
            serializer.save(company_id=user.company_id, owner=user)
        else:
            serializer.save()
    
    @action(detail=True, methods=['post'])
    def move_to_stage(self, request, pk=None):
        """Переместить сделку в другую стадию"""
        deal = self.get_object()
        stage_id = request.data.get('stage_id')
        
        if not stage_id:
            return Response({'detail': 'stage_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            stage = FunnelStage.objects.get(id=stage_id, funnel=deal.funnel)
            deal.stage = stage
            
            # Если стадия финальная и успешная - закрываем сделку как выигранную
            if stage.is_final and stage.is_success:
                deal.is_won = True
                deal.closed_at = timezone.now()
            # Если стадия финальная но не успешная - закрываем как проигранную
            elif stage.is_final and not stage.is_success:
                deal.is_lost = True
                deal.closed_at = timezone.now()
            
            deal.save()
            
            # Создаем активность
            Activity.objects.create(
                company=deal.company,
                user=request.user,
                deal=deal,
                activity_type='stage_change',
                title=f'Сделка перемещена в стадию "{stage.name}"',
                activity_date=timezone.now()
            )
            
            return Response(DealSerializer(deal).data)
        except FunnelStage.DoesNotExist:
            return Response({'detail': 'Stage not found'}, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=True, methods=['post'])
    def mark_won(self, request, pk=None):
        """Пометить сделку как выигранную"""
        deal = self.get_object()
        deal.is_won = True
        deal.is_lost = False
        deal.closed_at = timezone.now()
        deal.save()
        
        Activity.objects.create(
            company=deal.company,
            user=request.user,
            deal=deal,
            activity_type='note',
            title='Сделка выиграна',
            activity_date=timezone.now()
        )
        
        return Response(DealSerializer(deal).data)
    
    @action(detail=True, methods=['post'])
    def mark_lost(self, request, pk=None):
        """Пометить сделку как проигранную"""
        deal = self.get_object()
        deal.is_lost = True
        deal.is_won = False
        deal.lost_reason = request.data.get('reason', '')
        deal.closed_at = timezone.now()
        deal.save()
        
        Activity.objects.create(
            company=deal.company,
            user=request.user,
            deal=deal,
            activity_type='note',
            title='Сделка проиграна',
            description=deal.lost_reason,
            activity_date=timezone.now()
        )
        
        return Response(DealSerializer(deal).data)


# ==================== АКТИВНОСТИ ====================

class ActivityViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Активности"""
    queryset = Activity.objects.all()
    serializer_class = ActivitySerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['contact', 'lead', 'deal', 'activity_type', 'user']
    ordering_fields = ['activity_date', 'created_at']
    ordering = ['-activity_date']
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'company_id') and user.company_id:
            serializer.save(company_id=user.company_id, user=user)
        else:
            serializer.save(user=user)


# ==================== META BUSINESS INTEGRATION ====================

class MetaBusinessAccountViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Meta Business аккаунты"""
    queryset = MetaBusinessAccount.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['is_active', 'is_verified']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'create':
            return MetaBusinessAccountCreateSerializer
        return MetaBusinessAccountSerializer
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'company_id') and user.company_id:
            serializer.save(company_id=user.company_id)
        else:
            serializer.save()
    
    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        """Проверить подключение к Meta API"""
        account = self.get_object()
        # Простая проверка токена
        import requests
        try:
            response = requests.get(
                f"https://graph.facebook.com/v18.0/{account.business_id}",
                params={'access_token': account.access_token},
                timeout=10
            )
            if response.status_code == 200:
                account.is_verified = True
                account.save()
                return Response({'status': 'verified', 'data': response.json()})
            else:
                return Response(
                    {'status': 'failed', 'error': response.json()},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class WhatsAppBusinessAccountViewSet(viewsets.ModelViewSet):
    """WhatsApp Business аккаунты"""
    queryset = WhatsAppBusinessAccount.objects.all()
    serializer_class = WhatsAppBusinessAccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['meta_account', 'is_active', 'is_verified', 'quality_rating']
    ordering = ['-created_at']
    
    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and hasattr(user, 'company_id') and user.company_id:
            return self.queryset.filter(meta_account__company_id=user.company_id)
        elif user.is_staff or user.is_superuser:
            return self.queryset
        return self.queryset.none()
    
    @action(detail=True, methods=['post'])
    def sync_templates(self, request, pk=None):
        """Синхронизировать шаблоны сообщений"""
        account = self.get_object()
        try:
            service = WhatsAppService(account)
            count = service.sync_templates()
            return Response({'synced_count': count})
        except MetaAPIError as e:
            return Response(
                {'error': e.message, 'code': e.code},
                status=status.HTTP_400_BAD_REQUEST
            )


class InstagramBusinessAccountViewSet(viewsets.ModelViewSet):
    """Instagram Business аккаунты"""
    queryset = InstagramBusinessAccount.objects.all()
    serializer_class = InstagramBusinessAccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['meta_account', 'is_active']
    ordering = ['-created_at']
    
    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and hasattr(user, 'company_id') and user.company_id:
            return self.queryset.filter(meta_account__company_id=user.company_id)
        elif user.is_staff or user.is_superuser:
            return self.queryset
        return self.queryset.none()


# ==================== ПЕРЕПИСКИ ====================

class ConversationViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Переписки"""
    queryset = Conversation.objects.select_related(
        'contact', 'lead', 'assigned_to',
        'whatsapp_account', 'instagram_account'
    )
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ConversationFilter
    search_fields = ['participant_name', 'participant_id', 'participant_username']
    ordering_fields = ['last_message_at', 'created_at', 'unread_count']
    ordering = ['-last_message_at']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ConversationDetailSerializer
        return ConversationListSerializer
    
    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        """Получить сообщения переписки"""
        conversation = self.get_object()
        messages = conversation.messages.select_related('sender_user', 'reply_to')
        
        # Пагинация
        page = self.paginate_queryset(messages)
        if page is not None:
            serializer = MessageSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = MessageSerializer(messages, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        """Отправить сообщение в переписку"""
        conversation = self.get_object()
        serializer = SendMessageSerializer(data={
            'conversation_id': str(conversation.id),
            **request.data
        })
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        
        try:
            if conversation.channel == 'whatsapp':
                result = self._send_whatsapp_message(conversation, data, request.user)
            else:
                result = self._send_instagram_message(conversation, data, request.user)
            
            return Response(MessageSerializer(result).data, status=status.HTTP_201_CREATED)
        except MetaAPIError as e:
            return Response(
                {'error': e.message, 'code': e.code},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    def _send_whatsapp_message(self, conversation, data, user):
        """Отправка сообщения через WhatsApp"""
        service = WhatsAppService(conversation.whatsapp_account)
        msg_type = data['message_type']
        to = conversation.participant_id
        
        if msg_type == 'text':
            result = service.send_text_message(
                to=to,
                text=data['text'],
                reply_to_message_id=data.get('reply_to_message_id')
            )
        elif msg_type in ('image', 'video', 'audio', 'document'):
            result = service.send_media_message(
                to=to,
                media_type=msg_type,
                media_url=data['media_url'],
                caption=data.get('media_caption')
            )
        elif msg_type == 'template':
            result = service.send_template_message(
                to=to,
                template_name=data['template_name'],
                language_code=data.get('template_language', 'ru'),
                components=data.get('template_components', [])
            )
        else:
            raise MetaAPIError(f"Unsupported message type: {msg_type}")
        
        # Сохраняем сообщение в БД
        message_id = result.get('messages', [{}])[0].get('id', '')
        
        message = Message.objects.create(
            conversation=conversation,
            meta_message_id=message_id,
            direction='outbound',
            sender_user=user,
            message_type=msg_type,
            text=data.get('text', ''),
            media_url=data.get('media_url', ''),
            media_caption=data.get('media_caption', ''),
            status='sent',
            timestamp=timezone.now()
        )
        
        ConversationService.update_conversation_on_message(
            conversation, message, is_inbound=False
        )
        
        return message
    
    def _send_instagram_message(self, conversation, data, user):
        """Отправка сообщения через Instagram"""
        service = InstagramService(conversation.instagram_account)
        msg_type = data['message_type']
        to = conversation.participant_id
        
        if msg_type == 'text':
            result = service.send_text_message(to, data['text'])
        elif msg_type == 'image':
            result = service.send_image_message(to, data['media_url'])
        else:
            raise MetaAPIError(f"Instagram doesn't support message type: {msg_type}")
        
        message_id = result.get('message_id', '')
        
        message = Message.objects.create(
            conversation=conversation,
            meta_message_id=message_id,
            direction='outbound',
            sender_user=user,
            message_type=msg_type,
            text=data.get('text', ''),
            media_url=data.get('media_url', ''),
            status='sent',
            timestamp=timezone.now()
        )
        
        ConversationService.update_conversation_on_message(
            conversation, message, is_inbound=False
        )
        
        return message
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Отметить все сообщения переписки как прочитанные"""
        conversation = self.get_object()
        conversation.messages.filter(
            direction='inbound',
            is_read=False
        ).update(is_read=True, read_at=timezone.now())
        conversation.unread_count = 0
        conversation.save(update_fields=['unread_count'])
        
        return Response({'status': 'ok'})
    
    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Назначить ответственного менеджера"""
        conversation = self.get_object()
        user_id = request.data.get('user_id')
        
        if user_id:
            from apps.users.models import User
            try:
                user = User.objects.get(id=user_id, company=conversation.company)
                conversation.assigned_to = user
            except User.DoesNotExist:
                return Response(
                    {'error': 'User not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            conversation.assigned_to = None
        
        conversation.save(update_fields=['assigned_to'])
        return Response(ConversationDetailSerializer(conversation).data)
    
    @action(detail=True, methods=['post'])
    def link_contact(self, request, pk=None):
        """Связать переписку с контактом CRM"""
        conversation = self.get_object()
        contact_id = request.data.get('contact_id')
        
        if contact_id:
            try:
                contact = Contact.objects.get(id=contact_id, company=conversation.company)
                conversation.contact = contact
            except Contact.DoesNotExist:
                return Response(
                    {'error': 'Contact not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            conversation.contact = None
        
        conversation.save(update_fields=['contact'])
        return Response(ConversationDetailSerializer(conversation).data)


class MessageViewSet(viewsets.ReadOnlyModelViewSet):
    """Сообщения (только чтение)"""
    queryset = Message.objects.select_related('sender_user', 'reply_to')
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['conversation', 'direction', 'message_type', 'status', 'is_read']
    ordering_fields = ['timestamp', 'created_at']
    ordering = ['timestamp']
    
    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and hasattr(user, 'company_id') and user.company_id:
            return self.queryset.filter(conversation__company_id=user.company_id)
        elif user.is_staff or user.is_superuser:
            return self.queryset
        return self.queryset.none()


class MessageTemplateViewSet(viewsets.ModelViewSet):
    """Шаблоны сообщений WhatsApp"""
    queryset = MessageTemplate.objects.select_related('whatsapp_account')
    serializer_class = MessageTemplateSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['whatsapp_account', 'category', 'status', 'is_active']
    search_fields = ['name']
    ordering = ['name']
    
    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and hasattr(user, 'company_id') and user.company_id:
            return self.queryset.filter(
                whatsapp_account__meta_account__company_id=user.company_id
            )
        elif user.is_staff or user.is_superuser:
            return self.queryset
        return self.queryset.none()


# ==================== META WEBHOOK ====================

class MetaWebhookView(APIView):
    """
    Webhook endpoint для Meta (WhatsApp Cloud API + Instagram Messaging API).
    
    URL: /api/crm/webhook/meta/<business_id>/
    
    Meta будет отправлять сюда:
    - Входящие сообщения
    - Статусы доставки
    - Другие события
    """
    permission_classes = [permissions.AllowAny]  # Webhook должен быть публичным
    
    def get(self, request, business_id):
        """
        Верификация webhook при подписке.
        Meta отправляет GET-запрос с параметрами:
        - hub.mode
        - hub.verify_token
        - hub.challenge
        """
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        
        try:
            meta_account = MetaBusinessAccount.objects.get(business_id=business_id)
        except MetaBusinessAccount.DoesNotExist:
            logger.warning(f"Webhook verification failed: account {business_id} not found")
            return HttpResponse('Account not found', status=404)
        
        result = MetaWebhookService.verify_webhook(
            mode, token, challenge, meta_account.webhook_verify_token
        )
        
        if result:
            logger.info(f"Webhook verified for business_id: {business_id}")
            return HttpResponse(result, content_type='text/plain')
        
        logger.warning(f"Webhook verification failed for business_id: {business_id}")
        return HttpResponse('Verification failed', status=403)
    
    def post(self, request, business_id):
        """
        Обработка входящих событий от Meta.
        """
        try:
            meta_account = MetaBusinessAccount.objects.get(business_id=business_id)
        except MetaBusinessAccount.DoesNotExist:
            logger.warning(f"Webhook received for unknown account: {business_id}")
            return Response({'error': 'Account not found'}, status=404)
        
        # Проверяем подпись (если настроен app_secret)
        if meta_account.webhook_secret:
            signature = request.headers.get('X-Hub-Signature-256', '')
            if not MetaWebhookService.verify_signature(
                request.body, signature, meta_account.webhook_secret
            ):
                logger.warning(f"Invalid webhook signature for business_id: {business_id}")
                return Response({'error': 'Invalid signature'}, status=403)
        
        payload = request.data
        object_type = payload.get('object')
        
        logger.info(f"Webhook received: {object_type} for {business_id}")
        
        try:
            if object_type == 'whatsapp_business_account':
                WebhookProcessor.process_whatsapp_webhook(payload, meta_account)
            elif object_type == 'instagram':
                WebhookProcessor.process_instagram_webhook(payload, meta_account)
            else:
                logger.warning(f"Unknown webhook object type: {object_type}")
        except Exception as e:
            logger.exception(f"Error processing webhook: {e}")
            # Всё равно возвращаем 200, чтобы Meta не ретраила
        
        return Response({'status': 'ok'})
