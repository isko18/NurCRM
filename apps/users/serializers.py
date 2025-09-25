from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from apps.users.models import (
    User, Company, Roles, Industry, SubscriptionPlan,
    Feature, Sector, CustomRole
)
from apps.construction.models import Cashbox, Department
from rest_framework.validators import UniqueValidator
from django.core.mail import send_mail
from django.conf import settings
import string
import secrets
from django.contrib.auth.password_validation import validate_password


# ✅ JWT авторизация с дополнительными данными пользователя
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        data.update({
            'user_id': self.user.id,
            'email': self.user.email,
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'avatar': self.user.avatar,
            'company': self.user.company.name if self.user.company else None,
            'role': self.user.role_display,  # строковое название роли
        })
        return data


# 🔑 Пользователь
class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=False,
        min_length=8,
        style={'input_type': 'password'}
    )
    role_display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'password',
            'first_name', 'last_name', 'avatar',
            'company', 'role', 'custom_role', 'role_display',

            # --- Доступы (общие) ---
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # --- Новые доступы ---
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            # Барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # Хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # Кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # Школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '

            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'company']

    def validate_email(self, value):
        if self.instance and self.instance.email == value:
            return value
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email уже занят другим пользователем.")
        return value

    def validate_avatar(self, value):
        if value and not value.startswith(('http://', 'https://')):
            raise serializers.ValidationError("Некорректная ссылка на аватар.")
        return value

    def validate(self, data):
        request = self.context.get('request')
        current_user = request.user if request else None

        permission_fields = [
            # общие
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '
        ]

        for field in permission_fields:
            if field in data:
                if not isinstance(data[field], bool):
                    raise serializers.ValidationError({field: "Значение должно быть True или False."})
                if current_user and current_user.role == 'manager':
                    raise serializers.ValidationError({field: "Менеджеру запрещено изменять права доступа."})

        return data

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


# 👑 Регистрация владельца компании
class OwnerRegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all(), message="Этот email уже используется.")]
    )
    password = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})
    password2 = serializers.CharField(write_only=True, style={'input_type': 'password'})
    company_name = serializers.CharField(write_only=True, required=True)
    company_sector_id = serializers.UUIDField(write_only=True, required=True)
    subscription_plan_id = serializers.UUIDField(write_only=True, required=True)

    class Meta:
        model = User
        fields = [
            'email', 'password', 'password2',
            'first_name', 'last_name',
            'avatar',
            'company_name', 'company_sector_id', 'subscription_plan_id'
        ]

    def validate(self, data):
        if data['password'] != data['password2']:
            raise serializers.ValidationError({"password2": "Пароли не совпадают."})
        return data

    def create(self, validated_data):
        company_name = validated_data.pop('company_name')
        company_sector_id = validated_data.pop('company_sector_id')
        subscription_plan_id = validated_data.pop('subscription_plan_id')
        validated_data.pop('password2')

        # Проверка сектора
        try:
            sector = Sector.objects.get(id=company_sector_id)
        except Sector.DoesNotExist:
            raise serializers.ValidationError({'company_sector_id': 'Выбранный сектор не найден.'})

        industries = sector.industries.all()
        if not industries.exists():
            raise serializers.ValidationError({'company_sector_id': 'Для выбранного сектора не найдена индустрия.'})
        if industries.count() > 1:
            raise serializers.ValidationError({'company_sector_id': 'Для выбранного сектора найдено несколько индустрий. Уточните данные.'})

        industry = industries.first()

        try:
            subscription_plan = SubscriptionPlan.objects.get(id=subscription_plan_id)
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError({'subscription_plan_id': 'Выбранный тариф не найден.'})

        user = User.objects.create(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            avatar=validated_data.get('avatar'),
            role=Roles.OWNER,
            is_active=True
        )

        # 👉 назначаем все флаги доступа владельцу
        permission_fields = [
            # общие
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process', 'can_view_building_objects','can_view_additional_services', "can_view_debts",

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '
        ]
        for field in permission_fields:
            setattr(user, field, True)

        user.set_password(validated_data['password'])
        user.save()

        company = Company.objects.create(
            name=company_name,
            industry=industry,
            sector=sector,
            subscription_plan=subscription_plan,
            owner=user
        )

        user.company = company
        user.save()

        # автосоздание департаментов и кассы для строительной компании
        if industry.name.lower() == "строительная компания":
            default_departments = [
                "Строительный отдел", "Отдел ремонта",
                "Архитектура и дизайн", "Инженерные услуги"
            ]
            for dept_name in default_departments:
                dept = Department.objects.create(company=company, name=dept_name)
                Cashbox.objects.create(department=dept)

        return user


