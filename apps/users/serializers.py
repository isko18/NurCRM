from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework.validators import UniqueValidator
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth.password_validation import validate_password

import string
import secrets

from apps.users.models import (
    User, Company, Roles, Industry, SubscriptionPlan,
    Feature, Sector, CustomRole, Branch, BranchMembership
)
from apps.construction.models import Cashbox, Department


# ===== Вспомогательные =====
def _ensure_owner_or_admin(user):
    if not user or getattr(user, "role", None) not in ("owner", "admin") and not user.is_superuser:
        raise serializers.ValidationError("Требуются права владельца или администратора.")


def _validate_branch_ids_for_company(branch_ids, company):
    if not branch_ids:
        return []
    branches = list(Branch.objects.filter(id__in=branch_ids, company=company))
    if len(branches) != len(set(branch_ids)):
        raise serializers.ValidationError({"branch_ids": "Некоторые филиалы не найдены в вашей компании."})
    return branches


def _sync_user_branches(user: User, branches: list[Branch]):
    """
    Пересобирает членства сотрудника в филиалах:
    - удаляет лишние
    - добавляет недостающие
    - помечает первый филиал как основной
    Если список пустой — очищает членства (остаётся «общим по компании»).
    """
    current_ids = set(user.branch_memberships.values_list("branch_id", flat=True))
    new_ids = set(b.id for b in branches)

    # удаляем лишние
    to_delete = current_ids - new_ids
    if to_delete:
        BranchMembership.objects.filter(user=user, branch_id__in=to_delete).delete()

    # добавляем недостающие
    to_add = new_ids - current_ids
    for b in branches:
        if b.id in to_add:
            BranchMembership.objects.create(user=user, branch=b, is_primary=False)

    # проставляем основной
    BranchMembership.objects.filter(user=user, is_primary=True).update(is_primary=False)
    if branches:
        BranchMembership.objects.filter(user=user, branch_id=branches[0].id).update(is_primary=True)


# ✅ JWT авторизация с доп.данными
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
            'role': self.user.role_display,
            'branch_ids': getattr(self.user, "allowed_branch_ids", []),
            'primary_branch_id': getattr(self.user.primary_branch, "id", None),
        })
        return data


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id", "name", "code", "address", "phone", "email",
            "timezone", "is_active", "created_at", "updated_at"
        ]
    # company – read-only снаружи: не включаем в fields на запись

    def create(self, validated_data):
        # company мог прийти из .save(company=...), уберём его из validated_data
        company = validated_data.pop("company", None)

        if company is None:
            # берём из request.user
            request = self.context.get("request")
            user = getattr(request, "user", None) if request else None
            if user is None:
                raise serializers.ValidationError("Пользователь не определён.")
            company = getattr(user, "owned_company", None) or getattr(user, "company", None)

        if company is None:
            raise serializers.ValidationError("Компания не определена для создания филиала.")

        return Branch.objects.create(company=company, **validated_data)

# 🔑 Пользователь
class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8, style={'input_type': 'password'})
    role_display = serializers.CharField(read_only=True)
    branch_ids = serializers.ListField(
        child=serializers.UUIDField(), read_only=True, source="allowed_branch_ids"
    )
    primary_branch_id = serializers.UUIDField(read_only=True, source="primary_branch.id")

    class Meta:
        model = User
        fields = [
            'id', 'email', 'password',
            'first_name', 'last_name', 'avatar',
            'company', 'role', 'custom_role', 'role_display',
            # доступы
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
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',
            # филиалы (read-only)
            'branch_ids', 'primary_branch_id',
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
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',
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
            'can_view_hostel_clients', 'can_view_hostел_analytics',
            # кафе
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',
            # школа
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',
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


# 👥 Создание сотрудника (+ распределение по филиалам)
class EmployeeCreateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all(), message="Этот email уже используется.")]
    )
    role_display = serializers.CharField(read_only=True)

    # ✅ новые поля для привязки к филиалам при создании
    primary_branch = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    branches = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
        help_text="Список UUID филиалов, к которым нужно прикрепить сотрудника"
    )

    class Meta:
        model = User
        fields = [
            'email', 'first_name', 'last_name', 'avatar',
            'role', 'custom_role', 'role_display',

            # доступы (как было)
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            'can_view_building_work_process','can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',

            # 👇 новые поля
            'primary_branch', 'branches',
        ]
        extra_kwargs = {field: {'required': False} for field in fields if field.startswith('can_view_')}

    def validate(self, data):
        request = self.context['request']
        current_user = request.user

        if current_user.role == 'manager':
            raise serializers.ValidationError("У вас нет прав для создания сотрудников.")

        # валидация филиалов (если переданы)
        owner_company = getattr(current_user, "owned_company", None) or getattr(current_user, "company", None)
        if not owner_company:
            raise serializers.ValidationError("У текущего пользователя не определена компания.")

        primary_branch_id = data.get('primary_branch')
        branch_ids = set(data.get('branches') or [])

        if primary_branch_id:
            branch_ids.add(primary_branch_id)

        if branch_ids:
            branches = Branch.objects.filter(id__in=branch_ids, company=owner_company)
            if branches.count() != len(branch_ids):
                raise serializers.ValidationError("Некоторые филиалы не найдены или принадлежат другой компании.")
            # сохраним на сериализаторе, чтобы не запрашивать повторно
            self._validated_branches = {str(b.id): b for b in branches}
        else:
            self._validated_branches = {}

        return data

    def create(self, validated_data):
        request = self.context['request']
        owner = request.user
        company = owner.owned_company  # как у тебя было

        # заберём и удалим из данных «служебные» поля
        primary_branch_id = validated_data.pop('primary_branch', None)
        branch_ids = validated_data.pop('branches', None) or []

        # случайный пароль
        alphabet = string.ascii_letters + string.digits
        generated_password = ''.join(secrets.choice(alphabet) for _ in range(10))

        # извлекаем флаги (как у тебя было)
        access_fields = [
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',

            'can_view_building_work_process','can_view_building_objects', 'can_view_additional_services', "can_view_debts",

            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',

            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',

            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',

            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',
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

        # автоназначение прав (как было)
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

        # ✅ создаём членства в филиалах (если переданы)
        if hasattr(self, "_validated_branches") and self._validated_branches:
            # сначала все как неосновные
            for bid, branch in self._validated_branches.items():
                BranchMembership.objects.get_or_create(user=user, branch=branch, defaults={"is_primary": False})

            # проставляем основной (если указан)
            if primary_branch_id:
                # сброс прежнего основного у этого пользователя на всякий случай (хоть его и нет пока)
                BranchMembership.objects.filter(user=user, is_primary=True).update(is_primary=False)
                BranchMembership.objects.filter(user=user, branch_id=primary_branch_id).update(is_primary=True)

        # отправка письма — оставляю как было
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

        # отдадим привязанные филиалы (id + название) удобным списком
        rep['branches_attached'] = [
            {"id": str(m.branch_id), "name": m.branch.name, "is_primary": m.is_primary}
            for m in instance.branch_memberships.select_related("branch").all()
        ]
        return rep

# 🔍 Список сотрудников
class UserListSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)
    branch_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True, source="allowed_branch_ids")
    primary_branch_id = serializers.UUIDField(read_only=True, source="primary_branch.id")

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role', 'custom_role', 'role_display', 'avatar',
            # доступы
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',
            # филиалы
            'branch_ids', 'primary_branch_id',
        ]


class UserWithPermissionsSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)
    branch_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True, source="allowed_branch_ids")
    primary_branch_id = serializers.UUIDField(read_only=True, source="primary_branch.id")

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role', 'custom_role', 'role_display', 'avatar',
            # доступы
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',
            # филиалы
            'branch_ids', 'primary_branch_id',
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


# ✏️ Обновление сотрудника (+ перераспределение по филиалам)
class EmployeeUpdateSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)
    branch_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, write_only=True,
        help_text="Полный новый список филиалов сотрудника. Если не указано — без изменений."
    )

    class Meta:
        model = User
        fields = [
            'id', 'first_name', 'last_name', 'avatar',
            'role', 'custom_role', 'role_display',
            # доступы
            'can_view_dashboard', 'can_view_cashbox', 'can_view_departments',
            'can_view_orders', 'can_view_analytics', 'can_view_department_analytics',
            'can_view_products', 'can_view_booking',
            'can_view_employees', 'can_view_clients',
            'can_view_brand_category', 'can_view_settings', 'can_view_sale',
            'can_view_building_work_process', 'can_view_building_objects', 'can_view_additional_services', "can_view_debts",
            'can_view_barber_clients', 'can_view_barber_services',
            'can_view_barber_history', 'can_view_barber_records',
            'can_view_hostel_rooms', 'can_view_hostel_booking',
            'can_view_hostel_clients', 'can_view_hostel_analytics',
            'can_view_cafe_menu', 'can_view_cafe_orders',
            'can_view_cafe_purchasing', 'can_view_cafe_booking',
            'can_view_cafe_clients', 'can_view_cafe_tables',
            'can_view_school_students', 'can_view_school_groups',
            'can_view_school_lessons', 'can_view_school_teachers',
            'can_view_school_leads', 'can_view_school_invoices',
            'can_view_clients', 'can_view_client_requests', 'can_view_salary',
            'can_view_sales', 'can_view_services', 'can_view_agent',
            # филиалы (write-only)
            'branch_ids',
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

        # проверка филиалов, если переданы
        branch_ids = data.get("branch_ids", None)
        if branch_ids is not None:
            company = getattr(current_user, "owned_company", None) or current_user.company
            _validate_branch_ids_for_company(branch_ids, company)

        return data

    def update(self, instance, validated_data):
        branch_ids = validated_data.pop("branch_ids", None)

        # обычные поля
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # перераспределение по филиалам, если явно передали
        if branch_ids is not None:
            company = instance.company
            branches = _validate_branch_ids_for_company(branch_ids, company)
            _sync_user_branches(instance, branches)

        return instance


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
_OPTIONAL_TEXT = ("llc", "inn", "okpo", "score", "bik", "address")

class CompanyUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            "name",
            "llc", "inn", "okpo", "score", "bik", "address",
        ]

    def validate(self, attrs):
        # пустые строки -> None для опциональных текстовых полей
        for f in _OPTIONAL_TEXT:
            if f in attrs and (attrs[f] is None or str(attrs[f]).strip() == ""):
                attrs[f] = None

        if "name" in attrs and len(attrs["name"].strip()) < 2:
            raise serializers.ValidationError({"name": "Название компании слишком короткое."})
        return attrs


# 🎭 Кастомные роли
class CustomRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomRole
        fields = ["id", "name", "company"]
        read_only_fields = ["id", "company"]

class BranchCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name", "code", "address", "phone", "email", "timezone", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        # проставляем company из текущего пользователя
        request = self.context.get("request")
        company = getattr(request.user, "owned_company", None) or request.user.company
        return Branch.objects.create(company=company, **validated_data)

    def validate(self, attrs):
        # (опционально) можно проверить уникальность code внутри компании заранее
        request = self.context.get("request")
        company = getattr(request.user, "owned_company", None) or request.user.company
        code = attrs.get("code")
        if code:
            qs = Branch.objects.filter(company=company, code=code)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"code": "Код филиала должен быть уникален в пределах компании."})
        return super().validate(attrs)