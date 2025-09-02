from django.contrib import admin
from django.forms.models import BaseInlineFormSet

from .models import (
    Hotel, Bed, ConferenceRoom, Booking, ManagerAssignment,
    Folder, Document, BookingClient,
)


# ========= Inlines =========
class BookingInlineFormSet(BaseInlineFormSet):
    """При создании брони из карточки клиента проставляем client и company автоматически."""
    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        obj.client = self.instance
        obj.company = self.instance.company
        if commit:
            obj.save()
            form.save_m2m()
        return obj


class BookingInline(admin.TabularInline):
    model = Booking
    formset = BookingInlineFormSet
    extra = 1
    fields = ("start_time", "end_time", "hotel", "room", "bed", "purpose")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        # текущий клиент кладём в request в BookingClientAdmin.get_form
        obj = getattr(request, "_bc_admin_obj", None)
        if obj and db_field.name in ("hotel", "room", "bed"):
            if db_field.name == "hotel":
                ff.queryset = Hotel.objects.filter(company=obj.company)
            elif db_field.name == "room":
                ff.queryset = ConferenceRoom.objects.filter(company=obj.company)
            elif db_field.name == "bed":
                ff.queryset = Bed.objects.filter(company=obj.company)
        return ff


# ========= BookingClient =========
@admin.register(BookingClient)
class BookingClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "company")
    list_filter = ("company",)
    search_fields = ("name", "phone")
    ordering = ("company", "name")
    inlines = [BookingInline]

    def get_form(self, request, obj=None, **kwargs):
        # чтобы BookingInline знал "текущего клиента" для фильтрации справочников
        request._bc_admin_obj = obj
        return super().get_form(request, obj, **kwargs)


# ========= Hotel =========
@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "capacity", "price")
    list_filter = ("company",)
    search_fields = ("name", "description")
    ordering = ("company", "name")


# ========= Bed =========
@admin.register(Bed)
class BedAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "capacity", "price")
    list_filter = ("company",)
    search_fields = ("name", "description")
    ordering = ("company", "name")


# ========= ConferenceRoom =========
@admin.register(ConferenceRoom)
class ConferenceRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "capacity", "location", "price")
    list_filter = ("company",)
    search_fields = ("name", "location")
    ordering = ("company", "name")


# ========= Booking =========
@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "hotel", "room", "bed", "client", "start_time", "end_time", "purpose")
    list_filter = ("company", "start_time", "end_time")
    search_fields = ("purpose", "client__name", "client__phone", "hotel__name", "room__name", "bed__name")
    date_hierarchy = "start_time"
    ordering = ("-start_time",)
    list_select_related = ("company", "hotel", "room", "bed", "client")


# ========= ManagerAssignment =========
@admin.register(ManagerAssignment)
class ManagerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("company", "room", "manager")
    list_filter = ("company",)
    search_fields = ("room__name", "manager__email")


# ========= Folder =========
@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "parent")
    list_filter = ("company",)
    search_fields = ("name",)


# ========= Document =========
@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "folder", "created_at", "updated_at")
    list_filter = ("company", "created_at", "updated_at")
    search_fields = ("name", "file")
    ordering = ("name",)
