from django.urls import path
from . import views

urlpatterns = [
    path("start-session/<uuid:company_id>/", views.StartSession.as_view()),
    path("send-text/<uuid:company_id>/", views.SendText.as_view()),
    path("send-media/<uuid:company_id>/", views.SendMedia.as_view()),
    path("session/<uuid:company_id>/", views.SessionDetail.as_view()),
    path("messages/<uuid:company_id>/", views.MessageList.as_view()),

    # webhooks
    path("webhook/qr/", views.QRWebhook.as_view()),
    path("webhook/status/", views.StatusWebhook.as_view()),
    path("webhook/message/", views.MessageWebhook.as_view()),
]
