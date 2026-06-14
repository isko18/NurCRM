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

    # ===== Стадии воронки =====
    path('funnel-stages/', FunnelStageConsaltingListCreateView.as_view(), name='funnel-stages-list-create'),
    path('funnel-stages/<uuid:pk>/', FunnelStageConsaltingRetrieveUpdateDestroyView.as_view(), name='funnel-stages-rud'),

    # ===== Лиды (карточки) =====
    path('leads/', LeadConsaltingListCreateView.as_view(), name='leads-list-create'),
    path('leads/<uuid:pk>/', LeadConsaltingRetrieveUpdateDestroyView.as_view(), name='leads-rud'),
    path('leads/<uuid:pk>/move-stage/', LeadMoveStageView.as_view(), name='leads-move-stage'),
]
