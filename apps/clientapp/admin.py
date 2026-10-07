from django.contrib import admin

from .models import AppCustomer, AppShopSettings, ClientAppConfig, Referral, ReferralRule
from . import services


@admin.register(AppCustomer)
class AppCustomerAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "phone", "lang", "created_at", "deleted_at")
    search_fields = ("phone", "full_name", "referral_code")
    readonly_fields = ("phone_hash", "telegram_user_id", "created_at", "updated_at", "deleted_at")


@admin.register(AppShopSettings)
class AppShopSettingsAdmin(admin.ModelAdmin):
    list_display = ("company", "branch", "show_in_app", "hidden_by_admin", "address", "latitude", "longitude",
                    "geocode_status")
    list_filter = ("show_in_app", "hidden_by_admin", "geocode_status")
    search_fields = ("company__name", "address", "display_name")
    raw_id_fields = ("company", "branch")
    actions = ("hide_on_map", "show_on_map")

    @admin.action(description="Скрыть с карты приложения")
    def hide_on_map(self, request, queryset):
        queryset.update(hidden_by_admin=True)
        services.invalidate_shops_cache()

    @admin.action(description="Показать на карте (снять скрытие)")
    def show_on_map(self, request, queryset):
        queryset.update(hidden_by_admin=False, hidden_reason="")
        services.invalidate_shops_cache()


@admin.register(ClientAppConfig)
class ClientAppConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "paid_feature_code", "updated_at")

    def has_add_permission(self, request):
        return not ClientAppConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferralRule)
class ReferralRuleAdmin(admin.ModelAdmin):
    list_display = ("company", "enabled", "inviter_points", "invitee_points")
    raw_id_fields = ("company",)


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("inviter", "invitee", "company", "rewarded_at", "created_at")
    raw_id_fields = ("inviter", "invitee", "company")
