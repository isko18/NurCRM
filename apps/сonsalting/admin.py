# admin.py
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import PermissionDenied

from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
)
from apps.users.models import Company, User


def get_company_from_user(user):
    """
    Helper — как и в вьюхах/сериализаторах: извлечь компанию из user.
    Подстрой под вашу модель User, если связь хранится в другом месте.
    """
    if user is None or user.is_anonymous:
        return None
    company = getattr(user, "company", None)
    if company is None:
        profile = getattr(user, "profile", None)
        if profile is not None:
            company = getattr(profile, "company", None)
    return company


class TimeStampedAdminMixin:
    readonly_fields = ("created_at", "updated_at")


class CompanyScopedAdminMixin:
    """
    Миксин для админ-классов:
    - Ограничивает queryset только записями компании пользователя (если не суперюзер)
    - При сохранении автоматически устанавливает company (если отсутствует)
    - Ограничивает варианты ForeignKey полей к объектам своей компании там, где логично
    """

    company_field_name = "company"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        company = get_company_from_user(request.user)
        if not company:
            # Если пользователь настроен без компании, не показываем ничего
            return qs.none()
        return qs.filter(**{self.company_field_name: company})

    def save_model(self, request, obj, form, change):
        """
        При создании/обновлении — если объект не относится к компании и пользователь не супер,
        привязать компанию к текущей.
        """
        if not request.user.is_superuser:
            company = get_company_from_user(request.user)
            if not company:
                raise PermissionDenied("У пользователя не настроена компания.")
            # Если модель уже имеет поле company и оно пустое или относится не к этой компании —
            # принудительно установим текущую компанию пользователя
            if hasattr(obj, self.company_field_name):
                setattr(obj, self.company_field_name, company)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Ограничиваем выборку ForeignKey полей (услуги, клиенты и т.д.) только объектами своей компании.
        Подправляйте названия моделей/полей по необходимости.
        """
        # если суперюзер — показываем всё
        if request.user.is_superuser:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        company = get_company_from_user(request.user)
        if company is None:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        # Примеры кастомизации: для поля services/ client/ company
        if db_field.name == "company":
            # не даём выбрать чужую компанию — показываем только свою компанию
            kwargs["queryset"] = Company.objects.filter(pk=company.pk)
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        # Если поле связано с моделью ServicesConsalting — фильтруем по компании
        if db_field.related_model is ServicesConsalting:
            kwargs["queryset"] = ServicesConsalting.objects.filter(company=company)
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        # Если поле связано с моделью User — показываем только пользователей той же компании (если у вас так устроено)
        if db_field.related_model is User:
            # Попробуйте адаптировать логику под ваши User—Company связи
            kwargs["queryset"] = User.objects.filter(company=company)
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ServicesConsalting)
class ServicesConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "price", "created_at", "updated_at")
    list_filter = ("company",)
    search_fields = ("name", "description")
    readonly_fields = TimeStampedAdminMixin.readonly_fields
    ordering = ("name",)
    # Автодополнение/быстрый выбор — можно включить, если настроен autocomplete_fields
    raw_id_fields = ("company",)
    # ограничиваем видимость company в форме: суперюзер может выбрать, остальные — только свою (см. formfield_for_foreignkey)
    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            # запретим изменять поле company для обычных пользователей
            ro.append("company")
        return ro


@admin.register(SaleConsalting)
class SaleConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("services", "company", "user", "client", "short_description", "created_at")
    list_filter = ("company", "services")
    search_fields = ("description",)
    readonly_fields = TimeStampedAdminMixin.readonly_fields
    raw_id_fields = ("company", "services", "client", "user")
    ordering = ("-created_at",)

    def short_description(self, obj):
        return (obj.description[:60] + "...") if obj.description and len(obj.description) > 60 else obj.description
    short_description.short_description = _("Заметка")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
            # можно также сделать user readonly (чтобы не менять создателя)
            ro.append("user")
        return ro


@admin.register(SalaryConsalting)
class SalaryConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("company", "user", "amount", "created_at")
    list_filter = ("company", "user")
    search_fields = ("description",)
    readonly_fields = TimeStampedAdminMixin.readonly_fields
    raw_id_fields = ("company", "user")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
            ro.append("user")
        return ro


@admin.register(RequestsConsalting)
class RequestsConsaltingAdmin(CompanyScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "client", "status", "created_at")
    list_filter = ("company", "status")
    search_fields = ("name", "description")
    readonly_fields = TimeStampedAdminMixin.readonly_fields
    raw_id_fields = ("company", "client")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
        return ro
