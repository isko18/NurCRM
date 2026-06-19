from django.urls import path
from .views import (
    ServicesConsaltingListCreateView,
    ServicesConsaltingRetrieveUpdateDestroyView,
    SaleConsaltingListCreateView,
    SaleConsaltingRetrieveUpdateDestroyView,
    SalaryConsaltingListCreateView,
    SalaryConsaltingRetrieveUpdateDestroyView,
    RequestsConsaltingListCreateView,
    RequestsConsaltingRetrieveUpdateDestroyView,
    BookingConsaltingListCreateView,
    BookingConsaltingRetrieveUpdateDestroyView,
    FunnelConsaltingListCreateView,
    FunnelConsaltingRetrieveUpdateDestroyView,
    FunnelBoardView,
    FunnelStageConsaltingListCreateView,
    FunnelStageConsaltingRetrieveUpdateDestroyView,
    LeadConsaltingListCreateView,
    LeadConsaltingRetrieveUpdateDestroyView,
    LeadMoveStageView,
    LeadClaimView,
    LeadReleaseView,
    LeadAssignView,
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
)

urlpatterns = [
    path('services/', ServicesConsaltingListCreateView.as_view(), name='services-list-create'),
    path('services/<uuid:pk>/', ServicesConsaltingRetrieveUpdateDestroyView.as_view(), name='services-rud'),

    path('sales/', SaleConsaltingListCreateView.as_view(), name='sales-list-create'),
    path('sales/<uuid:pk>/', SaleConsaltingRetrieveUpdateDestroyView.as_view(), name='sales-rud'),

    path('salaries/', SalaryConsaltingListCreateView.as_view(), name='salaries-list-create'),
    path('salaries/<uuid:pk>/', SalaryConsaltingRetrieveUpdateDestroyView.as_view(), name='salaries-rud'),

    path('requests/', RequestsConsaltingListCreateView.as_view(), name='requests-list-create'),
    path('requests/<uuid:pk>/', RequestsConsaltingRetrieveUpdateDestroyView.as_view(), name='requests-rud'),

    path('bookings/', BookingConsaltingListCreateView.as_view(), name='bookings-list-create'),
    path('bookings/<uuid:pk>/', BookingConsaltingRetrieveUpdateDestroyView.as_view(), name='bookings-detail'),

    # ===== Воронка продаж =====
    path('funnels/', FunnelConsaltingListCreateView.as_view(), name='funnels-list-create'),
    path('funnels/<uuid:pk>/', FunnelConsaltingRetrieveUpdateDestroyView.as_view(), name='funnels-rud'),
    path('funnels/<uuid:pk>/board/', FunnelBoardView.as_view(), name='funnels-board'),
    path('funnels/<uuid:pk>/analytics/', FunnelAnalyticsView.as_view(), name='funnels-analytics'),

    # ===== Стадии воронки =====
    path('funnel-stages/', FunnelStageConsaltingListCreateView.as_view(), name='funnel-stages-list-create'),
    path('funnel-stages/<uuid:pk>/', FunnelStageConsaltingRetrieveUpdateDestroyView.as_view(), name='funnel-stages-rud'),

    # ===== Лиды (карточки) =====
    path('leads/', LeadConsaltingListCreateView.as_view(), name='leads-list-create'),
    path('leads/<uuid:pk>/', LeadConsaltingRetrieveUpdateDestroyView.as_view(), name='leads-rud'),
    path('leads/<uuid:pk>/move-stage/', LeadMoveStageView.as_view(), name='leads-move-stage'),
    path('leads/<uuid:pk>/claim/', LeadClaimView.as_view(), name='leads-claim'),
    path('leads/<uuid:pk>/release/', LeadReleaseView.as_view(), name='leads-release'),
    path('leads/<uuid:pk>/assign/', LeadAssignView.as_view(), name='leads-assign'),
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
]
