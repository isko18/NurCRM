# serializers.py
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Hotel, ConferenceRoom, Booking, ManagerAssignment, Folder, Document

User = get_user_model()

class CompanyReadOnlyMixin:
    """
    Делает company read-only наружу и гарантированно проставляет её
    из request.user.company на create/update.
    """
    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and getattr(request.user, 'company_id', None):
            validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Не даём подменить компанию и поддерживаем единообразие.
        request = self.context.get('request')
        if request and request.user and getattr(request.user, 'company_id', None):
            validated_data['company'] = request.user.company
        return super().update(instance, validated_data)


# ==== defaults для HiddenField ====
class CurrentCompanyDefault:
    requires_context = True
    def __call__(self, serializer_field):
        request = serializer_field.context.get("request")
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            return getattr(user, "company", None) or getattr(user, "owned_company", None)
        return None


class CurrentUserDefault:
    requires_context = True
    def __call__(self, serializer_field):
        request = serializer_field.context.get("request")
        user = getattr(request, "user", None)
        return user if (user and getattr(user, "is_authenticated", False)) else None


# ==== Hotel ====
class HotelSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())

    class Meta:
        model = Hotel
        fields = ["id", "company", "name", "capacity", "description", "price"]


# ==== ConferenceRoom ====
class RoomSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())

    class Meta:
        model = ConferenceRoom
        fields = ["id", "company", "name", "capacity", "location"]


# ==== Booking ====
class BookingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())
    reserved_by = serializers.HiddenField(default=CurrentUserDefault())

    class Meta:
        model = Booking
        fields = [
            "id", "company",
            "hotel", "room",
            "reserved_by",
            "start_time", "end_time",
            "purpose",
        ]

    def validate(self, attrs):
        """
        Дополнительные проверки до model.clean():
        - выбран ровно один из hotel/room;
        - все связанные объекты принадлежат той же компании, что и пользователь;
        - корректный временной интервал.
        """
        request = self.context.get("request")
        user_company = getattr(getattr(request, "user", None), "company", None) \
                       or getattr(getattr(request, "user", None), "owned_company", None)

        hotel = attrs.get("hotel") or getattr(self.instance, "hotel", None)
        room = attrs.get("room") or getattr(self.instance, "room", None)
        reserved_by = attrs.get("reserved_by") or getattr(self.instance, "reserved_by", None)

        # Ровно один из hotel/room
        if (hotel and room) or (not hotel and not room):
            raise serializers.ValidationError("Выберите либо гостиницу, либо комнату, но не обе одновременно.")

        # Компания должна совпадать
        company_id = getattr(user_company, "id", None)
        if company_id:
            if hotel and hotel.company_id != company_id:
                raise serializers.ValidationError({"hotel": "Отель принадлежит другой компании."})
            if room and room.company_id != company_id:
                raise serializers.ValidationError({"room": "Комната принадлежит другой компании."})
            if reserved_by and getattr(reserved_by, "company_id", None) and reserved_by.company_id != company_id:
                raise serializers.ValidationError({"reserved_by": "Пользователь из другой компании."})

        # Интервал времени
        start = attrs.get("start_time") or getattr(self.instance, "start_time", None)
        end = attrs.get("end_time") or getattr(self.instance, "end_time", None)
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "Время окончания должно быть позже времени начала."})

        return attrs


# ==== ManagerAssignment ====
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


class FolderSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
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
        """
        Родительская папка (если указана) должна принадлежать той же компании.
        """
        if parent is None:
            return parent
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)
        if user_company_id and parent.company_id != user_company_id:
            raise serializers.ValidationError('Родительская папка принадлежит другой компании.')
        return parent


# ===== Documents =====
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
        """
        Если у папки есть company, она должна совпадать с company текущего пользователя.
        """
        request = self.context.get('request')
        user_company_id = getattr(getattr(request, 'user', None), 'company_id', None)
        folder_company_id = getattr(getattr(folder, 'company', None), 'id', None)
        if folder_company_id and user_company_id and folder_company_id != user_company_id:
            raise serializers.ValidationError('Папка принадлежит другой компании.')
        return folder