from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import RoomViewSet, BookingViewSet, ManagerAssignmentViewSet, HotelViewSet

router = DefaultRouter()
router.register('rooms', RoomViewSet)
router.register('bookings', BookingViewSet)
router.register('assignments', ManagerAssignmentViewSet)
router.register('hotels', HotelViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
