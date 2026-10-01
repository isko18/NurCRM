from django.urls import path
from apps.instagram.views import (
    AccountsListMyCompany,
    AccountConnectLoginView,
    IGAccountLoginView,
    AutoLoginMyCompanyView,
    ThreadsLiveView
)

app_name = "instagram"

urlpatterns = [
    path("accounts/", AccountsListMyCompany.as_view(), name="accounts_list"),
    path("accounts/connect/", AccountConnectLoginView.as_view(), name="account_connect_login"),
    path("accounts/<uuid:pk>/login/", IGAccountLoginView.as_view(), name="account_login"),
    path("accounts/auto-login/", AutoLoginMyCompanyView.as_view(), name="accounts_auto_login"),
    path("accounts/<uuid:pk>/threads/live/", ThreadsLiveView.as_view(), name="threads_live"),
]
