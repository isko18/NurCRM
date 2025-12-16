from django.urls import path
from apps.construction.views import (
    CashboxListCreateView,
    CashboxDetailView,
    CashFlowListCreateView,
    CashFlowDetailView,
    CashboxOwnerDetailView,
    CashboxOwnerDetailSingleView,
    CashShiftListView,
    CashShiftDetailView,
    CashShiftOpenView,
    CashShiftCloseView,
    CashFlowBulkStatusUpdateView
)
from apps.construction.sale_history_views import CashShiftSalesListView
urlpatterns = [
    path("cashboxes/", CashboxListCreateView.as_view(), name="cashbox-list-create"),
    path("cashboxes/<uuid:pk>/", CashboxDetailView.as_view(), name="cashbox-detail"),

    path("cashflows/", CashFlowListCreateView.as_view(), name="cashflow-list-create"),
    path("cashflows/<uuid:pk>/", CashFlowDetailView.as_view(), name="cashflow-detail"),
    path("cashflows/bulk/status/", CashFlowBulkStatusUpdateView.as_view(), name="cashflow-bulk-status"),

    path("cashboxes/detail/owner/", CashboxOwnerDetailView.as_view(), name="owner-cashboxes"),
    path("cashboxes/<uuid:pk>/detail/owner/", CashboxOwnerDetailSingleView.as_view(), name="owner-cashbox-detail"),

    path("shifts/", CashShiftListView.as_view(), name="cashshift-list"),
    path("shifts/open/", CashShiftOpenView.as_view(), name="cashshift-open"),
    path("shifts/<uuid:pk>/", CashShiftDetailView.as_view(), name="cashshift-detail"),
    path("shifts/<uuid:pk>/close/", CashShiftCloseView.as_view(), name="cashshift-close"),
    path("cash/shifts/<uuid:pk>/sales/", CashShiftSalesListView.as_view(), name="cash-shift-sales",),
]
