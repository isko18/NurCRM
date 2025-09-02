# apps/cafe/admin.py
from django.contrib import admin
from django.forms.models import BaseInlineFormSet
from .models import CafeClient, Order, OrderItem, Table, MenuItem

class OrderInlineFormSet(BaseInlineFormSet):
    def save_new(self, form, commit=True):
        obj = super().save_new(form, commit=False)
        obj.client = self.instance
        obj.company = self.instance.company
        if commit:
            obj.full_clean()  # чтобы сработал clean() из модели
            obj.save()
            form.save_m2m()
        return obj

class OrderInline(admin.TabularInline):
    model = Order
    formset = OrderInlineFormSet
    extra = 1
    fields = ("table", "guests", "waiter", "created_at")
    readonly_fields = ("created_at",)

    # фильтруем столы по компании клиента
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        obj = getattr(request, "_cafe_client_admin_obj", None)
        if obj and db_field.name == "table":
            ff.queryset = Table.objects.filter(company=obj.company)
        return ff

@admin.register(CafeClient)
class CafeClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "company")
    list_filter = ("company",)
    search_fields = ("name", "phone")
    ordering = ("company", "name")
    inlines = [OrderInline]

    def get_form(self, request, obj=None, **kwargs):
        request._cafe_client_admin_obj = obj
        return super().get_form(request, obj, **kwargs)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "client", "table", "guests", "waiter", "created_at")
    list_filter = ("company", "created_at")
    search_fields = ("client__name", "client__phone", "table__number", "waiter__email")
    ordering = ("-created_at",)
    list_select_related = ("company", "client", "table", "waiter")

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "menu_item", "quantity", "company")
    list_filter = ("company",)
    search_fields = ("order__id", "menu_item__title")
