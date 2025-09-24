from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _
from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting
)
from apps.users.models import Company, User


def get_company_from_user(user):
    """Извлекает компанию из пользователя безопасно."""
    if not user or user.is_anonymous:
        return None
    company = getattr(user, "company", None)
    if company is None:
        profile = getattr(user, "profile", None)
        if profile:
            company = getattr(profile, "company", None)
    return company


class TimeStampedAdminMixin:
    readonly_fields = ("created_at", "updated_at")


class CompanyScopedAdminMixin:
    company_field_name = "company"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        company = get_company_from_user(request.user)
        return qs.filter(**{self.company_field_name: company}) if company else qs.none()

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            company = get_company_from_user(request.user)
            if not company:
                raise PermissionDenied("У пользователя не настроена компания.")
            if hasattr(obj, self.company_field_name):
                setattr(obj, self.company_field_name, company)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if request.user.is_superuser:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)
        company = get_company_from_user(request.user)
        if company is None:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        if db_field.name == "company":
            kwargs["queryset"] = Company.objects.filter(pk=company.pk)
        elif db_field.related_model is ServicesConsalting:
            kwargs["queryset"] = ServicesConsalting.objects.filter(company=company)
        elif db_field.related_model is User:
            kwargs["queryset"] = User.objects.filter(company=company)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ServicesConsalting)
class ServicesConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "price", "created_at", "updated_at")
    list_filter = ("company",)
    search_fields = ("name", "description")
    raw_id_fields = ("company",)
    ordering = ("name",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
        return ro


@admin.register(SaleConsalting)
class SaleConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("services", "company", "user", "client", "short_description", "created_at")
    list_filter = ("company", "services")
    search_fields = ("description",)
    raw_id_fields = ("company", "services", "client", "user")
    ordering = ("-created_at",)

    def short_description(self, obj):
        return (obj.description[:60] + "...") if obj.description and len(obj.description) > 60 else obj.description
    short_description.short_description = _("Заметка")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "user"])
        return ro


@admin.register(SalaryConsalting)
class SalaryConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("company", "user", "amount", "percent", "created_at")
    list_filter = ("company", "user")
    search_fields = ("description",)
    raw_id_fields = ("company", "user")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "user"])
        return ro


@admin.register(RequestsConsalting)
class RequestsConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "client", "status", "created_at")
    list_filter = ("company", "status")
    search_fields = ("name", "description")
    raw_id_fields = ("company", "client")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
        return ro


@admin.register(BookingConsalting)
class BookingConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("title", "company", "employee", "date", "time", "created_at")
    list_filter = ("company", "date", "employee")
    search_fields = ("title", "note")
    raw_id_fields = ("company", "employee")
    ordering = ("-date", "time")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
        return ro
