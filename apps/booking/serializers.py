# serializers.py
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Hotel, ConferenceRoom, Booking, ManagerAssignment,
    Folder, Document, Bed, BookingClient, BookingHistory
)

User = get_user_model()


# ===== Общие миксины и defaults =====
class CompanyReadOnlyMixin:
    """
    Проставляет company из request.user.company / request.user.owned_company
    на create/update и делает поле company "скрытым" наружу (вместе с HiddenField ниже).
    """
    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and getattr(request.user, "is_authenticated", False):
            user_company = getattr(request.user, "company", None) \
                           or getattr(request.user, "owned_company", None)
            if user_company:
                validated_data['company'] = user_company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        if request and request.user and getattr(request.user, "is_authenticated", False):
            user_company = getattr(request.user, "company", None) \
                           or getattr(request.user, "owned_company", None)
            if user_company:
                validated_data['company'] = user_company
        return super().update(instance, validated_data)


class CurrentCompanyDefault:
    """Автоматически подставляет компанию текущего пользователя в HiddenField."""
    requires_context = True
    def __call__(self, serializer_field):
        request = serializer_field.context.get("request")
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            return getattr(user, "company", None) or getattr(user, "owned_company", None)
        return None


# ===== Hotel / Bed / Room =====
class HotelSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())

    class Meta:
        model = Hotel
        fields = ["id", "company", "name", "capacity", "description", "price"]


class BedSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())

    class Meta:
        model = Bed
        fields = ["id", "company", "name", "capacity", "description", "price"]


class RoomSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())

    class Meta:
        model = ConferenceRoom
        fields = ["id", "company", "name", "capacity", "location", "price"]


# ===== Booking (короткая карточка для вложенного чтения у клиента) =====
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
        fields = [
            "id",
            "resource",
            "target_price",
            "start_time",
            "end_time",
            "purpose",
            "archived_at",
        ]

    def get_resource(self, obj):
        type_map = {"hotel": "Hotel", "room": "Room", "bed": "Bed"}
        kind = type_map.get(obj.target_type, "—")
        return f"{kind}: {obj.target_name}" if obj.target_name else kind
    
# ===== Booking =====
class BookingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())
    client = serializers.PrimaryKeyRelatedField(
        queryset=BookingClient.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        ref_name = 'BookingBooking'
        model = Booking
        fields = [
            "id", "company",
            "hotel", "room", "bed",
            "client",
            "start_time", "end_time",
            "purpose",
        ]


    def validate(self, attrs):
        """
        - выбран ровно один из hotel/room/bed;
        - все связанные объекты принадлежат компании пользователя;
        - корректный временной интервал.
        """
        request = self.context.get("request")
        user_company = getattr(getattr(request, "user", None), "company", None) \
                       or getattr(getattr(request, "user", None), "owned_company", None)
        company_id = getattr(user_company, "id", None)

        hotel  = attrs.get("hotel")  or getattr(self.instance, "hotel", None)
        room   = attrs.get("room")   or getattr(self.instance, "room", None)
        bed    = attrs.get("bed")    or getattr(self.instance, "bed", None)
        client = attrs.get("client") or getattr(self.instance, "client", None)

        # --- Ровно один из hotel/room/bed ---
        chosen = [x for x in (hotel, room, bed) if x]
        if len(chosen) != 1:
            raise serializers.ValidationError(
                "Выберите либо гостиницу, либо комнату, либо койко-место, но не несколько сразу."
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

        # --- Интервал времени ---
        start = attrs.get("start_time") or getattr(self.instance, "start_time", None)
        end   = attrs.get("end_time")   or getattr(self.instance, "end_time", None)
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "Время окончания должно быть позже времени начала."})

        return attrs


# ===== BookingClient (с вложенными бронированиями для просмотра) =====
class BookingClientSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())
    bookings = BookingBriefSerializer(many=True, read_only=True)
    history = BookingHistoryBriefSerializer(many=True, read_only=True, source="booking_history")  # <-- NEW

    class Meta:
        model = BookingClient
        fields = ["id", "company", "phone", "name", "text", "bookings", "history"]  # <-- history добавили



# ===== ManagerAssignment =====
class ManagerAssignmentSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())

    class Meta:
        model = ManagerAssignment
        fields = ["id", "company", "room", "manager"]

    def validate(self, attrs):
        request = self.context.get("request")
        user_company = getattr(getattr(request, "user", None), "company", None) \
                       or getattr(getattr(request, "user", None), "owned_company", None)
        company_id = getattr(user_company, "id", None)

        room = attrs.get("room") or getattr(self.instance, "room", None)
        manager = attrs.get("manager") or getattr(self.instance, "manager", None)

        if company_id:
            if room and room.company_id != company_id:
                raise serializers.ValidationError({"room": "Комната из другой компании."})
            if manager and getattr(manager, "company_id", None) and manager.company_id != company_id:
                raise serializers.ValidationError({"manager": "Пользователь из другой компании."})
        return attrs


# ===== Folder =====
class FolderSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    # можно и HiddenField(default=CurrentCompanyDefault()), но оставим id для удобства чтения
    company = serializers.ReadOnlyField(source='company.id')
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Folder.objects.all(), allow_null=True, required=False
    )
    parent_name = serializers.CharField(source='parent.name', read_only=True)

    class Meta:
        model = Folder
        fields = ['id', 'company', 'name', 'parent', 'parent_name']
        read_only_fields = ['id', 'company', 'parent_name']

    def validate_parent(self, parent):
        """Родительская папка (если указана) должна принадлежать той же компании."""
        if parent is None:
            return parent
        request = self.context.get('request')
        user_company = getattr(getattr(request, "user", None), "company", None) \
                       or getattr(getattr(request, "user", None), "owned_company", None)
        user_company_id = getattr(user_company, "id", None)
        if user_company_id and parent.company_id != user_company_id:
            raise serializers.ValidationError('Родительская папка принадлежит другой компании.')
        return parent


# ===== Document =====
class DocumentSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    folder_name = serializers.CharField(source='folder.name', read_only=True)

    class Meta:
        model = Document
        fields = [
            'id', 'company', 'name', 'file',
            'folder', 'folder_name',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'company', 'created_at', 'updated_at', 'folder_name']

    def validate_folder(self, folder):
        """Папка должна принадлежать компании пользователя."""
        request = self.context.get('request')
        user_company = getattr(getattr(request, "user", None), "company", None) \
                       or getattr(getattr(request, "user", None), "owned_company", None)
        user_company_id = getattr(user_company, "id", None)
        folder_company_id = getattr(folder, "company_id", None)
        if folder_company_id and user_company_id and folder_company_id != user_company_id:
            raise serializers.ValidationError('Папка принадлежит другой компании.')
        return folder


class BookingHistorySerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    client_name = serializers.CharField(source="client.name", read_only=True)
    client_phone = serializers.CharField(source="client.phone", read_only=True)
    resource = serializers.SerializerMethodField()

    class Meta:
        model = BookingHistory
        fields = [
            "id", "company",
            "original_booking_id",
            "target_type", "resource", "target_name", "target_price",
            "hotel", "room", "bed",
            "client", "client_name", "client_phone", "client_label",
            "start_time", "end_time", "purpose",
            "archived_at",
        ]
        read_only_fields = fields  # историю создаёт сигнал, менять ничего не нужно

    def get_resource(self, obj):
        type_map = {"hotel": "Hotel", "room": "Room", "bed": "Bed"}
        kind = type_map.get(obj.target_type, "—")
        return f"{kind}: {obj.target_name}" if obj.target_name else kind
