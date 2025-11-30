from django.contrib import admin
from .models import (
    Warehouse, Supplier, Product, Stock,
    StockIn, StockInItem,
    StockOut, StockOutItem,
    StockTransfer, StockTransferItem
)


# ===== общие настройки =====
class CompanyBranchAdminMixin(admin.ModelAdmin):
    """
    Показывает company/branch, фильтры по ним, базовый поиск.
    """
    list_filter = ("company", "branch")
    search_fields = ("name",)
    readonly_fields = ("company", "branch")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # ограничим доступ по компании пользователя, если нужно
        user = getattr(request, "user", None)
        company = getattr(user, "company", None)
        if company and not user.is_superuser:
            qs = qs.filter(company=company)
        return qs


# 📦 Склады
@admin.register(Warehouse)
class WarehouseAdmin(CompanyBranchAdminMixin):
    list_display = ("name", "company", "branch", "address", "created_at")
    search_fields = ("name", "address")


# 🚚 Поставщики
@admin.register(Supplier)
class SupplierAdmin(CompanyBranchAdminMixin):
    list_display = ("name", "company", "branch", "contact_name", "phone", "email")
    search_fields = ("name", "contact_name", "phone", "email")


# 🛒 Товары
@admin.register(Product)
class ProductAdmin(CompanyBranchAdminMixin):
    list_display = (
        "name", "company", "branch",
        "brand", "category",
        "unit", "purchase_price", "selling_price", "is_active",
    )
    list_filter = ("company", "branch", "brand", "category", "is_active")
    search_fields = ("name", "barcode")


# 📊 Остатки
@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("product", "warehouse", "company", "branch", "quantity")
    list_filter = ("warehouse__company", "warehouse__branch")
    search_fields = ("product__name", "warehouse__name")

    def company(self, obj):
        return obj.warehouse.company

    def branch(self, obj):
        return obj.warehouse.branch


# 📥 Приход
class StockInItemInline(admin.TabularInline):
    model = StockInItem
    extra = 0
    autocomplete_fields = ("product",)


@admin.register(StockIn)
class StockInAdmin(CompanyBranchAdminMixin):
    list_display = ("document_number", "company", "branch", "date", "supplier", "warehouse", "created_at")
    list_filter = ("company", "branch", "supplier", "warehouse")
    search_fields = ("document_number",)
    inlines = [StockInItemInline]


# 📤 Расход
class StockOutItemInline(admin.TabularInline):
    model = StockOutItem
    extra = 0
    autocomplete_fields = ("product",)


@admin.register(StockOut)
class StockOutAdmin(CompanyBranchAdminMixin):
    list_display = ("document_number", "company", "branch", "date", "warehouse", "type", "recipient")
    list_filter = ("company", "branch", "warehouse", "type")
    search_fields = ("document_number", "recipient")
    inlines = [StockOutItemInline]


# 🔄 Перемещения
class StockTransferItemInline(admin.TabularInline):
    model = StockTransferItem
    extra = 0
    autocomplete_fields = ("product",)


@admin.register(StockTransfer)
class StockTransferAdmin(CompanyBranchAdminMixin):
    list_display = (
        "document_number", "company", "branch", "date",
        "source_warehouse", "destination_warehouse",
    )
    list_filter = ("company", "branch", "source_warehouse", "destination_warehouse")
    search_fields = ("document_number",)
    inlines = [StockTransferItemInline]
