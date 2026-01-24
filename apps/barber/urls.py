# barber_crm/urls.py
from django.urls import path
from apps.barber import views

urlpatterns = [
    path('services/', views.ServiceListCreateView.as_view(), name='service-list'),
    path('services/<uuid:pk>/', views.ServiceRetrieveUpdateDestroyView.as_view(), name='service-detail'),

    path('clients/', views.ClientListCreateView.as_view(), name='client-list'),
    path('clients/<uuid:pk>/', views.ClientRetrieveUpdateDestroyView.as_view(), name='client-detail'),
    path('clients/<uuid:pk>/visits/history/', views.ClientVisitHistoryListView.as_view(), name='client-visit-history'),
    path('visits/history/', views.VisitHistoryListView.as_view(), name='visit-history'),

    path('appointments/', views.AppointmentListCreateView.as_view(), name='appointment-list'),
    path('appointments/<uuid:pk>/', views.AppointmentRetrieveUpdateDestroyView.as_view(), name='appointment-detail'),
    path('appointments/my/', views.MyAppointmentListView.as_view(), name='my-appointment-list'),
    path('appointments/my/<uuid:pk>/', views.MyAppointmentDetailView.as_view(), name='my-appointment-detail'),

    path('analytics/', views.BarberAnalyticsView.as_view(), name='barber-analytics'),
    path('analytics/my/', views.MyBarberAnalyticsView.as_view(), name='barber-analytics-my'),
     
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
    
    # Онлайн заявки (публичные эндпоинты)
    path("public/<str:company_slug>/bookings/", views.OnlineBookingPublicCreateView.as_view(), name="online-booking-public-create"),
    path("public/<str:company_slug>/services/", views.PublicServicesListView.as_view(), name="public-services-list"),
    path("public/<str:company_slug>/service-categories/", views.PublicServiceCategoriesListView.as_view(), name="public-service-categories-list"),
    path("public/<str:company_slug>/masters/", views.PublicMastersListView.as_view(), name="public-masters-list"),
    path("public/<str:company_slug>/masters/availability/", views.PublicMastersAvailabilityView.as_view(), name="public-masters-availability"),
    path("public/<str:company_slug>/masters/<uuid:master_id>/schedule/", views.PublicMasterScheduleView.as_view(), name="public-master-schedule"),
    
    # Онлайн заявки (защищенные эндпоинты)
    path("bookings/", views.OnlineBookingListView.as_view(), name="online-booking-list"),
    path("bookings/<uuid:pk>/", views.OnlineBookingDetailView.as_view(), name="online-booking-detail"),
    path("bookings/<uuid:pk>/status/", views.OnlineBookingStatusUpdateView.as_view(), name="online-booking-status-update"),
]
