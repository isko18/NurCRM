from django.contrib import admin
from .models import ResidentialComplex


@admin.register(ResidentialComplex)
class ResidentialComplexAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "address", "is_active", "created_at")
    list_filter = ("company", "is_active")
    search_fields = ("name", "address")
    readonly_fields = ("id", "created_at", "updated_at")
