from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from apps.users.models import User, Company, Roles, Industry, SubscriptionPlan, Feature, Sector  
from apps.construction.models import Cashbox, Department
from rest_framework.validators import UniqueValidator
from django.core.mail import send_mail
from django.conf import settings
import string
import secrets

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
            'role': self.user.role
        })
        return data
    
class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=False,
        min_length=8,
        style={'input_type': 'password'}
    )

    class Meta:
        model = User
        fields = [
            'id', 'email', 'password',
            'first_name', 'last_name',
            'avatar',
            'company', 'role',
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking',
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
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking'
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

        # Получаем индустрию из сектора
        industries = sector.industries.all()
        if not industries.exists():
            raise serializers.ValidationError({'company_sector_id': 'Для выбранного сектора не найдена индустрия.'})
        if industries.count() > 1:
            raise serializers.ValidationError({'company_sector_id': 'Для выбранного сектора найдено несколько индустрий. Уточните данные.'})

        industry = industries.first()

        # Проверка тарифа
        try:
            subscription_plan = SubscriptionPlan.objects.get(id=subscription_plan_id)
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError({'subscription_plan_id': 'Выбранный тариф не найден.'})

        # Создание пользователя
        user = User.objects.create(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            avatar=validated_data.get('avatar'),
            role='owner',
            is_active=True
        )
        user.set_password(validated_data['password'])
        user.save()

        # Создание компании
        company = Company.objects.create(
            name=company_name,
            industry=industry,
            sector=sector,
            subscription_plan=subscription_plan,
            owner=user
        )

        user.company = company
        user.save()

        # Создание отделов и касс, если индустрия строительная
        if industry.name.lower() == "строительная компания":
            default_departments = [
                "Строительный отдел",
                "Отдел ремонта",
                "Архитектура и дизайн",
                "Инженерные услуги"
            ]
            for dept_name in default_departments:
                dept = Department.objects.create(company=company, name=dept_name)
                Cashbox.objects.create(department=dept)

        return user


# 📝 Создание сотрудника с авто-генерацией пароля + отправкой email
class EmployeeCreateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all(), message="Этот email уже используется.")]
    )
    role = serializers.ChoiceField(choices=Roles.choices)

    class Meta:
        model = User
        fields = [
            'email', 'first_name', 'last_name', 'avatar', 'role',
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking',
        ]
        extra_kwargs = {
            'can_view_dashboard': {'required': False},
            'can_view_cashbox': {'required': False},
            'can_view_departments': {'required': False},
            'can_view_orders': {'required': False},
            'can_view_analytics': {'required': False},
            'can_view_products': {'required': False},
            'can_view_booking': {'required': False},
        }

    def validate(self, data):
        request = self.context['request']
        current_user = request.user

        # Запретить менеджеру создавать или редактировать других
        if current_user.role == 'manager':
            raise serializers.ValidationError("У вас нет прав для создания сотрудников.")

        return data

    def create(self, validated_data):
        request = self.context['request']
        owner = request.user
        company = owner.owned_company

        # Генерация случайного пароля
        alphabet = string.ascii_letters + string.digits
        generated_password = ''.join(secrets.choice(alphabet) for _ in range(10))

        # Извлекаем и удаляем флаги доступа (если переданы)
        access_flags = {
            'can_view_dashboard': validated_data.pop('can_view_dashboard', None),
            'can_view_cashbox': validated_data.pop('can_view_cashbox', None),
            'can_view_departments': validated_data.pop('can_view_departments', None),
            'can_view_orders': validated_data.pop('can_view_orders', None),
            'can_view_analytics': validated_data.pop('can_view_analytics', None),
            'can_view_products': validated_data.pop('can_view_products', None),
            'can_view_booking': validated_data.pop('can_view_booking', None),
        }

        # Создание пользователя
        user = User.objects.create(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            avatar=validated_data.get('avatar'),
            role=validated_data['role'],
            company=company,
            is_active=True
        )
        user.set_password(generated_password)

        # Назначение флагов доступа
        if all(flag is None for flag in access_flags.values()):
            # ⚙️ Автоматическое распределение по роли
            if user.role == 'admin':
                user.can_view_dashboard = True
                user.can_view_cashbox = True
                user.can_view_departments = True
                user.can_view_orders = True
                user.can_view_analytics = True
                user.can_view_products = True
                user.can_view_booking = True
            elif user.role == 'manager':
                user.can_view_cashbox = True
                user.can_view_orders = True
                user.can_view_products = True
            else:
                user.can_view_dashboard = True
        else:
            # Если явно переданы флаги — применяем их
            for field, value in access_flags.items():
                if value is not None:
                    setattr(user, field, value)

        user.save()

        # Отправка email
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

# 🔍 Сериализатор для списка пользователей
class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role', 'avatar',
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking'
        ]


class SectorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sector
        fields = ['id', 'name']
# 🔧 Сериализатор для списка видов деятельности
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
    features = FeatureSerializer(many=True)  # Сериализатор для списка функций

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
        ]


class EmployeeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id', 'first_name', 'last_name', 'avatar', 'role',
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_products', 'can_view_booking',
        ]
        read_only_fields = ['id']

    def validate(self, data):
        request = self.context['request']
        current_user = request.user
        target_user = self.instance

        # 🚫 Менеджер не может никого редактировать
        if current_user.role == 'manager':
            raise serializers.ValidationError("Менеджеру запрещено редактировать сотрудников.")

        # 🚫 Нельзя менять себя
        if current_user.id == target_user.id:
            raise serializers.ValidationError("Вы не можете редактировать самого себя через этот интерфейс.")

        # 🚫 Нельзя изменить роль владельца (если ты не суперпользователь)
        if target_user.role == 'owner' and not current_user.is_superuser:
            if 'role' in data and data['role'] != 'owner':
                raise serializers.ValidationError("Вы не можете изменить роль владельца компании.")

        return data
