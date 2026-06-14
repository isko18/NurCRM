from django.contrib import admin

from .models import ClientRelease


@admin.register(ClientRelease)
class ClientReleaseAdmin(admin.ModelAdmin):
    list_display = ["version", "created_at"]
    readonly_fields = ["created_at"]
