from django.contrib import admin
from .models import (
    Warehouse, Supplier, Product, Stock,
    StockIn, StockInItem,
    StockOut, StockOutItem,
    StockTransfer, StockTransferItem
)


# 📦 Склады
@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "address", "created_at")
    list_filter = ("company",)
    search_fields = ("name", "address")


# 🚚 Поставщики
@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "contact_name", "phone", "email")
    list_filter = ("company",)
    search_fields = ("name", "contact_name", "phone", "email")


# 🛒 Товары
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "brand", "category", "unit", "purchase_price", "selling_price", "is_active")
    list_filter = ("company", "brand", "category", "is_active")
    search_fields = ("name", "barcode")


# 📊 Остатки
@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("product", "warehouse", "quantity")
    list_filter = ("warehouse", "product__company")
    search_fields = ("product__name", "warehouse__name")


# 📥 Приход
class StockInItemInline(admin.TabularInline):
    model = StockInItem
    extra = 1


@admin.register(StockIn)
class StockInAdmin(admin.ModelAdmin):
    list_display = ("document_number", "company", "date", "supplier", "warehouse", "created_at")
    list_filter = ("company", "supplier", "warehouse")
    search_fields = ("document_number",)
    inlines = [StockInItemInline]


# 📤 Расход
class StockOutItemInline(admin.TabularInline):
    model = StockOutItem
    extra = 1


@admin.register(StockOut)
class StockOutAdmin(admin.ModelAdmin):
    list_display = ("document_number", "company", "date", "warehouse", "type", "recipient")
    list_filter = ("company", "warehouse", "type")
    search_fields = ("document_number", "recipient")
    inlines = [StockOutItemInline]


# 🔄 Перемещения
class StockTransferItemInline(admin.TabularInline):
    model = StockTransferItem
    extra = 1


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ("document_number", "company", "date", "source_warehouse", "destination_warehouse")
    list_filter = ("company", "source_warehouse", "destination_warehouse")
    search_fields = ("document_number",)
    inlines = [StockTransferItemInline]
