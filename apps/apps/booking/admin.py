from django.contrib import admin
from django.db.models import Q
from django.forms.models import BaseInlineFormSet

from .models import (
    Hotel, Bed, ConferenceRoom, Booking, ManagerAssignment,
    Folder, Document, BookingClient,
)


def has_branch_field(model_cls) -> bool:
    try:
        return any(f.name == "branch" for f in model_cls._meta.get_fields())
    except Exception:
        return False


# ========= Inlines =========
class BookingInlineFormSet(BaseInlineFormSet):
    """При создании брони из карточки клиента проставляем client/company (+ branch, если есть)."""
    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        obj.client = self.instance
        obj.company = self.instance.company
        # если у Booking есть branch — наследуем от клиента (может быть None = глобально)
        if has_branch_field(type(obj)) and hasattr(self.instance, "branch"):
            obj.branch = getattr(self.instance, "branch", None)
        if commit:
            obj.full_clean()
            obj.save()
            form.save_m2m()
        return obj

    def save_existing(self, form, instance, commit=True):
        obj = super().save_existing(form, instance, commit=False)
        obj.client = self.instance
        obj.company = self.instance.company
        if has_branch_field(type(obj)) and hasattr(self.instance, "branch"):
            obj.branch = getattr(self.instance, "branch", None)
        if commit:
            obj.full_clean()
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
        if not obj or db_field.name not in ("hotel", "room", "bed"):
            return ff

        company = obj.company
        client_branch = getattr(obj, "branch", None) if hasattr(obj, "branch") else None

        def scope_qs(model_cls):
            qs = model_cls.objects.filter(company=company)
            if has_branch_field(model_cls):
                if client_branch:
                    qs = qs.filter(Q(branch__isnull=True) | Q(branch=client_branch))
                else:
                    qs = qs.filter(branch__isnull=True)
            return qs

        if db_field.name == "hotel":
            ff.queryset = scope_qs(Hotel)
        elif db_field.name == "room":
            ff.queryset = scope_qs(ConferenceRoom)
        elif db_field.name == "bed":
            ff.queryset = scope_qs(Bed)
        return ff


# ========= BookingClient =========
@admin.register(BookingClient)
class BookingClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "company") + (("branch",) if has_branch_field(BookingClient) else ())
    list_filter = ("company",) + (("branch",) if has_branch_field(BookingClient) else ())
    search_fields = ("name", "phone")
    ordering = ("company",) + (("branch",) if has_branch_field(BookingClient) else ()) + ("name",)
    inlines = [BookingInline]

    def get_form(self, request, obj=None, **kwargs):
        # чтобы BookingInline знал "текущего клиента" для фильтрации справочников
        request._bc_admin_obj = obj
        return super().get_form(request, obj, **kwargs)


# ========= Hotel =========
@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("name", "company") + (("branch",) if has_branch_field(Hotel) else ()) + ("capacity", "price")
    list_filter = ("company",) + (("branch",) if has_branch_field(Hotel) else ())
    search_fields = ("name", "description")
    ordering = ("company",) + (("branch",) if has_branch_field(Hotel) else ()) + ("name",)


# ========= Bed =========
@admin.register(Bed)
class BedAdmin(admin.ModelAdmin):
    list_display = ("name", "company") + (("branch",) if has_branch_field(Bed) else ()) + ("capacity", "price")
    list_filter = ("company",) + (("branch",) if has_branch_field(Bed) else ())
    search_fields = ("name", "description")
    ordering = ("company",) + (("branch",) if has_branch_field(Bed) else ()) + ("name",)


# ========= ConferenceRoom =========
@admin.register(ConferenceRoom)
class ConferenceRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "company") + (("branch",) if has_branch_field(ConferenceRoom) else ()) + ("capacity", "location", "price")
    list_filter = ("company",) + (("branch",) if has_branch_field(ConferenceRoom) else ())
    search_fields = ("name", "location")
    ordering = ("company",) + (("branch",) if has_branch_field(ConferenceRoom) else ()) + ("name",)


# ========= Booking =========
@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "company") + (("branch",) if has_branch_field(Booking) else ()) + ("hotel", "room", "bed", "client", "start_time", "end_time", "purpose")
    list_filter = ("company",) + (("branch",) if has_branch_field(Booking) else ()) + ("start_time", "end_time")
    search_fields = ("purpose", "client__name", "client__phone", "hotel__name", "room__name", "bed__name")
    date_hierarchy = "start_time"
    ordering = ("-start_time",)
    list_select_related = ("company", "client", "hotel", "room", "bed") + (("branch",) if has_branch_field(Booking) else ())


# ========= ManagerAssignment =========
@admin.register(ManagerAssignment)
class ManagerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("company",) + (("room__branch",) if has_branch_field(ConferenceRoom) else ()) + ("room", "manager")
    list_filter = ("company",) + (("room__branch",) if has_branch_field(ConferenceRoom) else ())
    search_fields = ("room__name", "manager__email")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if has_branch_field(ConferenceRoom):
            return qs.select_related("room__branch", "room", "manager", "company")
        return qs.select_related("room", "manager", "company")


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
