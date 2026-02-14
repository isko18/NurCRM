from django.contrib import admin
from . import models


@admin.register(models.Document)
class DocumentAdmin(admin.ModelAdmin):
	list_display = ("number", "doc_type", "status", "date", "warehouse_from", "warehouse_to", "counterparty", "discount_percent", "discount_amount", "total")
	list_filter = ("doc_type", "status")
	search_fields = ("number", "comment")


@admin.register(models.DocumentItem)
class DocumentItemAdmin(admin.ModelAdmin):
	list_display = ("document", "product", "qty", "price", "discount_percent", "discount_amount", "line_total")
	search_fields = ("product__name",)


@admin.register(models.StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
	list_display = ("warehouse", "product", "qty")
	list_filter = ("warehouse",)


@admin.register(models.StockMove)
class StockMoveAdmin(admin.ModelAdmin):
	list_display = ("document", "warehouse", "product", "qty_delta", "created_at")
	list_filter = ("warehouse",)


@admin.register(models.Counterparty)
class CounterpartyAdmin(admin.ModelAdmin):
	list_display = ("name", "type")
	search_fields = ("name",)


@admin.register(models.WarehouseProductGroup)
class WarehouseProductGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "warehouse", "parent", "company", "branch")
    list_filter = ("warehouse", "company", "branch")
    search_fields = ("name",)
    raw_id_fields = ("warehouse", "parent")


@admin.register(models.CashRegister)
class CashRegisterAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "branch", "location")
    list_filter = ("company", "branch")
    search_fields = ("name",)


@admin.register(models.PaymentCategory)
class PaymentCategoryAdmin(admin.ModelAdmin):
	list_display = ("title", "company", "branch")
	search_fields = ("title",)
	list_filter = ("company", "branch")


@admin.register(models.MoneyDocument)
class MoneyDocumentAdmin(admin.ModelAdmin):
    list_display = ("number", "doc_type", "status", "date", "cash_register", "warehouse", "counterparty", "payment_category", "amount")
    list_filter = ("doc_type", "status", "company", "branch", "cash_register", "warehouse", "payment_category")
    search_fields = ("number", "comment", "counterparty__name")