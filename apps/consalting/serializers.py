# serializers.py
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
    user = serializers.ReadOnlyField(source='user.id')  # текущий пользователь сохраняется автоматически
    client_display = serializers.CharField(source='client.__str__', read_only=True)
    service_price = serializers.DecimalField(
        source='services.price', max_digits=10, decimal_places=2, read_only=True
    )  # Цена услуги
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SaleConsalting
        fields = ('id', 'company', 'user', 'services', 'service_price', 'client', 'client_display', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'company', 'user', 'created_at', 'updated_at', 'service_price')

    def validate_services(self, value):
        # Услуга должна принадлежать той же компании
        company = getattr(self.context.get('request').user, 'company', None)
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Услуга должна принадлежать вашей компании.")
        return value


# ==========================
# SalaryConsalting
# ==========================
class SalaryConsaltingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),  # можно фильтровать по компании в get_queryset
        required=True
    )
    client_display = serializers.CharField(source='user.__str__', read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SalaryConsalting
        fields = ('id', 'company', 'user', 'client_display', 'amount', 'description', 'percent', 'created_at', 'updated_at')
        read_only_fields = ('id', 'company', 'created_at', 'updated_at')

    def validate_user(self, value):
        """Проверка, что выбранный пользователь принадлежит той же компании"""
        company = getattr(self.context.get('request').user, 'company', None)
        if value and company and value.company_id != company.id:
            raise serializers.ValidationError("Выбранный сотрудник должен принадлежать вашей компании.")
        return value


# ==========================
# RequestsConsalting
# ==========================
class RequestsConsaltingSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    client_display = serializers.CharField(source='client.__str__', read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = RequestsConsalting
        fields = ('id', 'company', 'client', 'client_display', 'status', 'name', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'company', 'created_at', 'updated_at')


# ==========================
# BookingConsalting
# ==========================
class BookingConsaltingSerializer(serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')
    employee = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = BookingConsalting
        fields = ('id', 'company', 'title', 'date', 'time', 'employee', 'note', 'created_at', 'updated_at')

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
