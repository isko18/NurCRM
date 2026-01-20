from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SalesFunnelViewSet, FunnelStageViewSet,
    ContactViewSet, LeadViewSet, DealViewSet,
    WazzuppAccountViewSet, WazzuppMessageViewSet,
    ActivityViewSet
)

router = DefaultRouter()
router.register(r'funnels', SalesFunnelViewSet, basename='funnel')
router.register(r'funnel-stages', FunnelStageViewSet, basename='funnel-stage')
router.register(r'contacts', ContactViewSet, basename='contact')
router.register(r'leads', LeadViewSet, basename='lead')
router.register(r'deals', DealViewSet, basename='deal')
router.register(r'wazzupp-accounts', WazzuppAccountViewSet, basename='wazzupp-account')
router.register(r'wazzupp-messages', WazzuppMessageViewSet, basename='wazzupp-message')
router.register(r'activities', ActivityViewSet, basename='activity')

urlpatterns = [
    path('', include(router.urls)),
]
