# barber_crm/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Q

from .models import (
    BarberProfile, Service, Client, Appointment,
    Folder, Document,
)


# ===== Общий миксин для company/branch-скоупа в админке =====
class CompanyScopedAdmin(admin.ModelAdmin):
    """
    Показывает в админке только объекты своей компании (для не-суперпользователей),
    а для моделей с полем `branch` также учитывает активный филиал из request.branch:
      - если активный филиал задан → branch == active_branch ИЛИ branch IS NULL;
      - если активный филиал не задан → только branch IS NULL.
    На сохранении проставляет company/branch из пользователя и запроса, и
    ограничивает выпадающие списки по компании/филиалу.
    """

    # динамически добавляем created_at/updated_at в readonly, если они есть в модели
    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        fields = {f.name for f in self.model._meta.fields}
        for name in ("created_at", "updated_at"):
            if name in fields and name not in ro:
                ro.append(name)
        return ro

    # --- helpers ---
    def _get_company(self, request):
        return getattr(request.user, "company", None) or getattr(request.user, "owned_company", None)

    def _get_active_branch(self, request):
        return getattr(request, "branch", None)

    def _model_has_field(self, field_name: str) -> bool:
        return field_name in {f.name for f in self.model._meta.get_fields()}

    # --- queryset scoping ---
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        company = self._get_company(request)
        if not company:
            return qs.none()

        qs = qs.filter(company=company)

        if self._model_has_field("branch"):
            active_branch = self._get_active_branch(request)
            if active_branch:
                qs = qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
            else:
                qs = qs.filter(branch__isnull=True)
        return qs

    # --- FK choices scoping ---
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Ограничиваем FK списки по компании (client/barber/service/folder/parent/…),
        а если у целевой модели есть `branch` — ещё и по активному филиалу (или глобальным).
        """
        company = self._get_company(request)
        active_branch = self._get_active_branch(request)

        if not request.user.is_superuser and company:
            name = db_field.name
            model = db_field.remote_field.model

            # Ограничиваем собственно поле company (если есть)
            if name == "company":
                kwargs["queryset"] = model.objects.filter(id=getattr(company, "id", None))

            # Общий случай: для ссылок на сущности компании
            elif any(f.name == "company" for f in model._meta.fields):
                base_qs = model.objects.filter(company=company)

                # Если у целевой модели есть branch — прикручиваем филиальный скоуп
                if any(f.name == "branch" for f in model._meta.get_fields()):
                    if active_branch:
                        base_qs = base_qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))
                    else:
                        base_qs = base_qs.filter(branch__isnull=True)

                # Частный случай — parent (должен быть той же компании/филиала)
                if name == "parent" and active_branch:
                    base_qs = base_qs.filter(Q(branch=active_branch) | Q(branch__isnull=True))

                kwargs["queryset"] = base_qs

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # --- save scoping ---
    def save_model(self, request, obj, form, change):
        if not getattr(obj, "company_id", None):
            company = self._get_company(request)
            if company:
                obj.company = company

        # Если у модели есть branch — подставляем активный филиал (может быть None → глобальный)
        if self._model_has_field("branch") and not getattr(obj, "branch_id", None):
            obj.branch = self._get_active_branch(request)

        super().save_model(request, obj, form, change)

    # --- filters for superuser ---
    def get_list_filter(self, request):
        """
        Добавляем фильтры по company/branch только суперпользователям.
        """
        base = list(getattr(self, "list_filter", ()))
        fields = {f.name for f in self.model._meta.fields}

        if request.user.is_superuser:
            if "company" not in base and "company" in fields:
                base.append("company")
            if "branch" not in base and "branch" in fields:
                base.append("branch")
        return base


# ===== Barber =====
@admin.register(BarberProfile)
class BarberProfileAdmin(CompanyScopedAdmin):
    list_display = ("full_name", "phone", "is_active", "branch", "company", "created_at")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone", "extra_phone")
    ordering = ("full_name",)
    autocomplete_fields = ()  # при необходимости


# ===== Service =====
@admin.register(Service)
class ServiceAdmin(CompanyScopedAdmin):
    list_display = ("name", "price", "is_active", "branch", "company")
    list_filter = ("is_active",)  # branch/company добавятся для суперюзера автоматически
    search_fields = ("name",)
    ordering = ("name",)


# ===== Client =====
@admin.register(Client)
class ClientAdmin(CompanyScopedAdmin):
    list_display = ("full_name", "phone", "status", "branch", "company", "created_at")
    list_filter = ("status",)
    search_fields = ("full_name", "phone", "email")
    ordering = ("full_name",)


# ===== Appointment =====
@admin.register(Appointment)
class AppointmentAdmin(CompanyScopedAdmin):
    list_display = ("client", "barber", "service", "start_at", "end_at", "status", "branch", "company")
    list_filter = ("status", "barber")  # branch/company для суперюзера добавятся из миксина
    search_fields = (
        "client__full_name", "client__phone",
        "barber__first_name", "barber__last_name", "barber__email",
        "service__name", "comment",
    )
    list_select_related = ("client", "barber", "service", "company", "branch")
    autocomplete_fields = ("client", "barber", "service")


# ===== Folder =====
@admin.register(Folder)
class FolderAdmin(CompanyScopedAdmin):
    list_display = ("name", "parent", "branch", "company")
    search_fields = ("name",)
    list_filter = ("parent",)  # branch/company добавятся для суперюзера
    autocomplete_fields = ("parent",)
    ordering = ("name",)


# ===== Document =====
@admin.register(Document)
class DocumentAdmin(CompanyScopedAdmin):
    list_display = ("name", "folder", "file_link", "branch", "company", "created_at")
    search_fields = ("name", "file")
    list_filter = ("folder",)  # branch/company добавятся для суперюзера
    autocomplete_fields = ("folder",)

    def file_link(self, obj):
        if getattr(obj, "file", None) and getattr(obj.file, "url", None):
            return format_html('<a href="{}" target="_blank">Открыть</a>', obj.file.url)
        return "—"
    file_link.short_description = "Файл"
