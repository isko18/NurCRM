from django.urls import path

from .views import (
    OneCIntegrationView,
    OneCPostingCallbackView,
    OneCSyncRecordListView,
    OneCSyncRecordRetryView,
)

urlpatterns = [
    path("settings/", OneCIntegrationView.as_view(), name="onec-settings"),
    path("sync-records/", OneCSyncRecordListView.as_view(), name="onec-sync-records"),
    path("sync-records/<uuid:pk>/retry/", OneCSyncRecordRetryView.as_view(), name="onec-sync-record-retry"),
    path("callbacks/posting/", OneCPostingCallbackView.as_view(), name="onec-callback-posting"),
]
