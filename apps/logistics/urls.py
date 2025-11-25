# apps/logistics/urls.py
from django.urls import path
from apps.logistics.views import LogisticsListCreateView, LogisticsDetailView, LogisticsAnalyticsView

urlpatterns = [
    path("logistics/", LogisticsListCreateView.as_view(), name="logistics-list-create"),
    path("logistics/<uuid:pk>/", LogisticsDetailView.as_view(), name="logistics-detail"),
    path("logistics/analytics/", LogisticsAnalyticsView.as_view(), name="logistics-analytics"),
]
