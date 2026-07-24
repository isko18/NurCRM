from datetime import timedelta

import secrets
import string

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

import re

from django.db.models.functions import Lower

from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

# --- slug (формат: ^[a-z0-9]+(?:-[a-z0-9]+)*$, длина 3..50) ---
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MIN, SLUG_MAX = 3, 50


def normalize_slug(value) -> str:
    return (value or "").strip().lower()


def slug_format_error(value):
    """Возвращает текст ошибки если формат невалиден, иначе None."""
    if len(value) < SLUG_MIN or len(value) > SLUG_MAX:
        return f"Slug должен быть от {SLUG_MIN} до {SLUG_MAX} символов."
    if not SLUG_RE.match(value):
        return "Разрешены строчные латинские буквы, цифры и дефис (не подряд, не по краям)."
    return None


def slug_taken_by_other(value, exclude_pk=None) -> bool:
    qs = Company.objects.annotate(_s=Lower("slug")).filter(_s=value)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


class SlugConflict(APIException):
    status_code = 409
    default_detail = {"slug": ["Slug already exists"]}
    default_code = "conflict"

from apps.users.models import (
    User, Company, Roles, Industry, SubscriptionPlan,
    Feature, Sector, CustomRole, Branch, BranchMembership, KyrgyzstanRegion,
    SCALE_BARCODE_LAYOUT_PLU,
)


# ======================
# Helpers
# ======================

def _ensure_owner_or_admin(user):
    if not user or (getattr(user, "role", None) not in ("owner", "admin") and not getattr(user, "is_superuser", False)):
        raise serializers.ValidationError("Требуются права владельца или администратора.")


def _get_user_company(user):
    return getattr(user, "owned_company", None) or getattr(user, "company", None)

def _is_market_company(company: Company) -> bool:
    try:
        return bool(company and getattr(company, "is_market", None) and company.is_market())
    except Exception:
        return False

def _apply_market_cashier_gate(rep: dict, user: User):
    """
    Гейт "интерфейс кассира доступен только для сферы Маркет":
    - если компания НЕ market -> can_view_cashier всегда False (даже если флаг True в БД)
    """
    company = _get_user_company(user)
    if not _is_market_company(company):
        rep["can_view_cashier"] = False
    return rep


def _validate_branch_ids_for_company(branch_ids, company):
    if not branch_ids:
        return []
    # уникальные id без лишних запросов
    unique_ids = list(dict.fromkeys(branch_ids))
    branches = list(Branch.objects.filter(id__in=unique_ids, company=company))
    if len(branches) != len(unique_ids):
        raise serializers.ValidationError({"branch_ids": "Некоторые филиалы не найдены в вашей компании."})
    # сохраняем порядок как пришло
    by_id = {str(b.id): b for b in branches}
    ordered = [by_id[str(bid)] for bid in unique_ids]
    return ordered


@transaction.atomic
def _sync_user_branches(user: User, branches: list[Branch]):
    """
    Пересобирает членства:
    - удаляет лишние
    - добавляет недостающие
    - первый филиал делает primary
    Пустой список -> очищаем membership (пользователь остаётся «глобальный по компании»)
    """
    current_ids = set(user.branch_memberships.values_list("branch_id", flat=True))
    new_ids = set(b.id for b in branches)

    to_delete = current_ids - new_ids
    if to_delete:
        BranchMembership.objects.filter(user=user, branch_id__in=to_delete).delete()

    to_add = new_ids - current_ids
    if to_add:
        BranchMembership.objects.bulk_create(
            [BranchMembership(user=user, branch=b, is_primary=False) for b in branches if b.id in to_add],
            ignore_conflicts=True,
        )

    # primary: гарантируем ровно один
    BranchMembership.objects.filter(user=user, is_primary=True).update(is_primary=False)
    if branches:
        BranchMembership.objects.filter(user=user, branch_id=branches[0].id).update(is_primary=True)


@transaction.atomic
def _set_primary_branch(user: User, branch_id):
    """Ставит primary филиал пользователю, учитывая constraint '1 primary'."""
    BranchMembership.objects.filter(user=user, is_primary=True).update(is_primary=False)
    if branch_id:
        BranchMembership.objects.filter(user=user, branch_id=branch_id).update(is_primary=True)


