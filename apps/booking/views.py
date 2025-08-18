# views.py
from rest_framework import generics, permissions
from django_filters.rest_framework import DjangoFilterBackend

from .models import Hotel, ConferenceRoom, Booking, ManagerAssignment, Document, Folder
from .serializers import (
    HotelSerializer, RoomSerializer, BookingSerializer, ManagerAssignmentSerializer, FolderSerializer, DocumentSerializer
)
from .permissions import IsAdminOrReadOnly, IsManagerOrAdmin
from rest_framework.parsers import MultiPartParser, FormParser

class CompanyQuerysetMixin:
    """
    Скоуп по компании текущего пользователя.
    Безопасен для drf_yasg (swagger_fake_view) и AnonymousUser.
    """
    def _user_company(self):
        user = getattr(self.request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None
        # поддержка как user.company, так и user.owned_company (если есть)
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        qs = super().get_queryset()
        company = self._user_company()
        return qs.filter(company=company) if company else qs.none()

    def perform_create(self, serializer):
        company = self._user_company()
        # сериалайзеры уже используют HiddenField(company), но дублируем для надёжности
        serializer.save(company=company) if company else serializer.save()

    def perform_update(self, serializer):
        company = self._user_company()
        serializer.save(company=company) if company else serializer.save()


# ========= Hotel =========
class HotelListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Hotel.objects.all()
    serializer_class = HotelSerializer
    # permissions — используем глобальные настройки; в исходном ViewSet не было явного класса
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Hotel._meta.get_fields() if not f.is_relation or f.many_to_one]


class HotelRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Hotel.objects.all()
    serializer_class = HotelSerializer
    filter_backends = [DjangoFilterBackend]


# ========= ConferenceRoom (Room) =========
class RoomListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = ConferenceRoom.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in ConferenceRoom._meta.get_fields() if not f.is_relation or f.many_to_one]


class RoomRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ConferenceRoom.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAdminOrReadOnly]


# ========= Booking =========
class BookingListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Booking.objects.select_related('hotel', 'room', 'reserved_by').all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Booking._meta.get_fields() if not f.is_relation or f.many_to_one]


class BookingRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Booking.objects.select_related('hotel', 'room', 'reserved_by').all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]


# ========= ManagerAssignment =========
class ManagerAssignmentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = ManagerAssignment.objects.select_related('room', 'manager').all()
    serializer_class = ManagerAssignmentSerializer
    permission_classes = [IsManagerOrAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in ManagerAssignment._meta.get_fields() if not f.is_relation or f.many_to_one]


class ManagerAssignmentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ManagerAssignment.objects.select_related('room', 'manager').all()
    serializer_class = ManagerAssignmentSerializer
    permission_classes = [IsManagerOrAdmin]


class FolderListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Folder._meta.get_fields() if not f.is_relation or f.many_to_one]
    ordering = ['name']


class FolderRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    
class DocumentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = (
        Document.objects
        .select_related('folder')
        .all()
    )
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Document._meta.get_fields() if not f.is_relation or f.many_to_one]


class DocumentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = (
        Document.objects
        .select_related('folder')
        .all()
    )
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]