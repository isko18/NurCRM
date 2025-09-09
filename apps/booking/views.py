# views.py
from rest_framework import generics, permissions, filters as drf_filters
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as dj_filters
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    Hotel, Bed, ConferenceRoom, Booking, ManagerAssignment,
    Folder, Document, BookingClient, BookingHistory
)
from .serializers import (
    HotelSerializer, BedSerializer, RoomSerializer, BookingSerializer,
    ManagerAssignmentSerializer, FolderSerializer, DocumentSerializer,
    BookingClientSerializer, BookingHistorySerializer
)
from .permissions import IsAdminOrReadOnly, IsManagerOrAdmin


# ---- Кастомные фильтры ----
class DocumentFilter(dj_filters.FilterSet):
    name = dj_filters.CharFilter(lookup_expr='icontains')
    folder = dj_filters.UUIDFilter(field_name='folder__id')
    file_name = dj_filters.CharFilter(field_name='file', lookup_expr='icontains')
    created_at = dj_filters.DateTimeFromToRangeFilter()
    updated_at = dj_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = Document
        fields = ['name', 'folder', 'file_name', 'created_at', 'updated_at']


class BookingFilter(dj_filters.FilterSet):
    start_time = dj_filters.DateTimeFromToRangeFilter()
    end_time = dj_filters.DateTimeFromToRangeFilter()
    hotel_name = dj_filters.CharFilter(field_name='hotel__name', lookup_expr='icontains')
    room_name = dj_filters.CharFilter(field_name='room__name', lookup_expr='icontains')
    bed_name = dj_filters.CharFilter(field_name='bed__name', lookup_expr='icontains')

    class Meta:
        model = Booking
        fields = ['hotel', 'room', 'bed', 'client', 'start_time', 'end_time']


# ---- Миксин для компании ----
class CompanyQuerysetMixin:
    """
    Скоуп по компании текущего пользователя + защита company на create/update.
    Безопасен для drf_yasg (swagger_fake_view) и AnonymousUser.
    """
    def _user_company(self):
        user = getattr(self.request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        qs = super().get_queryset()
        company = self._user_company()
        return qs.filter(company=company) if company else qs.none()

    def perform_create(self, serializer):
        company = self._user_company()
        serializer.save(company=company) if company else serializer.save()

    def perform_update(self, serializer):
        company = self._user_company()
        serializer.save(company=company) if company else serializer.save()


# ========= BookingClient (клиенты) =========
class BookingClientListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = BookingClient.objects.all()
    serializer_class = BookingClientSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    filterset_fields = [f.name for f in BookingClient._meta.get_fields()
                        if not f.is_relation or f.many_to_one]
    ordering = ['name']
    ordering_fields = ['name']


class BookingClientRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BookingClient.objects.all()
    serializer_class = BookingClientSerializer
    permission_classes = [permissions.IsAuthenticated]


class ClientBookingListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    /clients/<uuid:pk>/bookings/
    GET  — список броней клиента
    POST — создать бронь этому клиенту (client и company проставляются автоматически)
    """
    queryset = Booking.objects.select_related('hotel', 'room', 'bed', 'client').all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BookingFilter

    def get_queryset(self):
        qs = super().get_queryset()
        client = self._get_client()
        return qs.filter(client=client).order_by('-start_time')

    def _get_client(self):
        company = self._user_company()
        return get_object_or_404(BookingClient, pk=self.kwargs['pk'], company=company)

    def perform_create(self, serializer):
        client = self._get_client()
        # company и client выставляем жёстко из URL
        serializer.save(company=client.company, client=client)


# ========= Hotel =========
class HotelListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Hotel.objects.all()
    serializer_class = HotelSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Hotel._meta.get_fields()
                        if not f.is_relation or f.many_to_one]


class HotelRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Hotel.objects.all()
    serializer_class = HotelSerializer
    permission_classes = [IsAdminOrReadOnly]


# ========= Bed =========
class BedListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Bed.objects.all()
    serializer_class = BedSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Bed._meta.get_fields()
                        if not f.is_relation or f.many_to_one]


class BedRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Bed.objects.all()
    serializer_class = BedSerializer
    permission_classes = [IsAdminOrReadOnly]


# ========= ConferenceRoom (Room) =========
class RoomListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = ConferenceRoom.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in ConferenceRoom._meta.get_fields()
                        if not f.is_relation or f.many_to_one]


class RoomRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ConferenceRoom.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAdminOrReadOnly]


# ========= Booking =========
class BookingListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Booking.objects.select_related('hotel', 'room', 'bed', 'client').all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BookingFilter


class BookingRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Booking.objects.select_related('hotel', 'room', 'bed', 'client').all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]


# ========= ManagerAssignment =========
class ManagerAssignmentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = ManagerAssignment.objects.select_related('room', 'manager').all()
    serializer_class = ManagerAssignmentSerializer
    permission_classes = [IsManagerOrAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in ManagerAssignment._meta.get_fields()
                        if not f.is_relation or f.many_to_one]


class ManagerAssignmentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ManagerAssignment.objects.select_related('room', 'manager').all()
    serializer_class = ManagerAssignmentSerializer
    permission_classes = [IsManagerOrAdmin]


# ========= Folder =========
class FolderListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Folder._meta.get_fields()
                        if not f.is_relation or f.many_to_one]
    ordering = ['name']


class FolderRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]


# ========= Document =========
class DocumentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Document.objects.select_related('folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = DocumentFilter


class DocumentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Document.objects.select_related('folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]


# ========= Booking History =========
class BookingHistoryListView(CompanyQuerysetMixin, generics.ListAPIView):
    """
    /booking/history/
    История (архив) всех бронирований компании.
    """
    queryset = (BookingHistory.objects
                .select_related("client", "hotel", "room", "bed", "company"))
    serializer_class = BookingHistorySerializer
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = [
        "client", "target_type", "hotel", "room", "bed",
        "start_time", "end_time", "archived_at", "original_booking_id",
    ]
    search_fields = ["client__name", "client__phone", "target_name", "purpose"]
    ordering_fields = ["start_time", "end_time", "archived_at", "id"]


class ClientBookingHistoryListView(BookingHistoryListView):
    """
    /booking/clients/<uuid:client_id>/history/
    """
    def get_queryset(self):
        return super().get_queryset().filter(client_id=self.kwargs["client_id"])
