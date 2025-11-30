from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (
    Hotel, ConferenceRoom, Booking, ManagerAssignment,
    Folder, Document, Bed, BookingClient, BookingHistory
)
from apps.users.models import Branch  # для проверки филиала по ?branch=

User = get_user_model()


# ===== Общий миксин company/branch (как в барбере/кафе) =====
class CompanyBranchReadOnlyMixin:
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.
    Порядок получения branch:
      0) ?branch=<uuid> в запросе (если филиал принадлежит компании пользователя)
      1) user.primary_branch() / user.primary_branch
      2) request.branch (если проставлен)
      3) None (глобальная запись компании)
    """

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None) if request else None

    def _user_company(self):
        user = self._user()
        if not user:
            return None
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def _auto_branch(self):
        request = self.context.get("request")
        if not request:
            return None
        user = self._user()
        company = self._user_company()
        user_company_id = getattr(company, "id", None)

        if not user_company_id:
            return None

        # 0) ?branch=<uuid> в query-параметрах
        branch_id = None
        if hasattr(request, "query_params"):
            branch_id = request.query_params.get("branch")
        elif hasattr(request, "GET"):
            branch_id = request.GET.get("branch")

        if branch_id:
            try:
                br = Branch.objects.get(id=branch_id, company_id=user_company_id)
                setattr(request, "branch", br)
                return br
            except (Branch.DoesNotExist, ValueError):
                # если чужой/кривой — игнорируем и идём дальше
                pass

        # 1) primary_branch: метод или поле
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val and getattr(val, "company_id", None) == user_company_id:
                    return val
            except Exception:
                pass
        if primary and getattr(primary, "company_id", None) == user_company_id:
            return primary

        # 2) request.branch из middleware
        if hasattr(request, "branch"):
            b = getattr(request, "branch")
            if b and getattr(b, "company_id", None) == user_company_id:
                return b

        # 3) глобально
        return None

    def create(self, validated_data):
        user_company = self._user_company()
        if user_company:
            validated_data["company"] = user_company
        auto_branch = self._auto_branch()
        validated_data["branch"] = auto_branch if auto_branch is not None else None
        return super().create(validated_data)

    def update(self, instance, validated_data):
        user_company = self._user_company()
        if user_company:
            validated_data["company"] = user_company
        auto_branch = self._auto_branch()
        # ВАЖНО: если филиал не определён, не перетираем существующий branch
        if auto_branch is not None:
            validated_data["branch"] = auto_branch
        return super().update(instance, validated_data)


# ====== helpers для сужения queryset по company/branch ======
def _scope_queryset_by_context(qs, serializer):
    request = serializer.context.get("request")
    if not request:
        return qs.none()
    user = getattr(request, "user", None)
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    company_id = getattr(company, "id", None)
    if not company_id:
        return qs.none()

    # определяем активный филиал так же, как миксин
    branch = None
    cbm = serializer if hasattr(serializer, "_auto_branch") else None
    if cbm:
        branch = cbm._auto_branch()
    else:
        branch = getattr(request, "branch", None)

    qs = qs.filter(company_id=company_id)
    if any(getattr(f, "name", None) == "branch" for f in qs.model._meta.get_fields()):
        if branch is not None:
            qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
        else:
            qs = qs.filter(branch__isnull=True)
    return qs


# ===== Hotel / Bed / Room =====
class HotelSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = Hotel
        fields = ["id", "company", "branch", "name", "capacity", "description", "price"]
        read_only_fields = ["id", "company", "branch"]

    def validate(self, attrs):
        # model.clean() добьёт компанию↔филиал; здесь ничего спец. не нужно
        return attrs


class BedSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = Bed
        fields = ["id", "company", "branch", "name", "capacity", "description", "price"]
        read_only_fields = ["id", "company", "branch"]


class RoomSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = ConferenceRoom
        fields = ["id", "company", "branch", "name", "capacity", "location", "price"]
        read_only_fields = ["id", "company", "branch"]


# ===== Booking (короткая карточка) =====
class BookingBriefSerializer(serializers.ModelSerializer):
    resource = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = ["id", "resource", "start_time", "end_time", "purpose"]

    def get_resource(self, obj):
        if obj.hotel:
            return f"Hotel: {obj.hotel.name}"
        if obj.room:
            return f"Room: {obj.room.name}"
        if obj.bed:
            return f"Bed: {obj.bed.name}"
        return "—"


class BookingHistoryBriefSerializer(serializers.ModelSerializer):
    resource = serializers.SerializerMethodField()

    class Meta:
        model = BookingHistory
        fields = ["id", "resource", "target_price", "start_time", "end_time", "purpose", "archived_at"]

    def get_resource(self, obj):
        type_map = {"hotel": "Hotel", "room": "Room", "bed": "Bed"}
        kind = type_map.get(obj.target_type, "—")
        return f"{kind}: {obj.target_name}" if obj.target_name else kind


# ===== Booking =====
class BookingSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    client = serializers.PrimaryKeyRelatedField(
        queryset=BookingClient.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        ref_name = 'BookingBooking'
        model = Booking
        fields = [
            "id", "company", "branch",
            "hotel", "room", "bed",
            "client",
            "start_time", "end_time",
            "purpose",
        ]
        read_only_fields = ["id", "company", "branch"]

    # сузим связанные PK по company/branch (как в барбере)
    def get_fields(self):
        fields = super().get_fields()
        fields["client"].queryset = _scope_queryset_by_context(BookingClient.objects.all(), self)
        fields["hotel"] = serializers.PrimaryKeyRelatedField(
            queryset=_scope_queryset_by_context(Hotel.objects.all(), self),
            required=False,
            allow_null=True,
        )
        fields["room"] = serializers.PrimaryKeyRelatedField(
            queryset=_scope_queryset_by_context(ConferenceRoom.objects.all(), self),
            required=False,
            allow_null=True,
        )
        fields["bed"] = serializers.PrimaryKeyRelatedField(
            queryset=_scope_queryset_by_context(Bed.objects.all(), self),
            required=False,
            allow_null=True,
        )
        return fields

    def validate(self, attrs):
        """
        - выбран ровно один из hotel/room/bed;
        - все связанные объекты принадлежат компании пользователя;
        - клиент/ресурс — глобальные или из текущего филиала;
        - корректный временной интервал;
        - прогоняем model.clean().
        """
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        user_company = getattr(user, "company", None) or getattr(user, "owned_company", None)
        company_id = getattr(user_company, "id", None)
        target_branch = self._auto_branch()

        hotel  = attrs.get("hotel")  or getattr(self.instance, "hotel", None)
        room   = attrs.get("room")   or getattr(self.instance, "room", None)
        bed    = attrs.get("bed")    or getattr(self.instance, "bed", None)
        client = attrs.get("client") or getattr(self.instance, "client", None)

        # --- Ровно один из hotel/room/bed ---
        chosen = [x for x in (hotel, room, bed) if x]
        if len(chosen) != 1:
            raise serializers.ValidationError(
                "Выберите либо гостиницу, либо комнату, либо койко-место (ровно одно)."
            )

        # --- Компания должна совпадать ---
        if company_id:
            if hotel and hotel.company_id != company_id:
                raise serializers.ValidationError({"hotel": "Отель принадлежит другой компании."})
            if room and room.company_id != company_id:
                raise serializers.ValidationError({"room": "Комната принадлежит другой компании."})
            if bed and bed.company_id != company_id:
                raise serializers.ValidationError({"bed": "Койка принадлежит другой компании."})
            if client and client.company_id != company_id:
                raise serializers.ValidationError({"client": "Клиент из другой компании."})

        # --- Филиал: ресурс/клиент — глобальные или текущего филиала ---
        if target_branch is not None:
            tb_id = target_branch.id
            if hotel and hotel.branch_id not in (None, tb_id):
                raise serializers.ValidationError({"hotel": "Отель принадлежит другому филиалу."})
            if room and room.branch_id not in (None, tb_id):
                raise serializers.ValidationError({"room": "Комната принадлежит другому филиалу."})
            if bed and bed.branch_id not in (None, tb_id):
                raise serializers.ValidationError({"bed": "Койка принадлежит другому филиалу."})
            if client and client.branch_id not in (None, tb_id):
                raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})

        # --- Интервал времени ---
        start = attrs.get("start_time") or getattr(self.instance, "start_time", None)
        end   = attrs.get("end_time")   or getattr(self.instance, "end_time", None)
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "Время окончания должно быть позже времени начала."})

        # Прогоняем model.clean() на временном экземпляре
        temp_kwargs = {**attrs}
        if user_company is not None:
            temp_kwargs.setdefault("company", user_company)
        temp_kwargs.setdefault("branch", target_branch)
        inst = Booking(**temp_kwargs)
        if self.instance:
            inst.id = self.instance.id
        try:
            inst.clean()
        except DjangoValidationError as e:
            if hasattr(e, "message_dict"):
                raise serializers.ValidationError(e.message_dict)
            if hasattr(e, "messages"):
                raise serializers.ValidationError({"detail": e.messages})
            raise serializers.ValidationError({"detail": str(e)})
        return attrs


# ===== BookingClient (с вложенными бронированиями/историей) =====
class BookingClientSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    bookings = BookingBriefSerializer(many=True, read_only=True)
    history = BookingHistoryBriefSerializer(many=True, read_only=True, source="booking_history")

    class Meta:
        model = BookingClient
        fields = ["id", "company", "branch", "phone", "name", "text", "bookings", "history"]
        read_only_fields = ["id", "company", "branch", "bookings", "history"]

    def validate_phone(self, value):
        # простая нормализация
        if not value:
            return value
        return ''.join(ch for ch in value if ch.isdigit() or ch == '+')


# ===== ManagerAssignment =====
class ManagerAssignmentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    # у ManagerAssignment нет branch поля → оставляем как есть

    class Meta:
        model = ManagerAssignment
        fields = ["id", "company", "room", "manager"]
        read_only_fields = ["id", "company"]

    def get_fields(self):
        fields = super().get_fields()
        fields["room"].queryset = _scope_queryset_by_context(ConferenceRoom.objects.all(), self)
        # менеджеры по company:
        request = self.context.get("request")
        if request and getattr(request.user, "company_id", None):
            fields["manager"].queryset = User.objects.filter(company_id=request.user.company_id)
        else:
            fields["manager"].queryset = User.objects.none()
        return fields

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        company = getattr(user, "company", None) or getattr(user, "owned_company", None)
        user_company_id = getattr(company, "id", None)

        room = attrs.get("room") or getattr(self.instance, "room", None)
        manager = attrs.get("manager") or getattr(self.instance, "manager", None)
        if user_company_id:
            if room and room.company_id != user_company_id:
                raise serializers.ValidationError({"room": "Комната из другой компании."})
            if manager and getattr(manager, "company_id", None) != user_company_id:
                raise serializers.ValidationError({"manager": "Пользователь из другой компании."})
        return attrs


# ===== Folder =====
class FolderSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    parent = serializers.PrimaryKeyRelatedField(queryset=Folder.objects.all(), allow_null=True, required=False)
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = Folder
        fields = ["id", "company", "branch", "name", "parent", "parent_name"]
        read_only_fields = ["id", "company", "branch", "parent_name"]

    def get_fields(self):
        fields = super().get_fields()
        fields["parent"].queryset = _scope_queryset_by_context(Folder.objects.all(), self)
        return fields

    def validate_parent(self, parent):
        if parent is None:
            return parent
        # остальное добьёт model.clean()
        return parent


# ===== Document =====
class DocumentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    folder_name = serializers.CharField(source="folder.name", read_only=True)

    class Meta:
        model = Document
        fields = ["id", "company", "branch", "name", "file", "folder", "folder_name", "created_at", "updated_at"]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at", "folder_name"]

    def get_fields(self):
        fields = super().get_fields()
        fields["folder"].queryset = _scope_queryset_by_context(Folder.objects.all(), self)
        return fields

    def validate_folder(self, folder):
        # остальную согласованность проверит model.clean()
        return folder


# ===== BookingHistory (read-only) =====
class BookingHistorySerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    client_name = serializers.CharField(source="client.name", read_only=True)
    client_phone = serializers.CharField(source="client.phone", read_only=True)
    resource = serializers.SerializerMethodField()
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = BookingHistory
        fields = [
            "id", "company", "branch",
            "original_booking_id",
            "target_type", "resource", "target_name", "target_price",
            "hotel", "room", "bed",
            "client", "client_name", "client_phone", "client_label",
            "start_time", "end_time", "purpose",
            "archived_at",
        ]
        read_only_fields = fields  # историю создаёт сигнал

    def get_resource(self, obj):
        type_map = {"hotel": "Hotel", "room": "Room", "bed": "Bed"}
        kind = type_map.get(obj.target_type, "—")
        return f"{kind}: {obj.target_name}" if obj.target_name else kind
