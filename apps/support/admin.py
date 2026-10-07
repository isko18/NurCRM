from django.contrib import admin

from apps.support.models import (
    SupportAlertRule,
    SupportBotConfig,
    SupportErrorReport,
    SupportIssue,
    SupportReportAttachment,
)
from apps.support.rules import invalidate_rules_cache


@admin.register(SupportIssue)
class SupportIssueAdmin(admin.ModelAdmin):
    list_display = ("id", "category", "severity", "status", "title", "companies_count", "occurrences", "last_seen")
    list_filter = ("severity", "status", "category")
    search_fields = ("title", "fingerprint")
    readonly_fields = ("fingerprint", "first_seen", "last_seen", "occurrences", "companies_count", "versions")


@admin.register(SupportErrorReport)
class SupportErrorReportAdmin(admin.ModelAdmin):
    list_display = ("client_report_id", "company", "app", "version", "level", "category", "count", "last_at")
    list_filter = ("level", "category", "app")
    search_fields = ("client_report_id", "fingerprint", "device_id", "login")
    raw_id_fields = ("company", "issue")


@admin.register(SupportReportAttachment)
class SupportReportAttachmentAdmin(admin.ModelAdmin):
    list_display = ("client_report_id", "company", "size", "created_at")
    search_fields = ("client_report_id", "device_id")
    raw_id_fields = ("company",)


@admin.register(SupportAlertRule)
class SupportAlertRuleAdmin(admin.ModelAdmin):
    list_display = ("position", "code", "name", "category", "min_level", "scope", "threshold",
                    "window_minutes", "severity", "enabled")
    list_editable = ("enabled",)
    ordering = ("position", "id")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_rules_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        invalidate_rules_cache()


@admin.register(SupportBotConfig)
class SupportBotConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "chat_id", "enabled", "hourly_msg_limit")
