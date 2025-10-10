# barber_crm/serializers.py
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import BarberProfile, Service, Client, Appointment, Document, Folder


# ===========================
# Общий миксин: company/branch (branch авто из пользователя)
# ===========================
class CompanyBranchReadOnlyMixin:
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.
    Порядок получения branch:
      1) user.primary_branch (свойство или метод, если есть)
      2) request.branch (если положил middleware)
      3) None (глобальная запись компании)
    """

    # ---- какой филиал реально использовать ----
    def _auto_branch(self):
        request = self.context.get("request")
        if not request:
            return None
        user = getattr(request, "user", None)

        # 1) primary_branch может быть полем или методом
        primary = getattr(user, "primary_branch", None)
        if callable(primary):
            try:
                val = primary()
                if val:
                    return val
            except Exception:
                pass
        if primary:
            return primary

        # 2) из middleware (на будущее)
        if hasattr(request, "branch"):
            return request.branch

        # 3) глобально
        return None

    def create(self, validated_data):
        request = self.context.get("request")
        if request:
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "company_id", None):
                validated_data["company"] = user.company
            # branch строго из контекста (payload игнорируется)
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        if request:
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "company_id", None):
                validated_data["company"] = user.company
            validated_data["branch"] = self._auto_branch()
        return super().update(instance, validated_data)


# ===========================
# BarberProfile
# ===========================
class BarberProfileSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")  # показываем id филиала (или None)

    class Meta:
        model = BarberProfile
        fields = [
            "id", "company", "branch",
            "full_name", "phone", "extra_phone",
            "work_schedule", "is_active", "created_at",
        ]
        read_only_fields = ["id", "created_at", "company", "branch"]


# ===========================
# Service
# ===========================
class ServiceSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = Service
        fields = ["id", "company", "branch", "name", "price", "is_active"]
        read_only_fields = ["id", "company", "branch"]

    def validate(self, attrs):
        """
        Если активен филиал (для этого пользователя/запроса) —
        услуга может быть только глобальной (None) или этого филиала.
        (Поле branch read-only, поэтому клиент не может подменить филиал.)
        """
        # target_branch = self._auto_branch()
        # Здесь дополнительных проверок не требуется, т.к. branch мы проставим в create().
        return attrs


# ===========================
# Client
# ===========================
class ClientSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    class Meta:
        model = Client
        fields = [
            "id", "company", "branch",
            "full_name", "phone", "email",
            "birth_date", "status", "notes", "created_at",
        ]
        read_only_fields = ["id", "created_at", "company", "branch"]
        ref_name = "BarberClient"

    def validate(self, attrs):
        """
        Клиент создаётся глобально или в текущем филиале пользователя.
        Поле branch read-only, поэтому клиент не может его подменить.
        """
        return attrs


# ===========================
# Appointment
# ===========================
class AppointmentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    # Для удобного отображения
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    barber_name = serializers.SerializerMethodField()
    service_name = serializers.CharField(source="service.name", read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id", "company", "branch",
            "client", "client_name",
            "barber", "barber_name",
            "service", "service_name",
            "start_at", "end_at", "status", "comment", "created_at",
        ]
        read_only_fields = ["id", "created_at", "company", "branch"]

    def get_barber_name(self, obj):
        if not obj.barber:
            return None
        if obj.barber.first_name or obj.barber.last_name:
            return f"{obj.barber.first_name or ''} {obj.barber.last_name or ''}".strip()
        return obj.barber.email

    def validate(self, attrs):
        """
        Проверки:
        - client/barber/service из той же company
        - client/service глобальные или из филиала пользователя
        - дополнительные проверки из model.clean()
        """
        request = self.context.get("request")
        user_company = getattr(getattr(request, "user", None), "company", None) if request else None
        target_branch = self._auto_branch()

        client = attrs.get("client") or getattr(self.instance, "client", None)
        barber = attrs.get("barber") or getattr(self.instance, "barber", None)
        service = attrs.get("service") or getattr(self.instance, "service", None)

        # проверка компании
        for obj, name in [(client, "client"), (barber, "barber"), (service, "service")]:
            if obj and getattr(obj, "company_id", None) != getattr(user_company, "id", None):
                raise serializers.ValidationError({name: "Объект не принадлежит вашей компании."})

        # проверка филиала (клиент/услуга — глобальные или текущего филиала)
        if target_branch is not None:
            if client and getattr(client, "branch_id", None) not in (None, target_branch.id):
                raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})
            if service and getattr(service, "branch_id", None) not in (None, target_branch.id):
                raise serializers.ValidationError({"service": "Услуга принадлежит другому филиалу."})
            # при необходимости: проверить доступ barber к target_branch (если у вас есть членства по филиалам)

        # Собираем временный инстанс для model.clean()
        temp_kwargs = {**attrs}
        if user_company is not None:
            temp_kwargs.setdefault("company", user_company)
        temp_kwargs.setdefault("branch", target_branch)

        instance = Appointment(**temp_kwargs)
        if self.instance:
            instance.id = self.instance.id

        try:
            instance.clean()
        except DjangoValidationError as e:
            if hasattr(e, "message_dict"):
                raise serializers.ValidationError(e.message_dict)
            if hasattr(e, "messages"):
                raise serializers.ValidationError({"detail": e.messages})
            raise serializers.ValidationError({"detail": str(e)})
        except Exception as e:
            raise serializers.ValidationError({"detail": str(e)})

        return attrs


# ===========================
# Folder
# ===========================
class FolderSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

    parent = serializers.PrimaryKeyRelatedField(
        queryset=Folder.objects.all(), allow_null=True, required=False
    )
    parent_name = serializers.CharField(source="parent.name", read_only=True)

    class Meta:
        model = Folder
        fields = ["id", "company", "branch", "name", "parent", "parent_name"]
        read_only_fields = ["id", "company", "branch", "parent_name"]
        ref_name = "BarberFolder"

    def validate_parent(self, parent):
        """
        Родительская папка (если указана) должна быть вашей компании
        и глобальной/филиала пользователя.
        """
        if parent is None:
            return parent
        request = self.context.get("request")
        user_company_id = getattr(getattr(request, "user", None), "company_id", None) if request else None
        target_branch = self._auto_branch()

        if user_company_id and parent.company_id != user_company_id:
            raise serializers.ValidationError("Родительская папка принадлежит другой компании.")

        if target_branch is not None and parent.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError("Родительская папка принадлежит другому филиалу.")
        return parent


# ===========================
# Document
# ===========================
class DocumentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")
    folder_name = serializers.CharField(source="folder.name", read_only=True)

    class Meta:
        ref_name = "BarberDocument"
        model = Document
        fields = [
            "id", "company", "branch",
            "name", "file",
            "folder", "folder_name",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "company", "branch", "created_at", "updated_at", "folder_name"]

    def validate_folder(self, folder):
        """
        Папка должна быть компании пользователя и глобальной/филиала пользователя.
        """
        if folder is None:
            return folder
        request = self.context.get("request")
        user_company_id = getattr(getattr(request, "user", None), "company_id", None) if request else None
        target_branch = self._auto_branch()

        folder_company_id = getattr(getattr(folder, "company", None), "id", None)
        if folder_company_id and user_company_id and folder_company_id != user_company_id:
            raise serializers.ValidationError("Папка принадлежит другой компании.")

        if target_branch is not None and folder.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError("Папка принадлежит другому филиалу.")
        return folder
