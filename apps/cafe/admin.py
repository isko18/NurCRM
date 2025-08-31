from django.contrib import admin
from django.conf import settings
from .models import (
    Zone, Table, Booking, Warehouse, Purchase,
    Category, MenuItem, Ingredient, Order, OrderItem,
)


# ---------- helpers ----------

def _has_company_field(model_cls) -> bool:
    return any(f.name == "company" for f in model_cls._meta.fields)

def short_id(obj):
    return str(obj.id)[:8]
short_id.short_description = "ID"


# ---------- mixins (company scoping) ----------

class CompanyAdminMixin:
    """Сужает queryset и варианты в FK по company пользователя; автопроставляет company при сохранении."""
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or not _has_company_field(self.model):
            return qs
        return qs.filter(company=getattr(request.user, "company", None))

    def save_model(self, request, obj, form, change):
        if _has_company_field(type(obj)) and not obj.company_id:
            obj.company = getattr(request.user, "company", None)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.remote_field:
            rel_model = db_field.remote_field.model
            if _has_company_field(rel_model):
                kwargs["queryset"] = rel_model.objects.filter(
                    company=getattr(request.user, "company", None)
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_list_filter(self, request):
        base = getattr(super(), "list_filter", ())
        if request.user.is_superuser and _has_company_field(self.model):
            return (*base, "company")
        return base


class CompanyInlineMixin:
    """Та же фильтрация для инлайнов."""
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if hasattr(self.model, "company_id"):
            return qs.filter(company=getattr(request.user, "company", None))
        if self.model is Ingredient:
            return qs.filter(menu_item__company=getattr(request.user, "company", None))
        if self.model is OrderItem:
            return qs.filter(order__company=getattr(request.user, "company", None))
        return qs.none()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.remote_field:
            rel_model = db_field.remote_field.model
            if _has_company_field(rel_model):
                kwargs["queryset"] = rel_model.objects.filter(
                    company=getattr(request.user, "company", None)
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ---------- inlines ----------

class IngredientInline(CompanyInlineMixin, admin.TabularInline):
    model = Ingredient
    extra = 0
    autocomplete_fields = ["product", "menu_item"]
    fields = ("product", "amount")
    verbose_name = "Ингредиент"
    verbose_name_plural = "Ингредиенты"


class OrderItemInline(CompanyInlineMixin, admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ["menu_item"]
    fields = ("menu_item", "quantity")
    verbose_name = "Позиция"
    verbose_name_plural = "Позиции"


# ---------- admins ----------

@admin.register(Zone)
class ZoneAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = (short_id, "title", "company")
    search_fields = ("title",)
    ordering = ("title",)
    list_per_page = 50


@admin.register(Table)
class TableAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = (short_id, "number", "places", "status", "zone", "company")
    list_filter = ("status", "zone")
    search_fields = ("zone__title",)
    ordering = ("number",)
    list_select_related = ("zone", "company")
    autocomplete_fields = ("zone",)


@admin.register(Booking)
class BookingAdmin(CompanyAdminMixin, admin.ModelAdmin):
    date_hierarchy = "date"
    list_display = (short_id, "date", "time", "guest", "table", "guests", "status", "company")
    list_filter = ("status", "date", "table")
    search_fields = ("guest", "phone")
    ordering = ("-date", "-time")
    list_select_related = ("table", "company")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("table",)


@admin.register(Warehouse)
class WarehouseAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = ("title", "unit", "remainder", "minimum", "company")
    search_fields = ("title", "unit")
    ordering = ("title",)


@admin.register(Purchase)
class PurchaseAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = ("supplier", "positions", "price", "company")
    search_fields = ("supplier",)
    ordering = ("-price",)


@admin.register(Category)
class CategoryAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = ("title", "company")
    search_fields = ("title",)
    ordering = ("title",)


@admin.register(MenuItem)
class MenuItemAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = ("title", "category", "price", "is_active", "company")
    list_filter = ("is_active", "category")
    search_fields = ("title", "category__title")
    ordering = ("title",)
    list_select_related = ("category", "company")
    autocomplete_fields = ("category",)
    inlines = [IngredientInline]


@admin.register(Ingredient)
class IngredientAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = ("menu_item", "product", "amount", "get_company")
    search_fields = ("menu_item__title", "product__title")
    autocomplete_fields = ("menu_item", "product")

    def get_company(self, obj):
        return getattr(obj.menu_item, "company", None)
    get_company.short_description = "Компания"


@admin.register(Order)
class OrderAdmin(CompanyAdminMixin, admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = (short_id, "table", "waiter", "guests", "created_at", "company")
    list_filter = ("waiter", "table")
    search_fields = ("table__number", "waiter__username", "waiter__email")
    ordering = ("-created_at",)
    list_select_related = ("table", "waiter", "company")
    autocomplete_fields = ("table", "waiter")
    inlines = [OrderItemInline]


@admin.register(OrderItem)
class OrderItemAdmin(CompanyAdminMixin, admin.ModelAdmin):
    list_display = ("order", "menu_item", "quantity", "get_company")
    search_fields = ("order__id", "menu_item__title")
    autocomplete_fields = ("order", "menu_item")

    def get_company(self, obj):
        return getattr(obj.order, "company", None)
    get_company.short_description = "Компания"
