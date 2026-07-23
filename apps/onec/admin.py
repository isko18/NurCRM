from django.contrib import admin

from .models import OneCIntegration, OneCSyncRecord


@admin.register(OneCIntegration)
class OneCIntegrationAdmin(admin.ModelAdmin):
    list_display = ("company", "is_enabled", "auth_type", "currency", "base_url", "updated_at")
    list_filter = ("is_enabled", "auth_type", "currency")
    search_fields = ("company__name", "base_url", "login")
    readonly_fields = ("created_at", "updated_at", "last_pull_at")
    exclude = ("password_cipher", "inbound_secret_cipher")


@admin.register(OneCSyncRecord)
class OneCSyncRecordAdmin(admin.ModelAdmin):
    list_display = (
        "source_type", "source_id", "operation", "status",
        "attempts", "onec_number", "company", "created_at",
    )
    list_filter = ("status", "direction", "source_type", "operation")
    search_fields = ("source_id", "idempotency_key", "onec_external_id", "onec_number")
    readonly_fields = (
        "idempotency_key", "request_payload", "response_payload",
        "created_at", "updated_at", "onec_posted_at",
    )
    actions = ["retry_selected"]

    @admin.action(description="Повторить выгрузку в 1С")
    def retry_selected(self, request, queryset):
        from .services import _dispatch

        count = 0
        for rec in queryset.exclude(status__in=[OneCSyncRecord.Status.SENT, OneCSyncRecord.Status.POSTED]):
            rec.status = OneCSyncRecord.Status.PENDING
            rec.last_error = ""
            rec.save(update_fields=["status", "last_error", "updated_at"])
            _dispatch(rec.id)
            count += 1
        self.message_user(request, f"Пере-поставлено в очередь: {count}")
