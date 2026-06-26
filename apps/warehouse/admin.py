from django.contrib import admin
from . import models


@admin.register(models.Document)
class DocumentAdmin(admin.ModelAdmin):
	list_display = ("number", "doc_type", "status", "date", "warehouse_from", "warehouse_to", "counterparty", "discount_percent", "discount_amount", "total")
	list_filter = ("doc_type", "status", "warehouse_from__company")
	search_fields = ("number", "comment")


@admin.register(models.DocumentItem)
class DocumentItemAdmin(admin.ModelAdmin):
	list_display = ("document", "product", "qty", "price", "discount_percent", "discount_amount", "line_total")
	list_filter = ("document__warehouse_from__company",)
	search_fields = ("product__name",)


@admin.register(models.StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
	list_display = ("warehouse", "product", "qty")
	list_filter = ("warehouse__company", "warehouse")


@admin.register(models.StockMove)
class StockMoveAdmin(admin.ModelAdmin):
	list_display = ("document", "warehouse", "product", "qty_delta", "created_at")
	list_filter = ("warehouse__company", "warehouse")


@admin.register(models.Counterparty)
class CounterpartyAdmin(admin.ModelAdmin):
	list_display = ("name", "type", "company")
	list_filter = ("company", "type")
	search_fields = ("name",)


@admin.register(models.CompanyStockPartnership)
class CompanyStockPartnershipAdmin(admin.ModelAdmin):
	list_display = ("company_a", "company_b", "created_at")
	list_filter = ("company_a", "company_b")


@admin.register(models.CompanyStockPartnershipRequest)
class CompanyStockPartnershipRequestAdmin(admin.ModelAdmin):
	list_display = ("from_company", "to_company", "status", "created_at")
	list_filter = ("status", "from_company", "to_company")


@admin.register(models.CompanyCashIncassation)
class CompanyCashIncassationAdmin(admin.ModelAdmin):
	list_display = ("from_company", "to_company", "amount", "cash_register_from", "cash_register_to", "created_at")
	list_filter = ("from_company", "to_company")


@admin.register(models.CompanyWarehouseAgent)
class CompanyWarehouseAgentAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "company",
        "status",
        "assigned_warehouse",
        "common_access_enabled",
        "common_warehouse",
        "created_at",
        "decided_at",
        "decided_by",
    )
    list_filter = ("status", "company")
    search_fields = ("user__email", "company__name", "note")
    raw_id_fields = ("user", "company", "assigned_warehouse", "common_warehouse", "decided_by")


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
	list_display = ("title", "system_code", "company", "branch")
	search_fields = ("title",)
	list_filter = ("company", "branch", "system_code")
	readonly_fields = ("system_code",)


@admin.register(models.MoneyDocument)
class MoneyDocumentAdmin(admin.ModelAdmin):
    list_display = ("number", "doc_type", "status", "date", "cash_register", "warehouse", "counterparty", "payment_category", "amount")
    list_filter = ("doc_type", "status", "company", "branch", "cash_register", "warehouse", "payment_category")
    search_fields = ("number", "comment", "counterparty__name")


@admin.register(models.CashApprovalRequest)
class CashApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ("document", "status", "requires_money", "money_doc_type", "amount", "requested_at", "decided_at")
    list_filter = ("status", "requires_money", "money_doc_type", "document__warehouse_from__company")
    search_fields = ("document__number", "decision_note")


class WarehouseSalesSummaryDocumentInline(admin.TabularInline):
    model = models.WarehouseSalesSummaryDocument
    extra = 0


class WarehouseSalesSummaryProductInline(admin.TabularInline):
    model = models.WarehouseSalesSummaryProduct
    extra = 0


@admin.register(models.WarehouseSalesSummary)
class WarehouseSalesSummaryAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "type", "date", "warehouse", "company", "documents_count", "products_count", "total_amount", "created_at")
    list_filter = ("type", "company", "branch", "warehouse")
    search_fields = ("number", "name", "comment")
    readonly_fields = ("number", "documents_count", "products_count", "total_quantity", "total_weight", "total_amount", "created_at", "updated_at")
    inlines = (WarehouseSalesSummaryDocumentInline, WarehouseSalesSummaryProductInline)