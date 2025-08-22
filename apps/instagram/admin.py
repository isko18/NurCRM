from django.contrib import admin
from .models import CompanyIGAccount, IGThread, IGMessage

@admin.register(CompanyIGAccount)
class CompanyIGAccountAdmin(admin.ModelAdmin):
    list_display = ("username","company","is_active","is_logged_in","last_login_at","created_at")
    list_filter = ("is_active","is_logged_in","company")
    search_fields = ("username","company__name")
    readonly_fields = ("last_login_at","created_at","updated_at")

class IGMessageInline(admin.TabularInline):
    model = IGMessage
    extra = 0
    fields = ("mid","direction","sender_pk","text","created_at")
    readonly_fields = ("mid","direction","sender_pk","text","created_at")

@admin.register(IGThread)
class IGThreadAdmin(admin.ModelAdmin):
    list_display = ("thread_id","ig_account","last_activity","created_at")
    list_filter = ("ig_account__company",)
    search_fields = ("thread_id","ig_account__username")
    readonly_fields = ("created_at","updated_at")
    inlines = [IGMessageInline]

@admin.register(IGMessage)
class IGMessageAdmin(admin.ModelAdmin):
    list_display = ("mid","thread","direction","sender_pk","created_at")
    list_filter = ("direction","thread__ig_account__company")
    search_fields = ("mid","thread__thread_id","sender_pk","text")
    readonly_fields = ("created_local_at",)
