from django.contrib import admin

from apps.ekassa.models import EkassaIntegration


@admin.register(EkassaIntegration)
class EkassaIntegrationAdmin(admin.ModelAdmin):
    list_display = ("company", "is_enabled", "login_email", "fiscal_number", "updated_at")
    list_filter = ("is_enabled",)
    search_fields = ("company__name", "login_email", "fiscal_number")
    readonly_fields = ("id", "created_at", "updated_at", "password_cipher")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if obj:
            ro.append("company")
        return ro
