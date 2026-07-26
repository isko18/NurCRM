from django.urls import path
from .views import (
    ServicesConsaltingListCreateView,
    ServicesConsaltingRetrieveUpdateDestroyView,
    SaleConsaltingListCreateView,
    SaleConsaltingRetrieveUpdateDestroyView,
    SaleConsaltingAnalyticsView,
    SalaryConsaltingListCreateView,
    SalaryConsaltingRetrieveUpdateDestroyView,
    RequestsConsaltingListCreateView,
    RequestsConsaltingRetrieveUpdateDestroyView,
    BookingConsaltingListCreateView,
    BookingConsaltingRetrieveUpdateDestroyView,
    ClientConsaltingListCreateView,
    ClientConsaltingRetrieveUpdateDestroyView,
    FunnelConsaltingListCreateView,
    FunnelConsaltingRetrieveUpdateDestroyView,
    FunnelForRoleView,
    FunnelBoardView,
    FunnelBoardsView,
    FunnelEmployeesView,
    FunnelStageConsaltingListCreateView,
    FunnelStageConsaltingRetrieveUpdateDestroyView,
    FunnelStageReorderView,
    FunnelUserPreferenceView,
    LeadConsaltingListCreateView,
    LeadConsaltingRetrieveUpdateDestroyView,
    LeadMoveStageView,
    LeadClaimView,
    LeadReleaseView,
    LeadAssignView,
    LeadTransferView,
    LeadParticipantsView,
    LeadArchiveView,
    LeadArchivedListView,
    LeadCreateClientView,
    LeadRegisterPaymentView,
    LeadAllowedTransitionsView,
    LeadTimelineView,
    LeadActivityCreateView,
    LeadRecalculateScoreView,
    LeadWinView,
    LeadLoseView,
    LeadTaskListCreateView,
    LeadTaskRetrieveUpdateDestroyView,
    LossReasonListCreateView,
    LossReasonRetrieveUpdateDestroyView,
    FunnelAnalyticsView,
    LeadWhatsAppSendView,
    LeadWhatsAppHistoryView,
    WhatsAppConsaltingWebhookView,
    ServiceSalaryRateListView,
    ServiceSalaryRateUpdateView,
    SalaryAccrualListView,
    SalarySummaryView,
    SalaryPayoutListCreateView,
    InboundLeadListCreateView,
    InboundLeadRetrieveUpdateView,
    InboundLeadAssignView,
    LeadDistributionSettingsView,
    WhatsAppInboundWebhookView,
    SubscriptionMatrixView,
)
from .wazzup_views import WazzupAccountConsaltingViewSet, WazzupWebhookConsaltingView


