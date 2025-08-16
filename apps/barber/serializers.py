# barber_crm/serializers.py
from rest_framework import serializers
from .models import BarberProfile, Service, Client, Appointment


class CompanyReadOnlyMixin:
    """Делает company read-only и проставляет её на create."""
    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and request.user.company_id:
            validated_data['company'] = request.user.company
        return super().create(validated_data)


class BarberProfileSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = BarberProfile
        fields = [
            'id', 'company', 'full_name', 'phone', 'extra_phone',
            'work_schedule', 'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'company']


class ServiceSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Service
        fields = ['id', 'company', 'name', 'price', 'is_active']
        read_only_fields = ['id', 'company']


class ClientSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    class Meta:
        model = Client
        fields = [
            'id', 'company', 'full_name', 'phone', 'email',
            'birth_date', 'status', 'notes', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'company']
        ref_name = 'BarberClient'


class AppointmentSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    company = serializers.ReadOnlyField(source='company.id')

    # Для удобного отображения
    client_name = serializers.CharField(source='client.full_name', read_only=True)
    barber_name = serializers.CharField(source='barber.full_name', read_only=True)
    service_name = serializers.CharField(source='service.name', read_only=True)

    class Meta:
        model = Appointment
        fields = [
            'id', 'company', 'client', 'client_name',
            'barber', 'barber_name',
            'service', 'service_name',
            'start_at', 'end_at', 'status', 'comment', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'company']

    def validate(self, attrs):
        """Проверки перед сохранением + принадлежность компании."""
        request = self.context.get('request')
        user_company = getattr(getattr(request, 'user', None), 'company', None)

        client = attrs.get('client') or getattr(self.instance, 'client', None)
        barber = attrs.get('barber') or getattr(self.instance, 'barber', None)
        service = attrs.get('service') or getattr(self.instance, 'service', None)

        for obj, name in [(client, 'client'), (barber, 'barber'), (service, 'service')]:
            if obj and obj.company_id != getattr(user_company, 'id', None):
                raise serializers.ValidationError({name: 'Объект не принадлежит вашей компании.'})

        instance = Appointment(**attrs)
        # Если редактируем — надо передать id, чтобы не конфликтовал с самим собой
        if self.instance:
            instance.id = self.instance.id

        # Вызовем clean() из модели (валидация пересечений и т.п.)
        try:
            instance.clean()
        except Exception as e:
            raise serializers.ValidationError(str(e))

        return attrs
