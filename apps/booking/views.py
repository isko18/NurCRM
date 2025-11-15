from rest_framework import generics, permissions, filters as drf_filters
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.db.models import Q
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
from apps.users.models import Branch


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


# ---- Миксин company + branch (как в барбере/кафе) ----
class CompanyBranchQuerysetMixin:
    """
    Видимость данных:
      - есть активный филиал → записи этого филиала И глобальные (branch IS NULL)
      - нет активного филиала → только глобальные (branch IS NULL)

    Активный филиал определяется:
      0) ?branch=<uuid> в запросе (если филиал принадлежит компании пользователя)
      1) user.primary_branch() / user.primary_branch
      2) request.branch (если middleware уже положил)
      3) иначе None

    Создание/обновление:
      - company берётся из request.user.company/owned_company
      - branch проставляется автоматически, если смогли определить активный филиал
        (иначе создаём/обновляем как глобальную запись с branch=None)
    """

    # --- helpers ---
    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def _model_has_field(self, field_name: str) -> bool:
        qs = getattr(self, "queryset", None)
        model = getattr(qs, "model", None) if qs is not None else None
        if not model:
            return False
        return any(getattr(f, "name", None) == field_name for f in model._meta.get_fields())

    def _active_branch(self):
        """
        Определяем активный филиал по тем же правилам, что и в сериализаторах.
        Одновременно убеждаемся, что филиал принадлежит компании пользователя.
        """
        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            return None

        # 0) branch из query-параметра
        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company=company)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # если id кривой/чужой — игнорируем и продолжаем
                pass

        user = self._user()

        # 1) primary_branch: метод или поле
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and val.company_id == company.id:
                    setattr(request, "branch", val)
                    return val
            except Exception:
                pass
        if primary and getattr(primary, "company_id", None) == company.id:
            setattr(request, "branch", primary)
            return primary

        # 2) из middleware / ранее установленное
        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == company.id:
                return b

        setattr(request, "branch", None)
        return None

    # --- queryset / save hooks ---
    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()

        qs = super().get_queryset()
        company = self._user_company()
        if not company:
            return qs.none()

        qs = qs.filter(company=company)

        if self._model_has_field("branch"):
            active_branch = self._active_branch()  # None или Branch
            if active_branch is not None:
                # есть филиал → показываем его записи И глобальные
                qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
            else:
                # нет филиала → только глобальные
                qs = qs.filter(branch__isnull=True)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise permissions.PermissionDenied("У пользователя не задана компания.")
        if self._model_has_field("branch"):
            active_branch = self._active_branch()
            serializer.save(company=company, branch=active_branch if active_branch is not None else None)
        else:
            serializer.save(company=company)

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise permissions.PermissionDenied("У пользователя не задана компания.")
        if self._model_has_field("branch"):
            active_branch = self._active_branch()
            # не перетираем branch, если активный филиал не определён
            if active_branch is not None:
                serializer.save(company=company, branch=active_branch)
            else:
                serializer.save(company=company)
        else:
            serializer.save(company=company)


# ========= BookingClient (клиенты) =========
class BookingClientListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = BookingClient.objects.all()
    serializer_class = BookingClientSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    filterset_fields = [
        f.name for f in BookingClient._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]
    ordering = ['name']
    ordering_fields = ['name']


class BookingClientRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BookingClient.objects.all()
    serializer_class = BookingClientSerializer
    permission_classes = [permissions.IsAuthenticated]


class ClientBookingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
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

    def _get_client(self):
        company = self._user_company()
        return get_object_or_404(BookingClient, pk=self.kwargs['pk'], company=company)

    def get_queryset(self):
        qs = super().get_queryset()  # уже отфильтровано по company+branch/global миксином
        client = self._get_client()
        return qs.filter(client=client).order_by('-start_time')

    def perform_create(self, serializer):
        client = self._get_client()
        # company и client выставляем жёстко из URL; branch проставит миксин
        super().perform_create(serializer=serializer)
        serializer.instance.client = client
        serializer.instance.company = client.company
        serializer.instance.save(update_fields=["client", "company"])


# ========= Hotel =========
class HotelListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Hotel.objects.all()
    serializer_class = HotelSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in Hotel._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class HotelRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Hotel.objects.all()
    serializer_class = HotelSerializer
    permission_classes = [IsAdminOrReadOnly]


# ========= Bed =========
class BedListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Bed.objects.all()
    serializer_class = BedSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in Bed._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class BedRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Bed.objects.all()
    serializer_class = BedSerializer
    permission_classes = [IsAdminOrReadOnly]


# ========= ConferenceRoom (Room) =========
class RoomListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = ConferenceRoom.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in ConferenceRoom._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class RoomRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ConferenceRoom.objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAdminOrReadOnly]


# ========= Booking =========
class BookingListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Booking.objects.select_related('hotel', 'room', 'bed', 'client').all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BookingFilter


class BookingRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Booking.objects.select_related('hotel', 'room', 'bed', 'client').all()
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated]


# ========= ManagerAssignment =========
class ManagerAssignmentListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = ManagerAssignment.objects.select_related('room', 'manager').all()
    serializer_class = ManagerAssignmentSerializer
    permission_classes = [IsManagerOrAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in ManagerAssignment._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]


class ManagerAssignmentRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = ManagerAssignment.objects.select_related('room', 'manager').all()
    serializer_class = ManagerAssignmentSerializer
    permission_classes = [IsManagerOrAdmin]


# ========= Folder =========
class FolderListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [
        f.name for f in Folder._meta.get_fields()
        if not f.is_relation or f.many_to_one
    ]
    ordering = ['name']


class FolderRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Folder.objects.select_related('parent').all()
    serializer_class = FolderSerializer
    permission_classes = [permissions.IsAuthenticated]


# ========= Document =========
class DocumentListCreateView(CompanyBranchQuerysetMixin, generics.ListCreateAPIView):
    queryset = Document.objects.select_related('folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = DocumentFilter


class DocumentRetrieveUpdateDestroyView(CompanyBranchQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Document.objects.select_related('folder').all()
    serializer_class = DocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]


# ========= Booking History =========
class BookingHistoryListView(CompanyBranchQuerysetMixin, generics.ListAPIView):
    """
    /booking/history/
    История (архив) всех бронирований компании.
    """
    queryset = (
        BookingHistory.objects
        .select_related("client", "hotel", "room", "bed", "company")
    )
    serializer_class = BookingHistorySerializer
    permission_classes = [permissions.IsAuthenticated]
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
