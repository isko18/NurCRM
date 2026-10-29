# apps/logistics/urls.py
from django.urls import path
from apps.logistics.views import (
    LogisticsListCreateView,
    LogisticsDetailView,
    LogisticsAnalyticsView,
    LogisticsExpenseListCreateView,
)

urlpatterns = [
    path("logistics/", LogisticsListCreateView.as_view(), name="logistics-list-create"),
    path("logistics/<uuid:pk>/", LogisticsDetailView.as_view(), name="logistics-detail"),
    path("logistics/analytics/", LogisticsAnalyticsView.as_view(), name="logistics-analytics"),
    path("analytics/", LogisticsAnalyticsView.as_view(), name="logistics-analytics"),
    path("expenses/", LogisticsExpenseListCreateView.as_view(), name="logistics-expenses"),
]
