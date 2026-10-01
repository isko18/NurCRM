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

      - company берётся из request.user.company/owned_company (или из user.branch.company как fallback)
      - активный филиал определяется:
          1) «жёсткий» филиал сотрудника:
               - user.primary_branch() / user.primary_branch, если привязана к company
               - user.branch, если привязан к company
               - единственный branch в user.branch_ids (если список есть и в нём ровно 1 id этой компании)
          2) если жёсткого филиала нет — позволяем выбрать ?branch=<uuid>, если филиал принадлежит компании
          3) иначе branch = None (режим по всей компании)

    Логика выборки:
      - если branch определён → показываем ТОЛЬКО записи этого филиала;
      - если branch = None → показываем все записи компании (без ограничения по branch).

    Создание:
      - company берётся из пользователя
      - если есть активный филиал → всегда проставляем его в branch;
      - если филиала нет → branch не трогаем, пусть решает сериализатор/валидатор.

    Обновление:
      - company фиксируем;
      - branch НЕ трогаем (чтобы не переносить запись между филиалами случайно).
    """

    # --- helpers ---

    def _user(self):
        return getattr(self.request, "user", None)

    def _user_company(self):
        user = self._user()
        if not user or not getattr(user, "is_authenticated", False):
            return None

        # обычный случай
        company = getattr(user, "owned_company", None) or getattr(user, "company", None)
        if company:
            return company

        # fallback: если компания хранится только через филиал пользователя
        br = getattr(user, "branch", None)
        if br is not None:
            return getattr(br, "company", None)

        return None

    def _model_has_field(self, field_name: str) -> bool:
        qs = getattr(self, "queryset", None)
        model = getattr(qs, "model", None) if qs is not None else None
        if not model:
            return False
        return any(getattr(f, "name", None) == field_name for f in model._meta.get_fields())

    def _fixed_branch_from_user(self, company):
        """
        «Жёстко» назначенный филиал сотрудника (который нельзя сменить ?branch):

          - user.primary_branch() / user.primary_branch (если принадлежит company)
          - user.branch (если принадлежит company)
          - единственный филиал из user.branch_ids (если он один и относится к company)
        """
        user = self._user()
        if not user or not company:
            return None

        company_id = getattr(company, "id", None)

        # 1) primary_branch: метод или поле
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == company_id:
                    return val
            except Exception:
                pass

        if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
            return primary

        # 2) user.branch
        if hasattr(user, "branch"):
            b = getattr(user, "branch")
            if b and getattr(b, "company_id", None) == company_id:
                return b

        # 3) branch_ids: если ровно один филиал — считаем его фиксированным
        branch_ids = getattr(user, "branch_ids", None)
        if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
            try:
                br = Branch.objects.get(id=branch_ids[0], company_id=company_id)
                return br
            except Branch.DoesNotExist:
                pass

        return None

    def _active_branch(self):
        """
        1) жёсткий филиал (primary / user.branch / единственный из branch_ids)
        2) если жёсткого нет — ?branch=<uuid>, если филиал принадлежит компании
        3) иначе None (вся компания)
        """
        request = self.request
        company = self._user_company()
        if not company:
            setattr(request, "branch", None)
            return None

        company_id = getattr(company, "id", None)

        # 1) жёстко назначенный филиал
        fixed = self._fixed_branch_from_user(company)
        if fixed is not None:
            setattr(request, "branch", fixed)
            return fixed

        # 2) если жёсткого филиала нет — позволяем выбирать через ?branch
        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=company_id)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # чужой/битый uuid — игнорируем
                pass

        # 3) никакого филиала → None (работаем по всей компании)
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
            active_branch = self._active_branch()
            if active_branch is not None:
                # сотрудник с филиалом → только этот филиал
                qs = qs.filter(branch=active_branch)
            # если филиал не определён — НЕ фильтруем по branch (вся компания)

        return qs

    def perform_create(self, serializer):
        company = self._user_company()
        if not company:
            raise permissions.PermissionDenied("У пользователя не задана компания.")

        kwargs = {"company": company}

        if self._model_has_field("branch"):
            active_branch = self._active_branch()
            if active_branch is not None:
                # если есть активный филиал — жёстко проставляем его
                kwargs["branch"] = active_branch
            # если филиала нет — branch оставляем как есть (можно делать глобальные записи)

        serializer.save(**kwargs)

    def perform_update(self, serializer):
        company = self._user_company()
        if not company:
            raise permissions.PermissionDenied("У пользователя не задана компания.")

        # company фиксируем, branch не трогаем
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
