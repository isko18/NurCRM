from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from apps.users.models import User, Company, Roles
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

# 📝 Регистрация владельца компании
class OwnerRegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all(), message="Этот email уже используется.")]
    )
    password = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})
    password2 = serializers.CharField(write_only=True, style={'input_type': 'password'})
    company_name = serializers.CharField(write_only=True, required=True)
    company_industry = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = [
            'email', 'password', 'password2',
            'first_name', 'last_name',
            'avatar',
            'company_name', 'company_industry'
        ]

    def validate(self, data):
        if data['password'] != data['password2']:
            raise serializers.ValidationError({"password2": "Пароли не совпадают."})
        return data

    def create(self, validated_data):
        company_name = validated_data.pop('company_name')
        company_industry = validated_data.pop('company_industry')
        validated_data.pop('password2')

        # Создаем владельца без компании
        user = User.objects.create(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            avatar=validated_data.get('avatar'),
            role = 'owner',
            is_active=True
        )
        user.set_password(validated_data['password'])
        user.save()

        # Создаем компанию и привязываем владельца
        company = Company.objects.create(
            name=company_name,
            industry=company_industry,
            owner=user
        )

        # Присваиваем владельцу компанию
        user.company = company
        user.save()

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

        # Отправляем email с данными сотруднику
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

        return user

class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'role', 'avatar']
