# serializers.py
from rest_framework import serializers
from django.core.exceptions import ValidationError
from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
)
from apps.users.models import User, Company


def get_company_from_request_context(context):
    """
    Попытка извлечь company из serializer.context['request'].
    Возвращает Company или None (не кидает).
    """
    request = context.get('request') if context else None
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return None

    # Попытки получить company из user в нескольких вариантах
    company = getattr(user, 'company', None)
    if company is None:
        profile = getattr(user, 'profile', None)
        if profile is not None:
            company = getattr(profile, 'company', None)
    return company


class CurrentCompanyDefault:
    """
    Default value for company: tries several common places on request.user:
    - request.user.company
    - request.user.profile.company

    IMPORTANT:
    - Для корректной работы drf-yasg и других инструментов
      не выбрасывает исключение при отсутствии request/context,
      а возвращает None. В views в perform_create нужно явно
      передавать company=... при сохранении.
    """
    requires_context = True

    def __call__(self, serializer_field):
        request = serializer_field.context.get('request')
        if not request or not hasattr(request, 'user') or not getattr(request.user, 'is_authenticated', False):
            # Не бросаем ValidationError — это ломало генерацию схемы (swagger) в dev
            return None

        user = request.user
        # Попытки получить company из разных мест — подстройте под проект
        company = getattr(user, 'company', None)
        if company is None:
            profile = getattr(user, 'profile', None)
            if profile is not None:
                company = getattr(profile, 'company', None)

        # Если не найдено — возвращаем None. Views/perform_create должны выставить company.
        return company

    def __repr__(self):
        return '<CurrentCompanyDefault>'


class ServicesConsaltingSerializer(serializers.ModelSerializer):
    company = serializers.HiddenField(default=CurrentCompanyDefault())
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ServicesConsalting
        fields = ('id', 'company', 'name', 'price', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')


class SaleConsaltingSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    company = serializers.HiddenField(default=CurrentCompanyDefault())
    services = serializers.PrimaryKeyRelatedField(queryset=ServicesConsalting.objects.all(), allow_null=True, required=False)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SaleConsalting
        fields = ('id', 'company', 'user', 'services', 'client', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_services(self, value):
        """
        Убедиться, что выбранная услуга принадлежит той же компании,
        что и текущий пользователь (если компания доступна).
        """
        company = get_company_from_request_context(self.context)
        if value is not None and company is not None:
            if value.company_id != company.id:
                raise serializers.ValidationError("Услуга должна принадлежать той же компании, что и текущий пользователь.")
        return value


class SalaryConsaltingSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    company = serializers.HiddenField(default=CurrentCompanyDefault())
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = SalaryConsalting
        fields = ('id', 'company', 'user', 'amount', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')


class RequestsConsaltingSerializer(serializers.ModelSerializer):
    # В модели RequestsConsalting у вас нет поля `user`, поэтому не объявляем его тут.
    company = serializers.HiddenField(default=CurrentCompanyDefault())
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = RequestsConsalting
        fields = ('id', 'company', 'client', 'status', 'name', 'description', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')
