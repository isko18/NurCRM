from django.contrib import admin
from .models import Hotel, Bed, ConferenceRoom, Booking, ManagerAssignment, Folder, Document


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "capacity", "price")
    list_filter = ("company",)
    search_fields = ("name", "description")
    ordering = ("company", "name")


@admin.register(Bed)
class BedAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "capacity", "price")
    list_filter = ("company",)
    search_fields = ("name", "description")
    ordering = ("company", "name")


@admin.register(ConferenceRoom)
class ConferenceRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "capacity", "location", "price")
    list_filter = ("company",)
    search_fields = ("name", "location")
    ordering = ("company", "name")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "hotel", "room", "bed", "reserved_by", "start_time", "end_time", "purpose")
    list_filter = ("company", "start_time", "end_time")
    search_fields = ("purpose", "reserved_by__email", "hotel__name", "room__name", "bed__name")
    date_hierarchy = "start_time"
    ordering = ("-start_time",)


@admin.register(ManagerAssignment)
class ManagerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("company", "room", "manager")
    list_filter = ("company",)
    search_fields = ("room__name", "manager__email")


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "parent")
    list_filter = ("company",)
    search_fields = ("name",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "folder", "created_at", "updated_at")
    list_filter = ("company", "created_at", "updated_at")
    search_fields = ("name", "file")
    ordering = ("name",)