# 👥 Создание сотрудника
class EmployeeCreateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all(), message="Этот email уже используется.")]
    )
    role_display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'email', 'first_name', 'last_name', 'avatar',
            'role', 'custom_role', 'role_display',

            # общие
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process','can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '
        ]
        extra_kwargs = {field: {'required': False} for field in fields if field.startswith('can_view_')}

    def validate(self, data):
        request = self.context['request']
        current_user = request.user

        if current_user.role == 'manager':
            raise serializers.ValidationError("У вас нет прав для создания сотрудников.")
        return data

    def create(self, validated_data):
        request = self.context['request']
        owner = request.user
        company = owner.owned_company

        # случайный пароль
        alphabet = string.ascii_letters + string.digits
        generated_password = ''.join(secrets.choice(alphabet) for _ in range(10))

        # извлекаем флаги
        access_fields = [
            # общие
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process','can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '
        ]
        access_flags = {field: validated_data.pop(field, None) for field in access_fields}

        # создаём сотрудника
        user = User.objects.create(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            avatar=validated_data.get('avatar'),
            role=validated_data.get('role'),
            custom_role=validated_data.get('custom_role'),
            company=company,
            is_active=True
        )
        user.set_password(generated_password)

        # автоназначение прав
        if all(value is None for value in access_flags.values()):
            if user.role in ['owner', 'admin']:
                for field in access_flags:
                    setattr(user, field, True)
            elif user.role == 'manager':
                user.can_view_cashbox = True
                user.can_view_orders = True
                user.can_view_products = True
            else:
                user.can_view_dashboard = True
        else:
            for field, value in access_flags.items():
                if value is not None:
                    setattr(user, field, value)

        user.save()

        # уведомление по email
        try:
            send_mail(
                subject="Добро пожаловать в CRM",
                message=(
                    f"Здравствуйте, {user.first_name}!\n\n"
                    f"Ваш аккаунт создан в системе.\n"
                    f"Логин: {user.email}\n"
                    f"Пароль: {generated_password}\n\n"
                    "Рекомендуем сменить пароль после входа."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            print(f"Ошибка при отправке email сотруднику: {e}")

        self._generated_password = generated_password
        return user

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['generated_password'] = getattr(self, '_generated_password', None)
        return rep


# 🔍 Список сотрудников
class UserListSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role', 'custom_role', 'role_display', 'avatar',

            # общие
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '
        ]


class UserWithPermissionsSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role', 'custom_role', 'role_display', 'avatar',

            # общие
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '
        ]


# 📦 Отрасли и тарифы
class SectorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sector
        fields = ['id', 'name']


class IndustrySerializer(serializers.ModelSerializer):
    sectors = SectorSerializer(many=True, read_only=True)

    class Meta:
        model = Industry
        fields = ['id', 'name', 'sectors']


class FeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feature
        fields = ['id', 'name', 'description']


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    features = FeatureSerializer(many=True)

    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'name', 'price', 'description', 'features']

class CompanySerializer(serializers.ModelSerializer):
    industry = IndustrySerializer(read_only=True)
    subscription_plan = SubscriptionPlanSerializer(read_only=True)
    owner = UserListSerializer(read_only=True)
    sector = SectorSerializer(read_only=True)

    class Meta:
        model = Company
        fields = [
            'id',
            'name',
            'industry',
            'sector',
            'subscription_plan',
            'owner',
            'created_at',
            'start_date',
            'end_date',
            'can_view_documents',
            'can_view_whatsapp',
            'can_view_instagram',
            'can_view_telegram',
            # новые поля
            'llc',
            'inn',
            'okpo',
            'score',
            'bik',
            'address',
        ]
# ✏️ Обновление сотрудника
class EmployeeUpdateSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'first_name', 'last_name', 'avatar',
            'role', 'custom_role', 'role_display',

            # общие
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            # новые
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            # барбершоп
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            # хостел
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
                        'can_view_clients', 'can_view_client_requests', 'can_view_salary ',
            'can_view_sales ', 'can_view_services '
            
        ]
        read_only_fields = ['id']

    def validate(self, data):
        request = self.context['request']
        current_user = request.user
        target_user = self.instance

        if current_user.role == 'manager':
            raise serializers.ValidationError("Менеджеру запрещено редактировать сотрудников.")

        if current_user.id == target_user.id:
            raise serializers.ValidationError("Вы не можете редактировать самого себя через этот интерфейс.")

        if target_user.role == 'owner' and not current_user.is_superuser:
            if 'role' in data and data['role'] != 'owner':
                raise serializers.ValidationError("Вы не можете изменить роль владельца компании.")

        return data


# 🔑 Смена пароля
class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)
    new_password2 = serializers.CharField(required=True, write_only=True)

    def validate(self, data):
        user = self.context['request'].user

        if not user.check_password(data['current_password']):
            raise serializers.ValidationError({"current_password": "Неверный текущий пароль."})

        if data['new_password'] != data['new_password2']:
            raise serializers.ValidationError({"new_password2": "Пароли не совпадают."})

        validate_password(data['new_password'], user)
        return data

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


# 🏢 Обновление компании
class CompanyUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['name']

    def validate_name(self, value):
        if len(value) < 2:
            raise serializers.ValidationError("Название компании слишком короткое.")
        return value


# 🎭 Кастомные роли
class CustomRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomRole
        fields = ["id", "name", "company"]
        read_only_fields = ["id", "company"]
