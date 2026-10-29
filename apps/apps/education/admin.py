from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Lead, Course, Group, Student, Lesson,
    Folder, Document, TeacherRate
)


# ===== Базовый миксин для company + branch =====
class CompanyBranchScopedAdmin(admin.ModelAdmin):
    """
    - Ограничивает queryset по company пользователя (если не суперюзер)
    - Показывает list_filter по company/branch, если поля есть в модели
    - Делает company/branch только для чтения
    - Ограничивает ForeignKey-поля по company
    """
    readonly_fields = ()
    list_filter = ()
    search_fields = ()

    # динамически добавим фильтры company/branch, если поля существуют
    def _has_field(self, model, name: str) -> bool:
        try:
            model._meta.get_field(name)
            return True
        except Exception:
            return False

    def get_list_display(self, request):
        base = list(super().get_list_display(request))
        # показать branch, если есть
        if self._has_field(self.model, "branch") and "branch" not in base:
            base.insert(1, "branch")  # рядом с первым полем
        # company показываем всем; если не нужно — убери блок ниже
        if self._has_field(self.model, "company") and "company" not in base:
            base.insert(1, "company")
        return tuple(base)

    def get_list_filter(self, request):
        base = list(super().get_list_filter(request))
        if self._has_field(self.model, "company") and "company" not in base:
            base.append("company")
        if self._has_field(self.model, "branch") and "branch" not in base:
            base.append("branch")
        return tuple(base)

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        if self._has_field(self.model, "company") and "company" not in base:
            base.append("company")
        if self._has_field(self.model, "branch") and "branch" not in base:
            base.append("branch")
        return tuple(base)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        if user.is_superuser:
            return qs
        company = getattr(user, "company", None) or getattr(user, "owned_company", None)
        if company and self._has_field(self.model, "company"):
            return qs.filter(company=company)
        return qs.none()

    def save_model(self, request, obj, form, change):
        # проставляем company, если пусто
        company = getattr(request.user, "company", None) or getattr(request.user, "owned_company", None)
        if company and self._has_field(self.model, "company") and not getattr(obj, "company_id", None):
            obj.company = company
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Ограничиваем FK по компании пользователя.
        Филиал не режем тут (оставляем на model.clean), чтобы не «терять» глобальные записи.
        """
        user = request.user
        if not user.is_superuser:
            company = getattr(user, "company", None) or getattr(user, "owned_company", None)
            if company:
                rel_model = db_field.remote_field.model
                try:
                    rel_model._meta.get_field("company")
                    kwargs["queryset"] = rel_model.objects.filter(company=company)
                except Exception:
                    # у связанной модели нет company — не трогаем queryset
                    pass
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ===== TeacherRate =====
@admin.register(TeacherRate)
class TeacherRateAdmin(CompanyBranchScopedAdmin):
    list_display = ("teacher", "company", "branch", "period", "mode", "rate", "updated_at")
    list_filter = ("company", "branch", "mode", "period")
    search_fields = ("teacher__first_name", "teacher__last_name", "teacher__email")
    autocomplete_fields = ("teacher",)
    date_hierarchy = "updated_at"


# ===== Lead =====
@admin.register(Lead)
class LeadAdmin(CompanyBranchScopedAdmin):
    list_display = ("name", "company", "branch", "phone", "source", "created_at")
    list_filter = ("company", "branch", "source", "created_at")
    search_fields = ("name", "phone", "note")


# ===== Course =====
@admin.register(Course)
class CourseAdmin(CompanyBranchScopedAdmin):
    list_display = ("title", "company", "branch", "price_per_month")
    list_filter = ("company", "branch")
    search_fields = ("title",)


# ===== Group =====
@admin.register(Group)
class GroupAdmin(CompanyBranchScopedAdmin):
    list_display = ("name", "company", "branch", "course")
    list_filter = ("company", "branch", "course")
    search_fields = ("name", "course__title")
    list_select_related = ("course",)
    autocomplete_fields = ("course",)


# ===== Student =====
@admin.register(Student)
class StudentAdmin(CompanyBranchScopedAdmin):
    list_display = ("name", "company", "branch", "status", "group", "phone", "discount", "created_at", "active")
    list_filter = ("company", "branch", "status", "group", "active", "created_at")
    search_fields = ("name", "phone", "note")
    list_select_related = ("group",)
    autocomplete_fields = ("group",)


# ===== Lesson =====
@admin.register(Lesson)
class LessonAdmin(CompanyBranchScopedAdmin):
    list_display = ("group", "company", "branch", "teacher", "date", "time", "duration", "classroom")
    list_filter = ("company", "branch", "date", "group", "teacher")
    search_fields = ("group__name", "teacher__first_name", "teacher__last_name", "classroom")
    list_select_related = ("group", "teacher", "course")
    autocomplete_fields = ("group", "teacher")
    date_hierarchy = "date"


# ===== Folder =====
@admin.register(Folder)
class FolderAdmin(CompanyBranchScopedAdmin):
    list_display = ("name", "company", "branch", "parent")
    list_filter = ("company", "branch", "parent")
    search_fields = ("name",)
    list_select_related = ("parent",)
    autocomplete_fields = ("parent",)


# ===== Document =====
@admin.register(Document)
class DocumentAdmin(CompanyBranchScopedAdmin):
    list_display = ("name", "company", "branch", "folder", "file_link", "created_at", "updated_at")
    list_filter = ("company", "branch", "folder", "created_at")
    search_fields = ("name", "file")
    list_select_related = ("folder",)
    autocomplete_fields = ("folder",)
    date_hierarchy = "created_at"

    @admin.display(description="Файл")
    def file_link(self, obj):
        if getattr(obj, "file", None) and getattr(obj.file, "url", None):
            return format_html('<a href="{}" target="_blank">Открыть</a>', obj.file.url)
        return "—"
