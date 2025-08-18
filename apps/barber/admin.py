# barber_crm/admin.py
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    BarberProfile, Service, Client, Appointment,
    Folder, Document,   # уберите/оставьте в зависимости от наличия в приложении
)


# ===== Общий миксин для company-скоупа в админке =====
class CompanyScopedAdmin(admin.ModelAdmin):
    """
    Показывает в админке только объекты своей компании (для не-суперпользователей),
    на сохранении проставляет company из пользователя и ограничивает выпадающие списки.
    """

    # динамически добавляем created_at/updated_at в readonly, если они есть в модели
    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        fields = {f.name for f in self.model._meta.fields}
        for name in ("created_at", "updated_at"):
            if name in fields and name not in ro:
                ro.append(name)
        return ro

    def _get_company(self, request):
        return getattr(request.user, "company", None) or getattr(request.user, "owned_company", None)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        company = self._get_company(request)
        return qs.filter(company=company) if company else qs.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Ограничиваем FK списки по компании (client/barber/service/folder/company/parent).
        """
        company = self._get_company(request)
        if not request.user.is_superuser and company:
            name = db_field.name
            if name == "company":
                # оставляем выбор только своей компании (или вовсе скрывайте поле в форме)
                kwargs["queryset"] = db_field.remote_field.model.objects.filter(id=company.id)
            elif name in {"client", "barber", "service", "folder", "parent"}:
                model = db_field.remote_field.model
                if hasattr(model, "company_id") or any(f.name == "company" for f in model._meta.fields):
                    kwargs["queryset"] = model.objects.filter(company=company)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not obj.pk and not getattr(obj, "company_id", None):
            company = self._get_company(request)
            if company:
                obj.company = company
        super().save_model(request, obj, form, change)

    def get_list_filter(self, request):
        """
        Добавляем фильтр по компании только суперпользователям.
        """
        base = list(getattr(self, "list_filter", ()))
        if request.user.is_superuser and "company" not in base and any(f.name == "company" for f in self.model._meta.fields):
            base.append("company")
        return base


# ===== Barber =====
@admin.register(BarberProfile)
class BarberProfileAdmin(CompanyScopedAdmin):
    list_display = ("full_name", "phone", "is_active", "company", "created_at")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone", "extra_phone")
    ordering = ("full_name",)


# ===== Service =====
@admin.register(Service)
class ServiceAdmin(CompanyScopedAdmin):
    list_display = ("name", "price", "is_active", "company")
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("name",)


# ===== Client =====
@admin.register(Client)
class ClientAdmin(CompanyScopedAdmin):
    list_display = ("full_name", "phone", "status", "company", "created_at")
    list_filter = ("status",)
    search_fields = ("full_name", "phone", "email")
    ordering = ("full_name",)


# ===== Appointment =====
@admin.register(Appointment)
class AppointmentAdmin(CompanyScopedAdmin):
    list_display = ("client", "barber", "service", "start_at", "end_at", "status", "company")
    list_filter = ("status", "barber")
    search_fields = (
        "client__full_name", "client__phone",
        "barber__full_name",
        "service__name", "comment",
    )
    list_select_related = ("client", "barber", "service", "company")
    autocomplete_fields = ("client", "barber", "service")


# ===== Folder =====
@admin.register(Folder)
class FolderAdmin(CompanyScopedAdmin):
    list_display = ("name", "parent", "company")
    search_fields = ("name",)
    list_filter = ("parent",)
    autocomplete_fields = ("parent",)
    ordering = ("name",)


# ===== Document =====
@admin.register(Document)
class DocumentAdmin(CompanyScopedAdmin):
    list_display = ("name", "folder", "file_link", "company", "created_at")
    search_fields = ("name", "file")
    list_filter = ("folder",)
    autocomplete_fields = ("folder",)

    def file_link(self, obj):
        if getattr(obj, "file", None) and getattr(obj.file, "url", None):
            return format_html('<a href="{}" target="_blank">Открыть</a>', obj.file.url)
        return "—"
    file_link.short_description = "Файл"
