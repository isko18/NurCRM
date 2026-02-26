from django.contrib import admin
from .models import (
    ResidentialComplex,
    ResidentialComplexDrawing,
    ResidentialComplexWarehouse,
    BuildingProduct,
    BuildingProcurementRequest,
    BuildingProcurementItem,
    BuildingProcurementCashDecision,
    BuildingTransferRequest,
    BuildingTransferItem,
    BuildingWarehouseStockItem,
    BuildingWarehouseStockMove,
    BuildingWorkflowEvent,
)


@admin.register(ResidentialComplex)
class ResidentialComplexAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "address", "is_active", "created_at")
    list_filter = ("company", "is_active")
    search_fields = ("name", "address")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ResidentialComplexDrawing)
class ResidentialComplexDrawingAdmin(admin.ModelAdmin):
    list_display = ("title", "residential_complex", "is_active", "created_at")
    list_filter = ("residential_complex__company", "is_active")
    search_fields = ("title", "residential_complex__name", "description")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ResidentialComplexWarehouse)
class ResidentialComplexWarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "residential_complex", "is_active", "created_at")
    list_filter = ("residential_complex__company", "is_active")
    search_fields = ("name", "residential_complex__name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(BuildingProduct)
class BuildingProductAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "article", "barcode", "unit", "is_active", "created_at")
    list_filter = ("company", "is_active")
    search_fields = ("name", "article", "barcode")
    readonly_fields = ("id", "created_at", "updated_at")


class BuildingProcurementItemInline(admin.TabularInline):
    model = BuildingProcurementItem
    extra = 0
    readonly_fields = ("id", "line_total", "created_at", "updated_at")


@admin.register(BuildingProcurementRequest)
class BuildingProcurementRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "residential_complex", "status", "total_amount", "initiator", "created_at")
    list_filter = ("status", "residential_complex__company")
    search_fields = ("title", "comment", "residential_complex__name")
    readonly_fields = ("id", "total_amount", "submitted_to_cash_at", "cash_decided_at", "created_at", "updated_at")
    inlines = [BuildingProcurementItemInline]


@admin.register(BuildingProcurementCashDecision)
class BuildingProcurementCashDecisionAdmin(admin.ModelAdmin):
    list_display = ("procurement", "decision", "decided_by", "decided_at")
    list_filter = ("decision", "procurement__residential_complex__company")
    search_fields = ("procurement__title", "reason")
    readonly_fields = ("id", "decided_at")


class BuildingTransferItemInline(admin.TabularInline):
    model = BuildingTransferItem
    extra = 0
    readonly_fields = ("id", "line_total", "created_at", "updated_at")


@admin.register(BuildingTransferRequest)
class BuildingTransferRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "procurement", "warehouse", "status", "total_amount", "created_by", "created_at")
    list_filter = ("status", "warehouse__residential_complex__company")
    search_fields = ("procurement__title", "warehouse__name", "note", "rejection_reason")
    readonly_fields = ("id", "total_amount", "accepted_at", "rejected_at", "created_at", "updated_at")
    inlines = [BuildingTransferItemInline]


@admin.register(BuildingWarehouseStockItem)
class BuildingWarehouseStockItemAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "warehouse", "quantity", "last_price", "updated_at")
    list_filter = ("warehouse__residential_complex__company", "warehouse")
    search_fields = ("name", "warehouse__name", "warehouse__residential_complex__name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(BuildingWarehouseStockMove)
class BuildingWarehouseStockMoveAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "stock_item", "move_type", "quantity_delta", "price", "created_by", "created_at")
    list_filter = ("move_type", "warehouse__residential_complex__company", "warehouse")
    search_fields = ("stock_item__name", "warehouse__name")
    readonly_fields = ("id", "created_at")


@admin.register(BuildingWorkflowEvent)
class BuildingWorkflowEventAdmin(admin.ModelAdmin):
    list_display = ("action", "actor", "procurement", "transfer", "warehouse", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("action", "message", "procurement__title", "warehouse__name")
    readonly_fields = ("id", "created_at", "payload")
