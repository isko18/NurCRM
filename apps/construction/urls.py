from django.urls import path
from apps.construction.views import (
    CashboxListCreateView,
    CashboxDetailView,
    CashFlowListCreateView,
    CashFlowDetailView,
    CashboxOwnerDetailView,
    CashboxOwnerDetailSingleView,
)

urlpatterns = [
    # CASHBOXES
    path('cashboxes/', CashboxListCreateView.as_view(), name='cashbox-list-create'),
    path('cashboxes/<uuid:pk>/', CashboxDetailView.as_view(), name='cashbox-detail'),

    # CASHFLOWS
    path('cashflows/', CashFlowListCreateView.as_view(), name='cashflow-list-create'),
    path('cashflows/<uuid:pk>/', CashFlowDetailView.as_view(), name='cashflow-detail'),

    # CASHBOX DETAIL WITH FLOWS (owner/admin)
    path('cashboxes/detail/owner/', CashboxOwnerDetailView.as_view(), name='owner-cashboxes'),
    path('cashboxes/<uuid:pk>/detail/owner/', CashboxOwnerDetailSingleView.as_view(), name='owner-cashbox-detail'),
]
