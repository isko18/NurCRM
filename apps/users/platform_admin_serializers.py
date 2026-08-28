import secrets
import string
import uuid
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import Company, Sector, Industry, SubscriptionPlan, Branch, CustomRole, User, BranchMembership, Roles
from .serializers import (
    normalize_slug,
    slug_format_error,
    slug_taken_by_other,
    SLUG_MAX,
)


class PlatformAdminCompanyListSerializer(serializers.ModelSerializer):
    sector = serializers.SerializerMethodField()
    sector_name = serializers.CharField(source="sector.name", read_only=True, default=None)
    subscription_plan = serializers.SerializerMethodField()
    subscription_plan_name = serializers.CharField(source="subscription_plan.name", read_only=True, default=None)
    end_date = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "slug",
            "inn",
            "is_active",
            "end_date",
            "sector",
            "sector_name",
            "subscription_plan",
            "subscription_plan_name",
            "created_at",
        ]

    def get_sector(self, obj):
        if not obj.sector:
            return None
        return {"id": str(obj.sector.id), "name": obj.sector.name}

    def get_subscription_plan(self, obj):
        if not obj.subscription_plan:
            return None
        return {"id": str(obj.subscription_plan.id), "name": obj.subscription_plan.name}

    def get_end_date(self, obj):
        if not obj.end_date:
            return None
        return obj.end_date.strftime("%Y-%m-%d")


class PlatformAdminCompanyDetailSerializer(serializers.ModelSerializer):
    sector = serializers.SerializerMethodField()
    sector_id = serializers.SerializerMethodField()
    sector_name = serializers.CharField(source="sector.name", read_only=True, default=None)
    subscription_plan = serializers.SerializerMethodField()
    subscription_plan_id = serializers.SerializerMethodField()
    subscription_plan_name = serializers.CharField(source="subscription_plan.name", read_only=True, default=None)
    branches = serializers.SerializerMethodField()
    custom_roles = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            "id",
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
            "is_active",
            "start_date",
            "end_date",
            "support_note",
            "sector",
            "sector_id",
            "sector_name",
            "subscription_plan",
            "subscription_plan_id",
            "subscription_plan_name",
            "branches",
            "custom_roles",
        ]

    def get_sector(self, obj):
        if not obj.sector:
            return None
        return {"id": str(obj.sector.id), "name": obj.sector.name}

    def get_sector_id(self, obj):
        return str(obj.sector.id) if obj.sector else None

    def get_subscription_plan(self, obj):
        if not obj.subscription_plan:
            return None
        return {"id": str(obj.subscription_plan.id), "name": obj.subscription_plan.name}

    def get_subscription_plan_id(self, obj):
        return str(obj.subscription_plan.id) if obj.subscription_plan else None

    def get_branches(self, obj):
        return [
            {
                "id": str(b.id),
                "name": b.name,
                "code": b.code,
                "is_active": b.is_active,
            }
            for b in obj.branches.all()
        ]

    def get_custom_roles(self, obj):
        return [
            {
                "id": str(r.id),
                "name": r.name,
            }
            for r in obj.custom_roles.all()
        ]

    def get_end_date(self, obj):
        if not obj.end_date:
            return None
        return obj.end_date.strftime("%Y-%m-%d")


class PlatformAdminCompanyUpdateSerializer(serializers.ModelSerializer):
    name = serializers.CharField(required=False, min_length=2, max_length=255)
    slug = serializers.CharField(required=False, max_length=SLUG_MAX)
    sector_id = serializers.PrimaryKeyRelatedField(
        queryset=Sector.objects.all(),
        source="sector",
        required=False,
        allow_null=True,
        error_messages={"does_not_exist": "Указанная отрасль не найдена."},
    )
    industry_id = serializers.PrimaryKeyRelatedField(
        queryset=Industry.objects.all(),
        source="industry",
        required=False,
        allow_null=True,
        error_messages={"does_not_exist": "Указанный вид деятельности не найден."},
    )

    class Meta:
        model = Company
        fields = [
            "name",
            "llc",
            "inn",
            "okpo",
            "score",
            "bik",
            "address",
            "phone",
            "phones_howcase",
            "whatsapp_phone",
            "slug",
            "sector_id",
            "industry_id",
            "is_active",
            "support_note",
            "region",
        ]

    def validate_name(self, value):
        val = (value or "").strip()
        if len(val) < 2:
            raise serializers.ValidationError("Название компании слишком короткое.")
        return val

    def validate_slug(self, value):
        value = normalize_slug(value)
        err = slug_format_error(value)
        if err:
            raise serializers.ValidationError([err])
        exclude_pk = self.instance.pk if self.instance else None
        if slug_taken_by_other(value, exclude_pk=exclude_pk):
            raise serializers.ValidationError(["Такой slug уже занят"])
        return value

    def validate(self, attrs):
        optional_fields = (
            "llc",
            "inn",
            "okpo",
            "score",
            "bik",
            "address",
            "phone",
            "phones_howcase",
            "whatsapp_phone",
            "support_note",
        )
        for field in optional_fields:
            if field in attrs and attrs[field] is not None and str(attrs[field]).strip() == "":
                attrs[field] = None
        return attrs


