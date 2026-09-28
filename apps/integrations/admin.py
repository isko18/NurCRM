from django.contrib import admin

from .models import ApiKey, WebhookEndpoint


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "prefix", "scopes", "created_at", "last_used_at", "revoked_at")
    search_fields = ("name", "company__name", "prefix")
    readonly_fields = ("prefix", "key_hash", "created_at", "last_used_at")


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ("url", "company", "is_active", "last_status", "last_delivery_at")
    list_filter = ("is_active",)
    search_fields = ("url", "company__name")
    readonly_fields = ("last_delivery_at", "last_status", "last_error")
