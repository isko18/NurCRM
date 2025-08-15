# barber_crm/serializers.py
from rest_framework import serializers
from .models import BarberProfile, Service, Client, Appointment


class BarberProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BarberProfile
        fields = [
            'id', 'company', 'full_name', 'phone', 'extra_phone',
            'work_schedule', 'is_active', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'company', 'name', 'price', 'is_active']
        read_only_fields = ['id']


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = [
            'id', 'company', 'full_name', 'phone', 'email',
            'birth_date', 'status', 'notes', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
        ref_name = 'BarberClient'


class AppointmentSerializer(serializers.ModelSerializer):
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
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        """Проверки перед сохранением"""
        instance = Appointment(**attrs)

        # Если редактируем — надо передать id, чтобы не конфликтовал с самим собой
        if self.instance:
            instance.id = self.instance.id

        # Запустим clean() из модели
        try:
            instance.clean()
        except Exception as e:
            raise serializers.ValidationError(str(e))

        return attrs
