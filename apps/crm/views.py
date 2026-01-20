from rest_framework import generics, viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters import rest_framework as filters
from django.db.models import Q, Count, Sum, Avg
from django.utils import timezone

from .models import (
    SalesFunnel, FunnelStage, Contact, Lead, Deal,
    WazzuppAccount, WazzuppMessage, Activity
)
from .serializers import (
    SalesFunnelSerializer, SalesFunnelCreateSerializer,
    FunnelStageSerializer, ContactSerializer, ContactCreateSerializer,
    LeadSerializer, LeadCreateSerializer, DealSerializer, DealCreateSerializer,
    WazzuppAccountSerializer, WazzuppMessageSerializer, ActivitySerializer
)
from .services import WazzuppService
from apps.users.models import Company


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


# ==================== WAZZUPP ====================

class WazzuppAccountViewSet(CompanyQuerysetMixin, viewsets.ModelViewSet):
    """Wazzupp аккаунты"""
    queryset = WazzuppAccount.objects.all()
    serializer_class = WazzuppAccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['integration_type', 'is_active', 'is_connected']
    ordering = ['-created_at']
    
    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'company_id') and user.company_id:
            serializer.save(company_id=user.company_id)
        else:
            serializer.save()
    
    @action(detail=True, methods=['post'])
    def check_connection(self, request, pk=None):
        """Проверить подключение к Wazzupp"""
        account = self.get_object()
        service = WazzuppService(account)
        is_connected = service.check_connection()
        
        return Response({
            'is_connected': is_connected,
            'last_sync': account.last_sync
        })
    
    @action(detail=True, methods=['post'])
    def sync_messages(self, request, pk=None):
        """Синхронизировать сообщения из Wazzupp"""
        account = self.get_object()
        service = WazzuppService(account)
        synced_count = service.sync_messages()
        
        return Response({
            'synced_count': synced_count,
            'last_sync': account.last_sync
        })
    
    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        """Отправить сообщение через Wazzupp"""
        account = self.get_object()
        service = WazzuppService(account)
        
        to = request.data.get('to')
        message = request.data.get('message')
        message_type = request.data.get('message_type', 'text')
        media_url = request.data.get('media_url')
        caption = request.data.get('caption')
        
        if not to or not message:
            return Response(
                {'detail': 'to and message are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        result = service.send_message(
            to=to,
            message=message,
            message_type=message_type,
            media_url=media_url,
            caption=caption
        )
        
        if result:
            return Response({'status': 'sent', 'result': result})
        else:
            return Response(
                {'detail': 'Failed to send message'},
                status=status.HTTP_400_BAD_REQUEST
            )


class WazzuppMessageViewSet(CompanyQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    """Сообщения Wazzupp"""
    queryset = WazzuppMessage.objects.all()
    serializer_class = WazzuppMessageSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['account', 'contact', 'lead', 'is_incoming', 'is_read', 'message_type', 'status']
    search_fields = ['text', 'from_number', 'to_number']
    ordering_fields = ['timestamp', 'created_at']
    ordering = ['-timestamp']


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
