from django.urls import path
from .views import (
    ServicesConsaltingListCreateView,
    ServicesConsaltingRetrieveUpdateDestroyView,
    SaleConsaltingListCreateView,
    SaleConsaltingRetrieveUpdateDestroyView,
    SalaryConsaltingListCreateView,
    SalaryConsaltingRetrieveUpdateDestroyView,
    RequestsConsaltingListCreateView,
    RequestsConsaltingRetrieveUpdateDestroyView,
)

urlpatterns = [
    path('services/', ServicesConsaltingListCreateView.as_view(), name='services-list-create'),
    path('services/<uuid:pk>/', ServicesConsaltingRetrieveUpdateDestroyView.as_view(), name='services-rud'),

    path('sales/', SaleConsaltingListCreateView.as_view(), name='sales-list-create'),
    path('sales/<uuid:pk>/', SaleConsaltingRetrieveUpdateDestroyView.as_view(), name='sales-rud'),

    path('salaries/', SalaryConsaltingListCreateView.as_view(), name='salaries-list-create'),
    path('salaries/<uuid:pk>/', SalaryConsaltingRetrieveUpdateDestroyView.as_view(), name='salaries-rud'),

    path('requests/', RequestsConsaltingListCreateView.as_view(), name='requests-list-create'),
    path('requests/<uuid:pk>/', RequestsConsaltingRetrieveUpdateDestroyView.as_view(), name='requests-rud'),
]
