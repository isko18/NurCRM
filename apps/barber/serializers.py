# barber_crm/serializers.py
from rest_framework import serializers
from .models import BarberProfile, Service, Client, Appointment, Document


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


class BarberProfileSerializer(CompanyReadOnlyMixin, serializers.ModelSerializer):
    # Показываем id компании в ответе, но не принимаем его на вход
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
        # Чтобы не конфликтовать с другим ClientSerializer в проекте
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
        """
        Проверки перед сохранением:
        - принадлежность client/barber/service той же компании, что и у пользователя;
        - доменные проверки через model.clean()
        """
        request = self.context.get('request')
        user_company = getattr(getattr(request, 'user', None), 'company', None)

        client = attrs.get('client') or getattr(self.instance, 'client', None)
        barber = attrs.get('barber') or getattr(self.instance, 'barber', None)
        service = attrs.get('service') or getattr(self.instance, 'service', None)

        for obj, name in [(client, 'client'), (barber, 'barber'), (service, 'service')]:
            if obj and obj.company_id != getattr(user_company, 'id', None):
                raise serializers.ValidationError({name: 'Объект не принадлежит вашей компании.'})

        # Соберём временный инстанс для clean()
        instance = Appointment(**attrs)
        if self.instance:
            instance.id = self.instance.id

        try:
            instance.clean()
        except Exception as e:
            raise serializers.ValidationError(str(e))

        return attrs


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
