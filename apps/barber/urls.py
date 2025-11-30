# barber_crm/urls.py
from django.urls import path
from apps.barber import views

urlpatterns = [
    path('services/', views.ServiceListCreateView.as_view(), name='service-list'),
    path('services/<uuid:pk>/', views.ServiceRetrieveUpdateDestroyView.as_view(), name='service-detail'),

    path('clients/', views.ClientListCreateView.as_view(), name='client-list'),
    path('clients/<uuid:pk>/', views.ClientRetrieveUpdateDestroyView.as_view(), name='client-detail'),

    path('appointments/', views.AppointmentListCreateView.as_view(), name='appointment-list'),
    path('appointments/<uuid:pk>/', views.AppointmentRetrieveUpdateDestroyView.as_view(), name='appointment-detail'),
    
    path('folders/', views.FolderListCreateView.as_view(), name='folder-list'),
    path('folders/<uuid:pk>/', views.FolderRetrieveUpdateDestroyView.as_view(), name='folder-detail'),
    path('documents/', views.DocumentListCreateView.as_view(), name='document-list'),
    path('documents/<uuid:pk>/', views.DocumentRetrieveUpdateDestroyView.as_view(), name='document-detail'),
    
    path("payouts/", views.PayoutListCreateView.as_view(), name="payout-list-create"),
    path("payouts/<uuid:pk>/", views.PayoutRetrieveUpdateDestroyView.as_view(), name="payout-detail"),
    
    path("service-categories/", views.ServiceCategoryListCreateView.as_view(), name="service-category-list-create"),
    path("service-categories/<uuid:pk>/", views.ServiceCategoryRetrieveUpdateDestroyView.as_view(), name="service-category-detail"),
   
    path("product-sale-payouts/", views.ProductSalePayoutListCreateView.as_view(),
         name="barber-product-sale-payout-list"),
    path("product-sale-payouts/<uuid:pk>/", views.ProductSalePayoutRetrieveUpdateDestroyView.as_view(),
         name="barber-product-sale-payout-detail"),

    path("sale-payouts/", views.PayoutSaleListCreateView.as_view(), name="sale-payout-list"),
    path("sale-payouts/<uuid:pk>/", views.PayoutSaleRetrieveUpdateDestroyView.as_view(), name="sale-payout-detail"),
]
