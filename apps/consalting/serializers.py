from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting,
)
from apps.users.models import User


# ==========================
# Общий миксин: company/branch (branch авто из пользователя)
# ==========================
class CompanyBranchReadOnlyMixin(serializers.ModelSerializer):
    """
    Делает company/branch read-only наружу и гарантированно проставляет их из контекста на create/update.
    Порядок получения branch:
      1) user.primary_branch (свойство или метод, если есть)
      2) request.branch (если вы кладёте в middleware)
      3) None (глобальная запись компании)
    """
    company = serializers.ReadOnlyField(source="company.id")
    branch = serializers.ReadOnlyField(source="branch.id")

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

    def _user_company(self):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        # поддержим как employee, так и owner
        return getattr(user, "company", None) or getattr(user, "owned_company", None)

    def create(self, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
            validated_data["branch"] = self._auto_branch()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        company = self._user_company()
        if company is not None:
            validated_data["company"] = company
            validated_data["branch"] = self._auto_branch()
        return super().update(instance, validated_data)


# ==========================
# ServicesConsalting
# ==========================
class ServicesConsaltingSerializer(CompanyBranchReadOnlyMixin):
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ServicesConsalting
        fields = ("id", "company", "branch", "name", "price", "description", "created_at", "updated_at")
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at")

    def validate(self, attrs):
        # branch мы всё равно проставим из контекста, внешние значения игнорим.
        return attrs


# ==========================
# SaleConsalting
# ==========================
class SaleConsaltingSerializer(CompanyBranchReadOnlyMixin):
    user = serializers.ReadOnlyField(source="user.id")  # пользователь — текущий автор/оператор; если нужно, можно сделать PK поле
    user_display = serializers.SerializerMethodField()
    client_display = serializers.SerializerMethodField()
    service_display = serializers.CharField(source="services.name", read_only=True)
    service_price = serializers.DecimalField(source="services.price", max_digits=12, decimal_places=2, read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SaleConsalting
        fields = (
            "id", "company", "branch",
            "user", "user_display",
            "services", "service_display", "service_price",
            "client", "client_display",
            "description",
            "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "company", "branch", "user", "user_display",
            "service_display", "service_price",
            "created_at", "updated_at",
        )

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None

    def get_client_display(self, obj):
        if not obj.client:
            return None
        # стараемся красиво: full_name/название/телефон
        return getattr(obj.client, "full_name", None) or getattr(obj.client, "name", None) or getattr(obj.client, "phone", None)

    def validate_services(self, value):
        company = self._user_company()
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Услуга принадлежит другой компании.")
        # ветка: глобальная или текущая
        target_branch = self._auto_branch()
        if target_branch is not None and value and value.branch_id not in (None, target_branch.id):
            raise serializers.ValidationError("Услуга принадлежит другому филиалу.")
        return value

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()

        services = attrs.get("services") or getattr(self.instance, "services", None)
        client = attrs.get("client") or getattr(self.instance, "client", None)

        if company:
            if client and getattr(client, "company_id", None) != company.id:
                raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        if target_branch is not None:
            if client and getattr(client, "branch_id", None) not in (None, target_branch.id):
                raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})

        # user заполним, если хотим фиксировать текущего оператора автоматически
        request = self.context.get("request")
        if request and getattr(request, "user", None):
            attrs.setdefault("user", request.user)

        # прогон через model.clean() на всякий случай
        try:
            temp = SaleConsalting(**{**attrs, "company": company, "branch": target_branch})
            if self.instance:
                temp.id = self.instance.id
            temp.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))

        return attrs


# ==========================
# SalaryConsalting
# ==========================
class SalaryConsaltingSerializer(CompanyBranchReadOnlyMixin):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=True)
    user_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SalaryConsalting
        fields = ("id", "company", "branch", "user", "user_display", "amount", "percent", "description", "created_at", "updated_at")
        read_only_fields = ("id", "company", "branch", "user_display", "created_at", "updated_at")

    def get_user_display(self, obj):
        if obj.user and (obj.user.first_name or obj.user.last_name):
            return f"{obj.user.first_name or ''} {obj.user.last_name or ''}".strip()
        return getattr(obj.user, "email", None) if obj.user else None

    def validate_user(self, value):
        company = self._user_company()
        if value and company and getattr(value, "company_id", None) != company.id:
            raise serializers.ValidationError("Сотрудник принадлежит другой компании.")
        return value


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingSerializer(CompanyBranchReadOnlyMixin):
    client_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = RequestsConsalting
        fields = ("id", "company", "branch", "client", "client_display", "status", "name", "description", "created_at", "updated_at")
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at")

    def get_client_display(self, obj):
        if not obj.client:
            return None
        return getattr(obj.client, "full_name", None) or getattr(obj.client, "name", None) or getattr(obj.client, "phone", None)

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()
        client = attrs.get("client") or getattr(self.instance, "client", None)

        if company and client and getattr(client, "company_id", None) != company.id:
            raise serializers.ValidationError({"client": "Клиент принадлежит другой компании."})
        if target_branch is not None and client and getattr(client, "branch_id", None) not in (None, target_branch.id):
            raise serializers.ValidationError({"client": "Клиент принадлежит другому филиалу."})

        # прогон через model.clean() для комплексных проверок
        try:
            temp = RequestsConsalting(**{**attrs, "company": company, "branch": target_branch})
            if self.instance:
                temp.id = self.instance.id
            temp.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
        return attrs


# ==========================
# BookingConsalting
# ==========================
class BookingConsaltingSerializer(CompanyBranchReadOnlyMixin):
    employee = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    employee_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = BookingConsalting
        fields = ("id", "company", "branch", "title", "date", "time", "employee", "employee_display", "note", "created_at", "updated_at")
        read_only_fields = ("id", "company", "branch", "created_at", "updated_at", "employee_display")

    def get_employee_display(self, obj):
        if obj.employee and (obj.employee.first_name or obj.employee.last_name):
            return f"{obj.employee.first_name or ''} {obj.employee.last_name or ''}".strip()
        return getattr(obj.employee, "email", None) if obj.employee else None

    def validate_employee(self, value):
        company = self._user_company()
        if value and company and getattr(value, "company_id", None) != company.id:
            raise serializers.ValidationError("Сотрудник принадлежит другой компании.")
        return value

    def validate(self, attrs):
        company = self._user_company()
        target_branch = self._auto_branch()

        # прогон через model.clean() (проверит company/branch/employee и уникальный слот)
        try:
            temp = BookingConsalting(**{**attrs, "company": company, "branch": target_branch})
            if self.instance:
                temp.id = self.instance.id
            temp.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": e.messages if hasattr(e, "messages") else str(e)}))
        return attrs
