from rest_framework import serializers
from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting
)
from apps.users.models import User


class CompanyReadOnlyMixin:
    """Автоматически ставит company из request.user при create/update и делает company read-only."""
    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and getattr(request.user, 'company', None):
            validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        if request and request.user and getattr(request.user, 'company', None):
            validated_data['company'] = request.user.company
        return super().update(instance, validated_data)


# ==========================
# ServicesConsalting
# ==========================
class ServicesConsaltingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ServicesConsalting
        fields = ('id', 'company', 'name', 'price', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'company', 'created_at', 'updated_at')


# ==========================
# SaleConsalting
# ==========================
class SaleConsaltingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    user = serializers.ReadOnlyField(source='user.id')
    user_display = serializers.SerializerMethodField()
    client_display = serializers.SerializerMethodField()
    service_display = serializers.CharField(source='services.name', read_only=True)
    service_price = serializers.DecimalField(
        source='services.price', max_digits=10, decimal_places=2, read_only=True
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SaleConsalting
        fields = (
            'id', 'company', 'user', 'user_display', 'services', 'service_display', 'service_price',
            'client', 'client_display', 'description', 'created_at', 'updated_at'
        )
        read_only_fields = (
            'id', 'company', 'user', 'user_display', 'service_display', 'service_price', 'created_at', 'updated_at'
        )

    def get_user_display(self, obj):
        if obj.user:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return None

    def get_client_display(self, obj):
        if obj.client:
            return obj.client.full_name  # используем поле full_name
        return None

    def validate_services(self, value):
        company = getattr(self.context.get('request').user, 'company', None)
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Услуга должна принадлежать вашей компании.")
        return value


# ==========================
# SalaryConsalting
# ==========================
class SalaryConsaltingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=True)
    user_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SalaryConsalting
        fields = ('id', 'company', 'user', 'user_display', 'amount', 'description', 'percent', 'created_at', 'updated_at')
        read_only_fields = ('id', 'company', 'user_display', 'created_at', 'updated_at')

    def get_user_display(self, obj):
        if obj.user:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return None

    def validate_user(self, value):
        company = getattr(self.context.get('request').user, 'company', None)
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Выбранный сотрудник должен принадлежать вашей компании.")
        return value


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    client_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = RequestsConsalting
        fields = ('id', 'company', 'client', 'client_display', 'status', 'name', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'company', 'created_at', 'updated_at')

    def get_client_display(self, obj):
        if obj.client:
            return obj.client.full_name
        return None


# ==========================
# BookingConsalting
# ==========================
class BookingConsaltingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    employee = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    employee_display = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = BookingConsalting
        fields = ('id', 'company', 'title', 'date', 'time', 'employee', 'employee_display', 'note', 'created_at', 'updated_at')
        read_only_fields = ('id', 'company', 'created_at', 'updated_at', 'employee_display')

    def get_employee_display(self, obj):
        if obj.employee:
            return f"{obj.employee.first_name} {obj.employee.last_name}".strip()
        return None

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and getattr(request.user, 'company', None):
            validated_data['company'] = request.user.company
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        if request and request.user and getattr(request.user, 'company', None):
            validated_data['company'] = request.user.company
        return super().update(instance, validated_data)
