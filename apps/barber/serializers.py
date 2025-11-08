# barber_crm/serializers.py
from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import BarberProfile, Service, Client, Appointment, Document, Folder

from datetime import timedelta
import re
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
        fields = ["id", "company", "branch", "name", "time", "category", "price", "is_active"]
        read_only_fields = ["id", "company", "branch"]

    def validate_name(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        request = self.context.get("request")
        company_id = getattr(getattr(request, "user", None), "company_id", None) if request else None
        if not company_id:
            return attrs

        target_branch = self._auto_branch()  # тот же источник, что в create()
        name = (attrs.get("name") or getattr(self.instance, "name", "")).strip()
        qs = Service.objects.filter(company_id=company_id, name__iexact=name)
        if target_branch is None:
            qs = qs.filter(branch__isnull=True)   # проверяем среди глобальных
        else:
            qs = qs.filter(branch=target_branch)  # проверяем в филиале

        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise serializers.ValidationError({"name": "Услуга с таким названием уже существует (на этом уровне: глобально/филиал)."})

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

    client_name = serializers.CharField(source="client.full_name", read_only=True)
    barber_name = serializers.SerializerMethodField()
    services = serializers.PrimaryKeyRelatedField(queryset=Service.objects.all(), many=True)
    services_names = serializers.SlugRelatedField(source='services', many=True, read_only=True, slug_field='name')

    class Meta:
        model = Appointment
        fields = [
            "id", "company", "branch",
            "client", "client_name",
            "barber", "barber_name",
            "services", "services_names",
            "start_at", "end_at", "status", "comment", "created_at",
        ]
        read_only_fields = ["id", "created_at", "company", "branch"]

    def get_barber_name(self, obj):
        if not obj.barber:
            return None
        if obj.barber.first_name or obj.barber.last_name:
            return f"{obj.barber.first_name or ''} {obj.barber.last_name or ''}".strip()
        return obj.barber.email

    def create(self, validated_data):
        services = validated_data.pop("services", [])
        instance = super().create(validated_data)
        instance.services.set(services)
        return instance

    def update(self, instance, validated_data):
        services = validated_data.pop("services", None)
        instance = super().update(instance, validated_data)
        if services is not None:
            instance.services.set(services)
        return instance

    def validate(self, attrs):
        # Проверки те же, но нужно пройтись по списку services
        request = self.context.get("request")
        user_company = getattr(getattr(request, "user", None), "company", None) if request else None
        target_branch = self._auto_branch()

        client = attrs.get("client") or getattr(self.instance, "client", None)
        barber = attrs.get("barber") or getattr(self.instance, "barber", None)
        services = attrs.get("services") or getattr(self.instance, "services", [])

        # company проверки
        for obj, name in [(client, "client"), (barber, "barber")]:
            if obj and getattr(obj, "company_id", None) != getattr(user_company, "id", None):
                raise serializers.ValidationError({name: "Объект не принадлежит вашей компании."})
        for service in services:
            if getattr(service, "company_id", None) != getattr(user_company, "id", None):
                raise serializers.ValidationError({"services": "Одна из услуг принадлежит другой компании."})

        # branch проверки
        if target_branch is not None:
            if client and getattr(client, "branch_id", None) not in (None, target_branch.id):
                raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})
            for service in services:
                if getattr(service, "branch_id", None) not in (None, target_branch.id):
                    raise serializers.ValidationError({"services": f"Услуга '{service.name}' принадлежит другому филиалу."})

        return attrs
    
    def _parse_minutes(self, s: str) -> int:
        """Парсим Service.time: '30', '00:30', '1:15', '30m', '1h', '1h30m' -> минуты."""
        if not s:
            return 0
        s = s.strip()
        try:
            return int(s)  # "30"
        except ValueError:
            pass
        if ":" in s:  # "HH:MM" или "MM:SS" — используем как часы:минуты
            h, m = s.split(":", 1)
            return int(h) * 60 + int(m)
        m = re.match(r'(?i)^(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?$', s)
        if m:
            return (int(m.group(1) or 0) * 60) + int(m.group(2) or 0)
        return 0

    def validate(self, attrs):
        attrs = super().validate(attrs)

        start_at = attrs.get("start_at") or getattr(self.instance, "start_at", None)
        end_at   = attrs.get("end_at")   or getattr(self.instance, "end_at", None)
        services = attrs.get("services") or (self.instance.services.all() if self.instance else [])

        # Если конец не передан — попробуем вычислить из услуг
        if start_at and not end_at and services:
            total = 0
            for s in services:
                total += self._parse_minutes(getattr(s, "time", None))
            if total > 0:
                attrs["end_at"] = start_at + timedelta(minutes=total)
                end_at = attrs["end_at"]

        # Строгая проверка: end_at > start_at
        if start_at and end_at and not (end_at > start_at):
            raise serializers.ValidationError({"end_at": "Должно быть строго позже start_at."})

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
