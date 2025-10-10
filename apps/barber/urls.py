# barber_crm/urls.py
from django.urls import path
from . import views

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
]