class PlatformAdminCompanySubscriptionUpdateSerializer(serializers.Serializer):
    subscription_plan_id = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.all(),
        source="subscription_plan",
        required=False,
        allow_null=True,
        error_messages={"does_not_exist": "Указанный тарифный план не найден."},
    )
    end_date = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    support_note = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_end_date(self, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        if isinstance(value, str):
            value = value.strip()
            try:
                dt = timezone.datetime.strptime(value, "%Y-%m-%d")
                bishkek_tz = ZoneInfo("Asia/Bishkek")
                return dt.replace(hour=23, minute=59, second=59, tzinfo=bishkek_tz)
            except ValueError:
                raise serializers.ValidationError("Некорректный формат даты. Используйте YYYY-MM-DD.")
        return value

    def update(self, instance, validated_data):
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.save()
        return instance


def generate_user_password(length=10):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    pwd = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ] + [secrets.choice(chars) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(pwd)
    return "".join(pwd)


class PlatformAdminUserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(read_only=True)
    custom_role_name = serializers.CharField(source="custom_role.name", read_only=True, default=None)
    company_id = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()
    branches = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "avatar",
            "track_number",
            "role",
            "custom_role",
            "role_display",
            "custom_role_name",
            "is_active",
            "is_platform_admin",
            "company_id",
            "company",
            "branches",
            "created_at",
            "updated_at",
            *[f.name for f in User._meta.fields if f.name.startswith("can_view_") or f.name.startswith("can_manage_")],
        ]

    def get_company_id(self, obj):
        return str(obj.company_id) if obj.company_id else None

    def get_company(self, obj):
        if not obj.company:
            return None
        return {"id": str(obj.company.id), "name": obj.company.name}

    def get_branches(self, obj):
        return [str(m.branch_id) for m in obj.branch_memberships.all()]


class PlatformAdminUserCreateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    role = serializers.ChoiceField(choices=Roles.choices, required=False, allow_null=True, allow_blank=True)
    custom_role = serializers.PrimaryKeyRelatedField(
        queryset=CustomRole.objects.all(),
        required=False,
        allow_null=True,
    )
    branches = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta:
        model = User
        fields = [
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "avatar",
            "track_number",
            "role",
            "custom_role",
            "is_active",
            "branches",
            *[f.name for f in User._meta.fields if f.name.startswith("can_view_") or f.name.startswith("can_manage_")],
        ]

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if User.objects.filter(email=value, is_active=True).exists():
            raise serializers.ValidationError(["Пользователь с таким email уже существует"])
        return value

    def validate(self, attrs):
        company = self.context.get("company")
        custom_role = attrs.get("custom_role")
        if custom_role and custom_role.company and company and custom_role.company != company:
            raise serializers.ValidationError({"custom_role": ["Указанная роль не принадлежит этой компании."]})

        branch_ids = attrs.get("branches")
        if branch_ids and company:
            valid_ids = set(
                str(bid) for bid in Branch.objects.filter(company=company).values_list("id", flat=True)
            )
            for b_id in branch_ids:
                if str(b_id) not in valid_ids:
                    raise serializers.ValidationError({"branches": [f"Филиал {b_id} не найден в компании."]})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        company = self.context.get("company")
        branch_ids = validated_data.pop("branches", None)
        raw_password = generate_user_password()

        user = User(company=company, **validated_data)
        user.set_password(raw_password)
        user.save()

        # If role is owner, and company has no owner, set as owner
        if user.role == Roles.OWNER and company and not getattr(company, "owner", None):
            company.owner = user
            company.save(update_fields=["owner"])

        # Sync branches
        if branch_ids:
            memberships = [
                BranchMembership(user=user, branch_id=b_id)
                for b_id in branch_ids
            ]
            BranchMembership.objects.bulk_create(memberships)

        user.generated_password = raw_password
        return user


class PlatformAdminUserUpdateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=False)
    role = serializers.ChoiceField(choices=Roles.choices, required=False, allow_null=True, allow_blank=True)
    custom_role = serializers.PrimaryKeyRelatedField(
        queryset=CustomRole.objects.all(),
        required=False,
        allow_null=True,
    )
    branches = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta:
        model = User
        fields = [
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "avatar",
            "track_number",
            "role",
            "custom_role",
            "is_active",
            "branches",
            *[f.name for f in User._meta.fields if f.name.startswith("can_view_") or f.name.startswith("can_manage_")],
        ]

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if self.instance and self.instance.email and self.instance.email.lower() == value:
            return value
        if User.objects.filter(email=value, is_active=True).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError(["Пользователь с таким email уже существует"])
        return value

    def validate(self, attrs):
        user = self.instance
        company = user.company if user else None

        custom_role = attrs.get("custom_role")
        if custom_role and custom_role.company and company and custom_role.company != company:
            raise serializers.ValidationError({"custom_role": ["Указанная роль не принадлежит этой компании."]})

        branch_ids = attrs.get("branches")
        if branch_ids and company:
            valid_ids = set(
                str(bid) for bid in Branch.objects.filter(company=company).values_list("id", flat=True)
            )
            for b_id in branch_ids:
                if str(b_id) not in valid_ids:
                    raise serializers.ValidationError({"branches": [f"Филиал {b_id} не найден в компании."]})

        # Check last owner constraint
        if user and company and user.role == Roles.OWNER:
            new_role = attrs.get("role", user.role)
            new_is_active = attrs.get("is_active", user.is_active)
            if (new_role != Roles.OWNER or new_is_active is False):
                other_owners_exist = User.objects.filter(
                    company=company,
                    role=Roles.OWNER,
                    is_active=True,
                    deleted_at__isnull=True,
                ).exclude(pk=user.pk).exists()
                if not other_owners_exist:
                    raise serializers.ValidationError({"detail": "Нельзя снять единственного владельца компании."})

        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        branch_ids = validated_data.pop("branches", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if branch_ids is not None:
            instance.branch_memberships.all().delete()
            memberships = [
                BranchMembership(user=instance, branch_id=b_id)
                for b_id in branch_ids
            ]
            BranchMembership.objects.bulk_create(memberships)

        return instance
