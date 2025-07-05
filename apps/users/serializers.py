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

# 👤 Полный сериализатор пользователя
class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = [
            'id', 'email', 'password',
            'first_name', 'last_name',
            'avatar',
            'company', 'role',
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
    company_industry_id = serializers.UUIDField(write_only=True, required=True)
    subscription_plan_id = serializers.UUIDField(write_only=True, required=True)  # Добавляем поле для тарифа

    class Meta:
        model = User
        fields = [
            'email', 'password', 'password2',
            'first_name', 'last_name',
            'avatar',
            'company_name', 'company_industry_id', 'subscription_plan_id'
        ]

    def validate(self, data):
        # Проверка на совпадение паролей
        if data['password'] != data['password2']:
            raise serializers.ValidationError({"password2": "Пароли не совпадают."})
        return data

    def create(self, validated_data):
        company_name = validated_data.pop('company_name')
        company_industry_id = validated_data.pop('company_industry_id')
        subscription_plan_id = validated_data.pop('subscription_plan_id')
        validated_data.pop('password2')

        try:
            industry = Industry.objects.get(id=company_industry_id)
        except Industry.DoesNotExist:
            raise serializers.ValidationError({'company_industry_id': 'Выбранная отрасль не найдена.'})

        try:
            subscription_plan = SubscriptionPlan.objects.get(id=subscription_plan_id)
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError({'subscription_plan_id': 'Выбранный тариф не найден.'})

        # Создаем владельца
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

        # Создаем компанию
        company = Company.objects.create(
            name=company_name,
            industry=industry,
            subscription_plan=subscription_plan,
            owner=user
        )

        user.company = company
        user.save()

        # ✅ Автоматически создаем отделы и кассы, если отрасль — Строительная компания
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
            'email', 'first_name', 'last_name', 'avatar', 'role'
        ]

    def create(self, validated_data):
        request = self.context['request']
        owner = request.user
        company = owner.owned_company

        # Генерация случайного пароля
        alphabet = string.ascii_letters + string.digits
        generated_password = ''.join(secrets.choice(alphabet) for i in range(10))

        # Создаем сотрудника
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
        user.save()

        # Безопасная отправка email — без падения сервера при ошибках
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
                fail_silently=False,  # оставляем True только если хотим совсем игнорировать
            )
        except Exception as e:
            # Логируем ошибку (если хочешь — можно записывать в лог-файл)
            print(f"Ошибка при отправке email сотруднику: {e}")

        # Возвращаем дополнительные данные (email + пароль)
        self._generated_password = generated_password
        return user

    # Переопределяем to_representation чтобы добавить пароль в ответ
    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['generated_password'] = getattr(self, '_generated_password', None)
        return rep

# 🔍 Сериализатор для списка пользователей
class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'role', 'avatar']

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
