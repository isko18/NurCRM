from django.urls import path
from apps.construction.views import (
    DepartmentListCreateView,
    DepartmentDetailView,
    DepartmentAnalyticsListView,
    DepartmentAnalyticsDetailView,
    CashboxListCreateView,
    CashboxDetailView,
    CashFlowListCreateView,
    CashFlowDetailView,
    AssignEmployeeToDepartmentView,
    RemoveEmployeeFromDepartmentView,
    CompanyDepartmentAnalyticsView,
    CashboxOwnerDetailView, 
    CashboxOwnerDetailSingleView
)

urlpatterns = [
    # DEPARTMENTS
    path('departments/', DepartmentListCreateView.as_view(), name='department-list-create'),
    path('departments/<uuid:pk>/', DepartmentDetailView.as_view(), name='department-detail'),
    path('departments/<uuid:department_id>/assign-employee/', AssignEmployeeToDepartmentView.as_view(), name='assign-employee-to-department'),
    path('departments/<uuid:department_id>/remove-employee/', RemoveEmployeeFromDepartmentView.as_view(), name='remove-employee-from-department'),

    # DEPARTMENT ANALYTICS
    path('analytics/departments/', DepartmentAnalyticsListView.as_view(), name='department-analytics-list'),
    path('analytics/departments/<uuid:pk>/', DepartmentAnalyticsDetailView.as_view(), name='department-analytics-detail'),
    path('company/departments/analytics/', CompanyDepartmentAnalyticsView.as_view(), name='company-department-analytics'),

    path('cashboxes/', CashboxListCreateView.as_view(), name='cashbox-list-create'),

    path('cashboxes/<uuid:pk>/', CashboxDetailView.as_view(), name='cashbox-detail'),

    # CASHFLOWS
    path('cashflows/', CashFlowListCreateView.as_view(), name='cashflow-list-create'),
    path('cashflows/<uuid:pk>/', CashFlowDetailView.as_view(), name='cashflow-detail'),
    path('cashboxes/detail/owner/', CashboxOwnerDetailView.as_view(), name='owner-cashboxes'),
    path('cashboxes/<uuid:pk>/detail/owner/', CashboxOwnerDetailSingleView.as_view(), name='owner-cashbox-detail'),

]
