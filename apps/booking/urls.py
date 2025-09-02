from django.urls import path
from .views import (
    # Clients + nested bookings
    BookingClientListCreateView, BookingClientRetrieveUpdateDestroyView, ClientBookingListCreateView,

    # Hotels / Beds / Rooms
    HotelListCreateView, HotelRetrieveUpdateDestroyView,
    BedListCreateView, BedRetrieveUpdateDestroyView,
    RoomListCreateView, RoomRetrieveUpdateDestroyView,

    # Bookings
    BookingListCreateView, BookingRetrieveUpdateDestroyView,

    # Manager assignments
    ManagerAssignmentListCreateView, ManagerAssignmentRetrieveUpdateDestroyView,

    # Folders / Documents
    FolderListCreateView, FolderRetrieveUpdateDestroyView,
    DocumentListCreateView, DocumentRetrieveUpdateDestroyView,
)

urlpatterns = [
    # ==== Clients ====
    path('clients/', BookingClientListCreateView.as_view(), name='client-list'),
    path('clients/<uuid:pk>/', BookingClientRetrieveUpdateDestroyView.as_view(), name='client-detail'),

    # Бронирования конкретного клиента (просмотр/создание прямо из клиента)
    path('clients/<uuid:pk>/bookings/', ClientBookingListCreateView.as_view(), name='client-bookings'),

    # ==== Hotels ====
    path('hotels/', HotelListCreateView.as_view(), name='hotel-list'),
    path('hotels/<uuid:pk>/', HotelRetrieveUpdateDestroyView.as_view(), name='hotel-detail'),

    # ==== Beds ====
    path('beds/', BedListCreateView.as_view(), name='bed-list'),
    path('beds/<uuid:pk>/', BedRetrieveUpdateDestroyView.as_view(), name='bed-detail'),

    # ==== Rooms ====
    path('rooms/', RoomListCreateView.as_view(), name='room-list'),
    path('rooms/<uuid:pk>/', RoomRetrieveUpdateDestroyView.as_view(), name='room-detail'),

    # ==== Bookings ====
    path('bookings/', BookingListCreateView.as_view(), name='booking-list'),
    path('bookings/<uuid:pk>/', BookingRetrieveUpdateDestroyView.as_view(), name='booking-detail'),

    # ==== Manager assignments ====
    path('manager-assignments/', ManagerAssignmentListCreateView.as_view(), name='manager-assignment-list'),
    path('manager-assignments/<uuid:pk>/', ManagerAssignmentRetrieveUpdateDestroyView.as_view(), name='manager-assignment-detail'),

    # ==== Folders ====
    path('folders/', FolderListCreateView.as_view(), name='folder-list'),
    path('folders/<uuid:pk>/', FolderRetrieveUpdateDestroyView.as_view(), name='folder-detail'),

    # ==== Documents ====
    path('documents/', DocumentListCreateView.as_view(), name='document-list'),
    path('documents/<uuid:pk>/', DocumentRetrieveUpdateDestroyView.as_view(), name='document-detail'),
]
