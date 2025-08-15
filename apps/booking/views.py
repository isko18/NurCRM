from rest_framework import viewsets, permissions
from .models import ConferenceRoom, Booking, ManagerAssignment
from .serializers import RoomSerializer, BookingSerializer, ManagerAssignmentSerializer
from .permissions import IsAdminOrReadOnly, IsManagerOrAdmin
from .models import Hotel
from .serializers import HotelSerializer

class RoomViewSet(viewsets.ModelViewSet):
    queryset = ConferenceRoom.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAdminOrReadOnly]

class BookingViewSet(viewsets.ModelViewSet):
    queryset = Booking.objects.all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]

class ManagerAssignmentViewSet(viewsets.ModelViewSet):
    queryset = ManagerAssignment.objects.all()
    serializer_class = ManagerAssignmentSerializer
    permission_classes = [IsManagerOrAdmin]

class HotelViewSet(viewsets.ModelViewSet):
    queryset = Hotel.objects.all()
    serializer_class = HotelSerializer