def _generate_password(length=6):
    """Простой пароль из цифр (легче ввести и запомнить)."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


# ======================
# JWT
# ======================

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)

        user = (
            User.objects.filter(pk=self.user.pk)
            .select_related("company", "custom_role")
            .prefetch_related(
                Prefetch(
                    "branch_memberships",
                    queryset=BranchMembership.objects.select_related("branch"),
                ),
            )
            .first()
        )
        if user is None:
            raise serializers.ValidationError("Пользователь не найден.")
        self.user = user

        if not getattr(user, "is_active", True):
            raise serializers.ValidationError("Аккаунт деактивирован.")

        branch_ids = []
        primary_branch_id = None
        for mb in user.branch_memberships.all():
            branch_ids.append(mb.branch_id)
            if mb.is_primary:
                primary_branch_id = mb.branch_id

        data.update({
            "user_id": user.id,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "avatar": user.avatar,
            "phone_number": user.phone_number,
            "track_number": user.track_number,
            "company": user.company.name if user.company else None,
            "role": user.role_display,
            "branch_ids": branch_ids,
            "primary_branch_id": primary_branch_id,
        })
        return data


# ======================
# Branch
# ======================

class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id", "name", "code", "address", "phone", "email",
            "timezone", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class BranchCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id", "name", "code", "address", "phone", "email",
            "timezone", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        request = self.context.get("request")
        company = _get_user_company(getattr(request, "user", None)) if request else None
        if not company:
            raise serializers.ValidationError("Компания не определена.")

        code = attrs.get("code")
        if code:
            qs = Branch.objects.filter(company=company, code=code)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"code": "Код филиала должен быть уникален в пределах компании."})

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        company = _get_user_company(getattr(request, "user", None)) if request else None
        if not company:
            raise serializers.ValidationError("Компания не определена для создания филиала.")
        return Branch.objects.create(company=company, **validated_data)


# ======================
# User (current user)
# ======================

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8, style={"input_type": "password"})
    role_display = serializers.CharField(read_only=True)

    branch_ids = serializers.SerializerMethodField()
    primary_branch_id = serializers.SerializerMethodField()
    funnel_grants = serializers.SerializerMethodField()

    def get_funnel_grants(self, obj):
        try:
            return [
                {
                    "funnel_id": str(g.funnel_id),
                    "can_manage_leads": g.can_manage_leads,
                    "can_manage_stages": g.can_manage_stages,
                }
                for g in obj.funnel_grants.all()
            ]
        except Exception:
            return []

    def _memberships_once(self, obj):
        cache = self.context.setdefault("_branch_memberships_cache", {})
        oid = obj.pk
        if oid not in cache:
            cache[oid] = list(obj.branch_memberships.all())
        return cache[oid]

    def get_branch_ids(self, obj):
        return [m.branch_id for m in self._memberships_once(obj)]

    def get_primary_branch_id(self, obj):
        for m in self._memberships_once(obj):
            if m.is_primary:
                return m.branch_id
        return None

    class Meta:
        model = User
        fields = [
            "id", "email", "password",
            "first_name", "last_name", "track_number", "phone_number", "avatar",
            "company", "role", "custom_role", "role_display",

            "can_view_dashboard", "can_view_cashbox", "can_view_departments",
            "can_view_orders", "can_view_analytics", "can_view_department_analytics",
            "can_view_products", "can_view_booking",
            "can_view_employees", "can_view_clients",
            "can_view_brand_category", "can_view_settings", "can_view_sale",

            "can_view_building_analytics", "can_view_building_cash_register",
            "can_view_building_clients", "can_view_building_department",
            "can_view_building_employess", "can_view_building_notification",
            "can_view_building_procurement", "can_view_building_projects",
            "can_view_building_salary", "can_view_building_sell",
            "can_view_building_stock", "can_view_building_treaty",
            "can_view_building_work_process", "can_view_building_objects",
            "can_view_additional_services", "can_view_debts",

            "can_view_barber_clients", "can_view_barber_services",
            "can_view_barber_history", "can_view_barber_records",

            "can_view_hostel_rooms", "can_view_hostel_booking",
            "can_view_hostel_clients", "can_view_hostel_analytics",

            "can_view_cafe_menu", "can_view_cafe_orders",
            "can_view_cafe_purchasing", "can_view_cafe_booking",
            "can_view_cafe_clients", "can_view_cafe_tables",
            "can_view_cafe_cook", "can_view_cafe_inventory", "can_view_cafe_calculation",
            "can_view_cafe_order_pay", "can_view_cafe_order_return",

            "can_view_school_students", "can_view_school_groups",
            "can_view_school_lessons", "can_view_school_teachers",
            "can_view_school_leads", "can_view_school_invoices",

            "can_view_client_requests", "can_view_salary",
            "can_view_sales", "can_view_services",
            "can_view_agent", "can_view_catalog",
            "can_view_branch", "can_view_logistics", "can_view_request", "can_view_shifts",
            "can_view_cashier", "can_view_document", "can_view_market_scales", "can_view_market_label",
            "can_view_market_discount", "can_view_market_edit_price", "can_view_market_delete_cart_item",
            "can_view_market_procurement", "can_view_market_supplier", "can_view_market_employee_return",

            # Consulting: воронка продаж
            "can_view_funnel", "can_manage_funnel_leads", "can_manage_funnel_stages", "funnel_grants",

            "branch_ids", "primary_branch_id",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "company"]

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if self.instance and (self.instance.email or "").strip().lower() == value:
            return value
        if User.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError("Email уже занят другим пользователем.")
        return value

    def validate_avatar(self, value):
        if value and not value.startswith(("http://", "https://")):
            raise serializers.ValidationError("Некорректная ссылка на аватар.")
        return value

    def validate(self, data):
        request = self.context.get("request")
        current_user = getattr(request, "user", None) if request else None

        if current_user and getattr(current_user, "role", None) == "manager":
            # менеджеру запрещаем менять любые permission-флаги
            if any(k.startswith("can_view_") for k in data.keys()):
                raise serializers.ValidationError("Менеджеру запрещено изменять права доступа.")

        # проверка типов только для тех флагов, которые реально прислали
        for k, v in data.items():
            if k.startswith("can_view_") and not isinstance(v, bool):
                raise serializers.ValidationError({k: "Значение должно быть True или False."})

        return data

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        return _apply_market_cashier_gate(rep, instance)


# ======================
# Owner register
# ======================

class OwnerRegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})
    password2 = serializers.CharField(write_only=True, style={"input_type": "password"})
    company_name = serializers.CharField(write_only=True, required=True)
    company_sector_id = serializers.UUIDField(write_only=True, required=True)
    company_region = serializers.ChoiceField(
        choices=KyrgyzstanRegion.choices,
        write_only=True,
        required=False,
        allow_null=True,
        allow_blank=True,
    )
    subscription_plan_id = serializers.UUIDField(write_only=True, required=True)

    class Meta:
        model = User
        fields = [
            "email", "password", "password2",
            "first_name", "last_name",
            "avatar",
            "company_name", "company_sector_id", "company_region", "subscription_plan_id",
        ]

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if User.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError("Этот email уже используется.")
        return value

    def validate(self, data):
        if data["password"] != data["password2"]:
            raise serializers.ValidationError({"password2": "Пароли не совпадают."})
        return data

    @transaction.atomic
    def create(self, validated_data):
        company_name = validated_data.pop("company_name")
        sector_id = validated_data.pop("company_sector_id")
        company_region = validated_data.pop("company_region", None) or None
        plan_id = validated_data.pop("subscription_plan_id")
        validated_data.pop("password2")

        try:
            sector = Sector.objects.get(id=sector_id)
        except Sector.DoesNotExist:
            raise serializers.ValidationError({"company_sector_id": "Выбранный сектор не найден."})

        industries = sector.industries.all()
        if not industries.exists():
            raise serializers.ValidationError({"company_sector_id": "Для выбранного сектора не найдена индустрия."})
        if industries.count() > 1:
            raise serializers.ValidationError({"company_sector_id": "Для выбранного сектора найдено несколько индустрий."})
        industry = industries.first()

        try:
            subscription_plan = SubscriptionPlan.objects.get(id=plan_id)
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError({"subscription_plan_id": "Выбранный тариф не найден."})

        user = User.objects.create(
            email=(validated_data["email"] or "").strip().lower(),
            first_name=validated_data.get("first_name"),
            last_name=validated_data.get("last_name"),
            avatar=validated_data.get("avatar"),
            role=Roles.OWNER,
            is_active=True,
        )

        # владельцу — все доступы
        for f in [x.name for x in User._meta.fields if x.name.startswith("can_view_")]:
            setattr(user, f, True)

        user.set_password(validated_data["password"])
        user.save()

        company = Company.objects.create(
            name=company_name,
            industry=industry,
            sector=sector,
            region=company_region,
            subscription_plan=subscription_plan,
            owner=user,
            scale_barcode_layout=SCALE_BARCODE_LAYOUT_PLU,
        )

        user.company = company
        user.save(update_fields=["company"])

        # ВНИМАНИЕ: «Основная касса компании» создаётся сигналом
        # apps/construction/signals.py::create_cashbox_for_company.
        # Здесь дублирующее создание убрано, чтобы не плодить пустые кассы.

        return user


# ======================
# Employee create
# ======================

class EmployeeCreateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    role_display = serializers.CharField(read_only=True)

    primary_branch = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    branches = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
        help_text="Список UUID филиалов, к которым нужно прикрепить сотрудника",
    )

    class Meta:
        model = User
        fields = [
            "email", "first_name", "last_name", "track_number", "phone_number", "avatar",
            "role", "custom_role", "role_display",

            # все can_view_* которые ты раньше использовал
            *[f.name for f in User._meta.fields if f.name.startswith("can_view_")],

            "primary_branch", "branches",
        ]

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if User.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError("Этот email уже используется.")
        return value

    def validate(self, data):
        request = self.context["request"]
        current_user = request.user

        if getattr(current_user, "role", None) == "manager":
            raise serializers.ValidationError("У вас нет прав для создания сотрудников.")

        company = getattr(current_user, "owned_company", None)
        if not company:
            raise serializers.ValidationError("Только владелец может создавать сотрудников.")

        primary_branch_id = data.get("primary_branch")
        branch_ids = list(data.get("branches") or [])

        # primary обязательно должен быть в списке
        if primary_branch_id and primary_branch_id not in branch_ids:
            branch_ids = [primary_branch_id] + branch_ids

        branches = _validate_branch_ids_for_company(branch_ids, company)
        data["_branches_objects"] = branches  # локально, без self-полей

        # если primary передали — проверим что он реально в компании (уже проверено выше)
        return data

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        owner = request.user
        company = owner.owned_company

        primary_branch_id = validated_data.pop("primary_branch", None)
        validated_data.pop("branches", None)

        branches_objects = validated_data.pop("_branches_objects", [])

        generated_password = _generate_password()

        # заберём все can_view_* (опциональные) и не дадим им попасть в User.objects.create если хочешь
        access_flags = {}
        for f in [x.name for x in User._meta.fields if x.name.startswith("can_view_")]:
            access_flags[f] = validated_data.pop(f, None)

        user = User.objects.create(
            email=(validated_data.get("email") or "").strip().lower(),
            first_name=validated_data.get("first_name"),
            last_name=validated_data.get("last_name"),
            track_number=validated_data.get("track_number"),
            phone_number=validated_data.get("phone_number"),
            avatar=validated_data.get("avatar"),
            role=validated_data.get("role"),
            custom_role=validated_data.get("custom_role"),
            company=company,
            is_active=True,
        )
        user.set_password(generated_password)

        # автоназначение прав (как у тебя)
        if all(v is None for v in access_flags.values()):
            if user.role in ["owner", "admin"]:
                for k in access_flags.keys():
                    setattr(user, k, True)
            elif user.role == "manager":
                user.can_view_cashbox = True
                user.can_view_orders = True
                user.can_view_products = True
            else:
                user.can_view_dashboard = True
        else:
            for k, v in access_flags.items():
                if v is not None:
                    setattr(user, k, v)

        user.save()

        # memberships
        if branches_objects:
            BranchMembership.objects.bulk_create(
                [BranchMembership(user=user, branch=b, is_primary=False) for b in branches_objects],
                ignore_conflicts=True,
            )

            # primary: если не передали — сделаем первый филиал primary
            if primary_branch_id is None:
                primary_branch_id = branches_objects[0].id

            _set_primary_branch(user, primary_branch_id)

        # почта не должна ломать создание
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
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception:
            pass

        self._generated_password = generated_password
        return user

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep["generated_password"] = getattr(self, "_generated_password", None)
        rep["branches_attached"] = [
            {"id": str(m.branch_id), "name": m.branch.name, "is_primary": m.is_primary}
            for m in instance.branch_memberships.select_related("branch").all()
        ]
        return rep


# ======================
# Employee update
# ======================

class FunnelGrantSerializer(serializers.Serializer):
    """Доступ сотрудника к воронке consalting: {funnel_id, can_manage_leads, can_manage_stages}."""
    funnel_id = serializers.UUIDField()
    can_manage_leads = serializers.BooleanField(required=False, default=False)
    can_manage_stages = serializers.BooleanField(required=False, default=False)


class EmployeeUpdateSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)
    branch_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
        help_text="Полный новый список филиалов сотрудника. Если не указано — без изменений.",
    )
    funnel_grants = FunnelGrantSerializer(
        many=True, required=False,
        help_text="Полный новый список доступов к воронкам consalting. Если не указано — без изменений.",
    )

    class Meta:
        model = User
        fields = [
            "id", "first_name", "last_name", "track_number", "phone_number", "avatar",
            "role", "custom_role", "role_display",
            *[f.name for f in User._meta.fields if f.name.startswith("can_view_")],
            "can_manage_funnel_leads",
            "can_manage_funnel_stages",
            "funnel_grants",
            "branch_ids",
        ]
        read_only_fields = ["id"]

    def validate(self, data):
        request = self.context["request"]
        current_user = request.user
        target_user = self.instance

        if getattr(current_user, "role", None) == "manager":
            raise serializers.ValidationError("Менеджеру запрещено редактировать сотрудников.")

        if current_user.id == target_user.id:
            raise serializers.ValidationError("Вы не можете редактировать самого себя через этот интерфейс.")

        if getattr(target_user, "role", None) == "owner" and not getattr(current_user, "is_superuser", False):
            if "role" in data and data["role"] != "owner":
                raise serializers.ValidationError("Вы не можете изменить роль владельца компании.")

        branch_ids = data.get("branch_ids", None)
        if branch_ids is not None:
            company = target_user.company
            _validate_branch_ids_for_company(branch_ids, company)

        return data

    @transaction.atomic
    def update(self, instance, validated_data):
        branch_ids = validated_data.pop("branch_ids", None)
        funnel_grants = validated_data.pop("funnel_grants", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if branch_ids is not None:
            branches = _validate_branch_ids_for_company(branch_ids, instance.company)
            _sync_user_branches(instance, branches)

        if funnel_grants is not None:
            self._sync_funnel_grants(instance, funnel_grants)

        return instance

    def _sync_funnel_grants(self, instance, grants):
        """Полная замена доступов сотрудника к воронкам (только воронки своей компании)."""
        from apps.consalting.models import EmployeeFunnelGrant, FunnelConsalting

        company = instance.company
        valid_funnel_ids = set(
            FunnelConsalting.objects.filter(
                company=company,
                id__in=[g["funnel_id"] for g in grants],
            ).values_list("id", flat=True)
        )
        EmployeeFunnelGrant.objects.filter(employee=instance).delete()
        EmployeeFunnelGrant.objects.bulk_create([
            EmployeeFunnelGrant(
                employee=instance,
                funnel_id=g["funnel_id"],
                can_manage_leads=bool(g.get("can_manage_leads", False)),
                can_manage_stages=bool(g.get("can_manage_stages", False)),
            )
            for g in grants
            if g["funnel_id"] in valid_funnel_ids
        ])


# ======================
# Lists / dictionaries
# ======================

class UserListSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)
    branch_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True, source="allowed_branch_ids")
    primary_branch_id = serializers.UUIDField(read_only=True, source="primary_branch.id")

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "track_number", "phone_number",
            "role", "custom_role", "role_display", "avatar",
            *[f.name for f in User._meta.fields if f.name.startswith("can_view_")],
            "branch_ids", "primary_branch_id",
        ]

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        return _apply_market_cashier_gate(rep, instance)


class UserWithPermissionsSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)
    branch_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True, source="allowed_branch_ids")
    primary_branch_id = serializers.UUIDField(read_only=True, source="primary_branch.id")

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "track_number", "phone_number",
            "role", "custom_role", "role_display", "avatar",
            *[f.name for f in User._meta.fields if f.name.startswith("can_view_")],
            "branch_ids", "primary_branch_id",
        ]

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        return _apply_market_cashier_gate(rep, instance)


class SectorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sector
        fields = ["id", "name"]


class IndustrySerializer(serializers.ModelSerializer):
    sectors = SectorSerializer(many=True, read_only=True)

    class Meta:
        model = Industry
        fields = ["id", "name", "sectors"]


class FeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feature
        fields = ["id", "name", "description"]


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    features = FeatureSerializer(many=True)

    class Meta:
        model = SubscriptionPlan
        fields = ["id", "name", "price", "description", "features"]


class CompanySerializer(serializers.ModelSerializer):
    industry = IndustrySerializer(read_only=True)
    subscription_plan = SubscriptionPlanSerializer(read_only=True)
    owner = UserListSerializer(read_only=True)
    sector = SectorSerializer(read_only=True)
    region_display = serializers.CharField(source="get_region_display", read_only=True)

    class Meta:
        model = Company
        fields = [
            "id", "name","slug",
            "industry", "sector", "region", "region_display",
            "phone", "phones_howcase", "whatsapp_phone", "subscription_plan",
            "owner",
            "created_at", "start_date", "end_date",
            "can_view_documents", "can_view_whatsapp", "can_view_instagram", "can_view_telegram", "can_view_showcase",
            "cashier_password",
            "llc", "inn", "okpo", "score", "bik", "address",
            "scale_barcode_mode",
            "scale_barcode_layout",
        ]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)
    new_password2 = serializers.CharField(required=True, write_only=True)

    def validate(self, data):
        user = self.context["request"].user

        if not user.check_password(data["current_password"]):
            raise serializers.ValidationError({"current_password": "Неверный текущий пароль."})

        if data["new_password"] != data["new_password2"]:
            raise serializers.ValidationError({"new_password2": "Пароли не совпадают."})

        if data["new_password"] == data["current_password"]:
            raise serializers.ValidationError({"new_password": "Новый пароль должен отличаться от текущего."})

        validate_password(data["new_password"], user)
        return data

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user

_OPTIONAL_TEXT = (
    "llc",
    "inn",
    "okpo",
    "score",
    "bik",
    "address",
    "phone",
    "phones_howcase",
    "whatsapp_phone",
)


class CompanyUpdateSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(required=False, max_length=SLUG_MAX)

    def validate_slug(self, value):
        value = normalize_slug(value)
        err = slug_format_error(value)
        if err:
            raise serializers.ValidationError(err)  # 400
        exclude_pk = self.instance.pk if self.instance else None
        if slug_taken_by_other(value, exclude_pk=exclude_pk):
            raise SlugConflict()  # 409
        return value

    class Meta:
        model = Company
        fields = [
            "name",
            "slug",
            "llc",
            "inn",
            "okpo",
            "score",
            "bik",
            "address",
            "phone",
            "phones_howcase",
            "whatsapp_phone",
            "industry",
            "sector",
            "region",
            "scale_barcode_mode",
            "scale_barcode_layout",
        ]

    def validate(self, attrs):
        for f in _OPTIONAL_TEXT:
            if f in attrs and (attrs[f] is None or str(attrs[f]).strip() == ""):
                attrs[f] = None

        if "name" in attrs and len(attrs["name"].strip()) < 2:
            raise serializers.ValidationError({"name": "Название компании слишком короткое."})
        return attrs


class CustomRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomRole
        fields = ["id", "name", "company"]
        read_only_fields = ["id", "company"]


class CompanySubscriptionSerializer(serializers.ModelSerializer):
    extend_months = serializers.IntegerField(
        write_only=True,
        required=False,
        min_value=1,
        help_text="На сколько месяцев продлить подписку (для кнопки 'Продлить на месяц' = 1).",
    )

    class Meta:
        model = Company
        fields = ["start_date", "end_date", "extend_months"]

    def update(self, instance, validated_data):
        extend_months = validated_data.pop("extend_months", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if extend_months:
            now = timezone.now()
            base_date = instance.end_date or now
            if base_date < now:
                base_date = now
            instance.end_date = base_date + timedelta(days=30 * extend_months)

        instance.save()
        return instance
