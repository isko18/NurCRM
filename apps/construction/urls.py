from django.urls import path
from apps.construction.views import (
    DepartmentListCreateView,
    DepartmentDetailView,
    DepartmentAnalyticsListView,
    DepartmentAnalyticsDetailView,
    CashboxListView,
    CashboxDetailView,
    CashFlowListCreateView,
    CashFlowDetailView
)

urlpatterns = [
    # DEPARTMENTS
    path('departments/', DepartmentListCreateView.as_view(), name='department-list-create'),
    path('departments/<uuid:pk>/', DepartmentDetailView.as_view(), name='department-detail'),

    # DEPARTMENT ANALYTICS
    path('analytics/departments/', DepartmentAnalyticsListView.as_view(), name='department-analytics-list'),
    path('analytics/departments/<uuid:pk>/', DepartmentAnalyticsDetailView.as_view(), name='department-analytics-detail'),

    # CASHBOXES
    path('cashboxes/', CashboxListView.as_view(), name='cashbox-list'),
    path('cashboxes/<uuid:pk>/', CashboxDetailView.as_view(), name='cashbox-detail'),

    # CASHFLOWS
    path('cashflows/', CashFlowListCreateView.as_view(), name='cashflow-list-create'),
    path('cashflows/<uuid:pk>/', CashFlowDetailView.as_view(), name='cashflow-detail'),
]
