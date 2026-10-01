# apps/integrations/urls.py
from django.urls import path
from .views import WhatsAppSessionGetView, WhatsAppSessionUpsertView, MeView

urlpatterns = [
    path("companies/<uuid:company_id>/wa/session/", WhatsAppSessionGetView.as_view()),
    path("companies/<uuid:company_id>/wa/session/upsert/", WhatsAppSessionUpsertView.as_view()),
    path("me/", MeView.as_view()),
]
