# admin.py
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Lead, Course, Teacher, Group, Student, Lesson,
    Folder, Document,
)


class CompanyScopedAdmin(admin.ModelAdmin):
    readonly_fields = ("company",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        if user.is_superuser:
            return qs
        company = getattr(user, "company", None) or getattr(user, "owned_company", None)
        if company:
            return qs.filter(company=company)
        return qs.none()

    def save_model(self, request, obj, form, change):
        # Проставляем компанию, если у пользователя она есть и объект ещё не привязан
        company = getattr(request.user, "company", None) or getattr(request.user, "owned_company", None)
        if company and not getattr(obj, "company_id", None):
            obj.company = company
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Ограничиваем ForeignKey выбор записями своей компании, если у FK-модели есть поле company.
        """
        user = request.user
        if not user.is_superuser:
            company = getattr(user, "company", None) or getattr(user, "owned_company", None)
            if company:
                rel_model = db_field.remote_field.model
                # у модели есть поле company?
                if any(f.name == "company" for f in rel_model._meta.get_fields() if hasattr(f, "name")):
                    kwargs["queryset"] = rel_model.objects.filter(company=company)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_list_display(self, request):
        """
        Показываем колонку company только суперпользователю (удобно при поддержке).
        """
        base = list(getattr(self, "list_display", ()))
        if request.user.is_superuser:
            if "company" not in base and hasattr(self.model, "company"):
                base.append("company")
        else:
            if "company" in base:
                base.remove("company")
        return base or super().get_list_display(request)


# ===== Lead =====
@admin.register(Lead)
class LeadAdmin(CompanyScopedAdmin):
    list_display = ("name", "phone", "source", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("name", "phone", "note")
    list_select_related = ("student",)


# ===== Course =====
@admin.register(Course)
class CourseAdmin(CompanyScopedAdmin):
    list_display = ("title", "price_per_month")
    search_fields = ("title",)
    list_filter = ()


# ===== Teacher =====
@admin.register(Teacher)
class TeacherAdmin(CompanyScopedAdmin):
    list_display = ("name", "subject", "phone")
    search_fields = ("name", "subject", "phone")
    list_filter = ("subject",)


# ===== Group =====
@admin.register(Group)
class GroupAdmin(CompanyScopedAdmin):
    list_display = ("name", "course", "teacher")
    search_fields = ("name", "course__title", "teacher__name")
    list_filter = ("course",)
    list_select_related = ("course", "teacher")
    autocomplete_fields = ("course", "teacher")


# ===== Student =====
@admin.register(Student)
class StudentAdmin(CompanyScopedAdmin):
    list_display = ("name", "status", "group", "phone", "discount", "created_at")
    list_filter = ("status", "group")
    search_fields = ("name", "phone", "note")
    list_select_related = ("group",)
    autocomplete_fields = ("group",)


# ===== Lesson =====
@admin.register(Lesson)
class LessonAdmin(CompanyScopedAdmin):
    list_display = ("group", "teacher", "date", "time", "duration", "classroom")
    list_filter = ("date", "teacher", "group")
    search_fields = ("group__name", "teacher__name", "classroom")
    list_select_related = ("group", "teacher")
    autocomplete_fields = ("group", "teacher")
    date_hierarchy = "date"


# ===== Folder =====
@admin.register(Folder)
class FolderAdmin(CompanyScopedAdmin):
    list_display = ("name", "parent")
    list_filter = ("parent",)
    search_fields = ("name",)
    list_select_related = ("parent",)
    autocomplete_fields = ("parent",)


# ===== Document =====
@admin.register(Document)
class DocumentAdmin(CompanyScopedAdmin):
    list_display = ("name", "folder", "file_link", "created_at", "updated_at")
    list_filter = ("folder", "created_at")
    search_fields = ("name", "file")
    list_select_related = ("folder",)
    autocomplete_fields = ("folder",)
    date_hierarchy = "created_at"

    @admin.display(description="Файл")
    def file_link(self, obj):
        if getattr(obj, "file", None) and getattr(obj.file, "url", None):
            return format_html('<a href="{}" target="_blank">Открыть</a>', obj.file.url)
        return "—"
