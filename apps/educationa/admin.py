from django.contrib import admin
from .models import Lead, Course, Teacher, Group, Student, Lesson, Folder, Document


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "source", "company", "created_at")
    search_fields = ("name", "phone")
    list_filter = ("source", "company")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "price_per_month", "company")
    search_fields = ("title",)
    list_filter = ("company",)


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "subject", "phone", "company")
    search_fields = ("name", "subject")
    list_filter = ("company",)


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "course", "teacher", "company")
    search_fields = ("name",)
    list_filter = ("course", "teacher", "company")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "status", "group", "company", "created_at")
    search_fields = ("name", "phone")
    list_filter = ("status", "company")


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "teacher", "date", "time", "duration", "company")
    list_filter = ("date", "teacher", "company")


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "parent", "company")
    search_fields = ("name",)
    list_filter = ("company",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "folder", "company", "created_at")
    search_fields = ("name", "file")
    list_filter = ("company", "folder")