urlpatterns = [
    # ===== Клиенты & Абонентская матрица =====
    path('subscription-matrix/', SubscriptionMatrixView.as_view(), name='subscription-matrix'),
    path('clients/subscription-matrix/', SubscriptionMatrixView.as_view(), name='clients-subscription-matrix'),
    # ===== Входящие лиды и авто-распределение =====
    path('inbound-leads/', InboundLeadListCreateView.as_view(), name='inbound-leads-list-create'),
    path('inbound-leads/<uuid:pk>/', InboundLeadRetrieveUpdateView.as_view(), name='inbound-leads-detail'),
    path('inbound-leads/<uuid:pk>/assign/', InboundLeadAssignView.as_view(), name='inbound-leads-assign'),
    path('lead-distribution/', LeadDistributionSettingsView.as_view(), name='lead-distribution'),
    path('integrations/whatsapp/webhook/', WhatsAppInboundWebhookView.as_view(), name='whatsapp-inbound-webhook'),

    # ===== Зарплатная система =====
    path('salary/rates/', ServiceSalaryRateListView.as_view(), name='salary-rates-list'),
    path('salary/rates/<uuid:service_id>/', ServiceSalaryRateUpdateView.as_view(), name='salary-rates-update'),
    path('salary/accruals/', SalaryAccrualListView.as_view(), name='salary-accruals-list'),
    path('salary/summary/', SalarySummaryView.as_view(), name='salary-summary'),
    path('salary/payouts/', SalaryPayoutListCreateView.as_view(), name='salary-payouts-list-create'),

    path('services/', ServicesConsaltingListCreateView.as_view(), name='services-list-create'),
    path('services/<uuid:pk>/', ServicesConsaltingRetrieveUpdateDestroyView.as_view(), name='services-rud'),

    path('analytics/', SaleConsaltingAnalyticsView.as_view(), name='consulting-analytics'),
    path('sales/', SaleConsaltingListCreateView.as_view(), name='sales-list-create'),
    path('sales/analytics/', SaleConsaltingAnalyticsView.as_view(), name='sales-analytics'),
    path('sales/<uuid:pk>/', SaleConsaltingRetrieveUpdateDestroyView.as_view(), name='sales-rud'),

    path('salaries/', SalaryConsaltingListCreateView.as_view(), name='salaries-list-create'),
    path('salaries/<uuid:pk>/', SalaryConsaltingRetrieveUpdateDestroyView.as_view(), name='salaries-rud'),

    path('requests/', RequestsConsaltingListCreateView.as_view(), name='requests-list-create'),
    path('requests/<uuid:pk>/', RequestsConsaltingRetrieveUpdateDestroyView.as_view(), name='requests-rud'),

    path('bookings/', BookingConsaltingListCreateView.as_view(), name='bookings-list-create'),
    path('bookings/<uuid:pk>/', BookingConsaltingRetrieveUpdateDestroyView.as_view(), name='bookings-detail'),

    # ===== Клиенты =====
    path('clients/', ClientConsaltingListCreateView.as_view(), name='clients-list-create'),
    path('clients/<uuid:pk>/', ClientConsaltingRetrieveUpdateDestroyView.as_view(), name='clients-rud'),

    # ===== Воронка продаж =====
    path('funnels/', FunnelConsaltingListCreateView.as_view(), name='funnels-list-create'),
    path('funnels/for-role/', FunnelForRoleView.as_view(), name='funnels-for-role'),
    path('funnels/boards/', FunnelBoardsView.as_view(), name='funnels-boards'),
    path('funnels/<uuid:pk>/', FunnelConsaltingRetrieveUpdateDestroyView.as_view(), name='funnels-rud'),
    path('funnels/<uuid:pk>/board/', FunnelBoardView.as_view(), name='funnels-board'),
    path('funnels/<uuid:pk>/employees/', FunnelEmployeesView.as_view(), name='funnels-employees'),
    path('funnels/<uuid:pk>/analytics/', FunnelAnalyticsView.as_view(), name='funnels-analytics'),

    # ===== Стадии воронки =====
    path('funnel-stages/', FunnelStageConsaltingListCreateView.as_view(), name='funnel-stages-list-create'),
    path('funnel-stages/reorder/', FunnelStageReorderView.as_view(), name='funnel-stages-reorder'),
    path('funnel-stages/<uuid:pk>/', FunnelStageConsaltingRetrieveUpdateDestroyView.as_view(), name='funnel-stages-rud'),

    # ===== Пользовательские предпочтения =====
    path('user-preferences/', FunnelUserPreferenceView.as_view(), name='user-preferences'),

    # ===== Лиды (карточки) =====
    path('leads/', LeadConsaltingListCreateView.as_view(), name='leads-list-create'),
    path('leads/archived/', LeadArchivedListView.as_view(), name='leads-archived'),
    path('leads/<uuid:pk>/', LeadConsaltingRetrieveUpdateDestroyView.as_view(), name='leads-rud'),
    path('leads/<uuid:pk>/move-stage/', LeadMoveStageView.as_view(), name='leads-move-stage'),
    path('leads/<uuid:pk>/claim/', LeadClaimView.as_view(), name='leads-claim'),
    path('leads/<uuid:pk>/release/', LeadReleaseView.as_view(), name='leads-release'),
    path('leads/<uuid:pk>/assign/', LeadAssignView.as_view(), name='leads-assign'),
    path('leads/<uuid:pk>/transfer/', LeadTransferView.as_view(), name='leads-transfer'),
    path('leads/<uuid:pk>/participants/', LeadParticipantsView.as_view(), name='leads-participants'),
    path('leads/<uuid:pk>/archive/', LeadArchiveView.as_view(), name='leads-archive'),
    path('leads/<uuid:pk>/create-client/', LeadCreateClientView.as_view(), name='leads-create-client'),
    path('leads/<uuid:pk>/register-payment/', LeadRegisterPaymentView.as_view(), name='leads-register-payment'),
    path('leads/<uuid:pk>/allowed-transitions/', LeadAllowedTransitionsView.as_view(), name='leads-allowed-transitions'),
    path('leads/<uuid:pk>/timeline/', LeadTimelineView.as_view(), name='leads-timeline'),
    path('leads/<uuid:pk>/activities/', LeadActivityCreateView.as_view(), name='leads-activity-create'),
    path('leads/<uuid:pk>/recalculate-score/', LeadRecalculateScoreView.as_view(), name='leads-recalculate-score'),
    path('leads/<uuid:pk>/win/', LeadWinView.as_view(), name='leads-win'),
    path('leads/<uuid:pk>/lose/', LeadLoseView.as_view(), name='leads-lose'),

    # ===== Задачи по лидам =====
    path('lead-tasks/', LeadTaskListCreateView.as_view(), name='lead-tasks-list-create'),
    path('lead-tasks/<uuid:pk>/', LeadTaskRetrieveUpdateDestroyView.as_view(), name='lead-tasks-rud'),

    # ===== Причины проигрыша =====
    path('loss-reasons/', LossReasonListCreateView.as_view(), name='loss-reasons-list-create'),
    path('loss-reasons/<uuid:pk>/', LossReasonRetrieveUpdateDestroyView.as_view(), name='loss-reasons-rud'),

    # ===== WhatsApp Интеграция (каркас) =====
    path('leads/<uuid:pk>/whatsapp/send/', LeadWhatsAppSendView.as_view(), name='lead-whatsapp-send'),
    path('leads/<uuid:pk>/whatsapp/history/', LeadWhatsAppHistoryView.as_view(), name='lead-whatsapp-history'),
    path('whatsapp/webhook/', WhatsAppConsaltingWebhookView.as_view(), name='whatsapp-consalting-webhook'),

    # ===== Wazzup Интеграция (Консалтинг) =====
    path('wazzup/webhook/', WazzupWebhookConsaltingView.as_view(), name='wazzup-consalting-webhook'),
    path('wazzup-accounts/', WazzupAccountConsaltingViewSet.as_view({'get': 'list', 'post': 'create'}), name='wazzup-consalting-accounts'),
    path('wazzup-accounts/<uuid:pk>/', WazzupAccountConsaltingViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}), name='wazzup-consalting-account-detail'),
    path('wazzup-accounts/<uuid:pk>/setup-webhook/', WazzupAccountConsaltingViewSet.as_view({'post': 'setup_webhook'}), name='wazzup-consalting-account-setup-webhook'),
    path('wazzup-accounts/<uuid:pk>/send-message/', WazzupAccountConsaltingViewSet.as_view({'post': 'send_message'}), name='wazzup-consalting-account-send-message'),
]

