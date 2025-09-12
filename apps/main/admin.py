# admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.utils.timezone import now
from mptt.admin import MPTTModelAdmin

from .models import (
    Contact, Pipeline, Deal, Task, Order, OrderItem,
    GlobalBrand, GlobalCategory, GlobalProduct,
    ProductCategory, ProductBrand, Product,
    Cart, CartItem, MobileScannerToken,
    Sale, SaleItem,
    Review, Notification, Integration, Analytics, Event,
    Warehouse, WarehouseEvent,
    Client, ClientDeal, DealInstallment,
    Bid, SocialApplications, TransactionRecord, DebtPayment, Debt, ObjectItem, ObjectSale, ObjectSaleItem
)

admin.site.site_header = "nurCRM Admin"
admin.site.site_title = "nurCRM Admin"
admin.site.index_title = "Управление данными"

admin.site.register(Debt)
admin.site.register(DebtPayment)
admin.site.register(ObjectItem)
admin.site.register(ObjectSale)
admin.site.register(ObjectSaleItem)


# ========= Инлайны =========
class DealInstallmentInline(admin.TabularInline):
    model = DealInstallment
    extra = 0
    can_delete = False
    fields = ("number", "due_date", "amount", "balance_after", "paid_on")
    readonly_fields = ("number", "due_date", "amount", "balance_after")
    ordering = ("number",)


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ("product",)
    fields = ("product", "quantity", "unit_price")


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    autocomplete_fields = ("product",)
    fields = ("product", "name_snapshot", "barcode_snapshot", "quantity", "unit_price")
    readonly_fields = ("name_snapshot", "barcode_snapshot")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ("product",)
    fields = ("product", "quantity", "price", "total")
    readonly_fields = ("price", "total")


# ========= Базовые справочники / деревья =========
@admin.register(GlobalBrand)
class GlobalBrandAdmin(MPTTModelAdmin):
    list_display = ("name", "parent")
    search_fields = ("name",)


@admin.register(GlobalCategory)
class GlobalCategoryAdmin(MPTTModelAdmin):
    list_display = ("name", "parent")
    search_fields = ("name",)


@admin.register(ProductBrand)
class ProductBrandAdmin(MPTTModelAdmin):
    list_display = ("name", "company", "parent")
    list_filter = ("company",)
    search_fields = ("name",)
    list_select_related = ("company",)


@admin.register(ProductCategory)
class ProductCategoryAdmin(MPTTModelAdmin):
    list_display = ("name", "company", "parent")
    list_filter = ("company",)
    search_fields = ("name",)
    list_select_related = ("company",)


@admin.register(GlobalProduct)
class GlobalProductAdmin(admin.ModelAdmin):
    list_display = ("name", "barcode", "brand", "category", "created_at")
    search_fields = ("name", "barcode")
    list_filter = ("brand", "category", "created_at")
    list_select_related = ("brand", "category")


# ========= Клиенты и сделки =========
@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "client_company", "phone", "email", "company", "owner", "created_at")
    list_filter = ("company", "department", "created_at")
    search_fields = ("name", "client_company", "phone", "email")
    list_select_related = ("company", "owner")


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "owner", "created_at")
    list_filter = ("company", "created_at")
    search_fields = ("name",)
    list_select_related = ("company", "owner")


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "pipeline", "contact", "assigned_to", "value", "status", "stage", "created_at")
    list_filter = ("company", "status", "pipeline", "assigned_to", "created_at")
    search_fields = ("title", "contact__name")
    list_select_related = ("company", "pipeline", "contact", "assigned_to")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "assigned_to", "deal", "due_date", "status", "created_at")
    list_filter = ("company", "status", "assigned_to", "due_date")
    search_fields = ("title", "description")
    list_select_related = ("company", "assigned_to", "deal")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "type", "status", "company", "created_at")
    list_filter = ("company", "status", "type", "created_at")
    search_fields = ("full_name", "phone", "email")
    list_select_related = ("company",)


@admin.register(ClientDeal)
class ClientDealAdmin(admin.ModelAdmin):
    inlines = (DealInstallmentInline,)
    list_display = ("title", "client", "company", "kind", "amount", "prepayment", "debt_months", "remaining_debt", "created_at")
    list_filter = ("company", "kind", "created_at")
    search_fields = ("title", "note", "client__full_name")
    list_select_related = ("company", "client")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Остаток долга")
    def remaining_debt(self, obj):
        return obj.remaining_debt


# ========= Товары / продажи / корзины =========
@admin.action(description="Отметить как принятые")
def accept_products(modeladmin, request, queryset):
    queryset.update(status=Product.Status.ACCEPTED)

@admin.action(description="Отметить как отклонённые")
def reject_products(modeladmin, request, queryset):
    queryset.update(status=Product.Status.REJECTED)

@admin.action(description="Очистить статус (None)")
def clear_status(modeladmin, request, queryset):
    queryset.update(status=None)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "brand", "category", "barcode", "quantity", "purchase_price", "price", "status", "updated_at")
    list_filter = ("company", "brand", "category", "status", "updated_at")
    search_fields = ("name", "barcode")
    list_select_related = ("company", "brand", "category")
    autocomplete_fields = ("brand", "category", "client")
    actions = (accept_products, reject_products, clear_status)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    inlines = (CartItemInline,)
    list_display = ("id", "company", "user", "status", "subtotal", "discount_total", "tax_total", "total", "updated_at")
    list_filter = ("company", "status", "updated_at")
    search_fields = ("id", "session_key", "user__email")
    list_select_related = ("company", "user")
    readonly_fields = ("subtotal", "discount_total", "tax_total", "total", "created_at", "updated_at")


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("cart", "product", "company", "quantity", "unit_price")
    list_filter = ("company",)
    search_fields = ("cart__id", "product__name", "product__barcode")
    list_select_related = ("cart", "product", "company")


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    inlines = (SaleItemInline,)
    list_display = ("id", "company", "user", "client", "status", "total", "created_at", "paid_at")
    list_filter = ("company", "status", "created_at", "paid_at")
    search_fields = ("id", "client__full_name", "user__email")
    list_select_related = ("company", "user", "client")
    readonly_fields = ("created_at", "paid_at", "subtotal", "discount_total", "tax_total", "total")

    @admin.action(description="Отметить как оплаченные")
    def mark_paid_action(self, request, queryset):
        for sale in queryset:
            sale.mark_paid()
    actions = ("mark_paid_action",)


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ("sale", "product", "name_snapshot", "quantity", "unit_price", "company")
    list_filter = ("company",)
    search_fields = ("sale__id", "product__name", "name_snapshot", "barcode_snapshot")
    list_select_related = ("sale", "product", "company")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = (OrderItemInline,)
    list_display = ("order_number", "company", "customer_name", "date_ordered", "status", "phone", "department", "total_qty", "total_amount")
    list_filter = ("company", "status", "date_ordered")
    search_fields = ("order_number", "customer_name", "phone")
    list_select_related = ("company",)

    @admin.display(description="Кол-во")
    def total_qty(self, obj):
        return obj.total_quantity

    @admin.display(description="Итого")
    def total_amount(self, obj):
        return obj.total


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "company", "quantity", "price", "total")
    list_filter = ("company",)
    search_fields = ("order__order_number", "product__name", "product__barcode")
    list_select_related = ("order", "product", "company")


# ========= Прочее =========
@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("company", "user", "rating", "created_at")
    list_filter = ("company", "rating", "created_at")
    search_fields = ("user__email", "company__name")
    list_select_related = ("company", "user")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("company", "user", "short_message", "is_read", "created_at")
    list_filter = ("company", "is_read", "created_at")
    search_fields = ("message", "user__email")
    list_select_related = ("company", "user")

    @admin.display(description="Сообщение")
    def short_message(self, obj):
        return (obj.message[:60] + "…") if len(obj.message) > 60 else obj.message


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ("company", "type", "status", "created_at", "updated_at")
    list_filter = ("company", "type", "status", "created_at")
    search_fields = ("company__name",)
    list_select_related = ("company",)


@admin.register(Analytics)
class AnalyticsAdmin(admin.ModelAdmin):
    list_display = ("company", "type", "metric", "created_at")
    list_filter = ("company", "type", "created_at")
    search_fields = ("data",)
    list_select_related = ("company",)

    @admin.display(description="Метрика")
    def metric(self, obj):
        return obj.data.get("metric", "")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "datetime", "participants_count", "created_at", "updated_at")
    list_filter = ("company", "datetime")
    search_fields = ("title", "notes")
    filter_horizontal = ("participants",)
    list_select_related = ("company",)

    @admin.display(description="Участники")
    def participants_count(self, obj):
        return obj.participants.count()


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "location", "created_at", "updated_at")
    list_filter = ("company",)
    search_fields = ("name", "location")
    list_select_related = ("company",)


@admin.register(WarehouseEvent)
class WarehouseEventAdmin(admin.ModelAdmin):
    list_display = ("title", "warehouse", "status", "event_date", "amount", "responsible_person", "created_at")
    list_filter = ("warehouse", "status", "event_date", "created_at")
    search_fields = ("title", "client_name", "description")
    list_select_related = ("warehouse", "responsible_person")


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("full_name", "phone", "text")


@admin.register(SocialApplications)
class SocialApplicationsAdmin(admin.ModelAdmin):
    # Внимание: в модели __str__ ссылается на несуществующие поля full_name/phone.
    # Чтобы избежать падений, показываем явные поля в списке.
    list_display = ("id", "company", "status", "created_at", "text_short")
    list_filter = ("status", "created_at")
    search_fields = ("company", "text")

    @admin.display(description="Текст")
    def text_short(self, obj):
        t = obj.text or ""
        return (t[:60] + "…") if len(t) > 60 else t


@admin.register(TransactionRecord)
class TransactionRecordAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "department", "amount", "status", "date", "created_at")
    list_filter = ("company", "status", "department", "date")
    search_fields = ("name", "description")
    list_select_related = ("company", "department")


@admin.register(MobileScannerToken)
class MobileScannerTokenAdmin(admin.ModelAdmin):
    list_display = ("token", "company", "cart", "expires_at", "is_valid_now")
    list_filter = ("company", "expires_at")
    search_fields = ("token", "cart__id")
    list_select_related = ("company", "cart")

    @admin.display(boolean=True, description="Действителен")
    def is_valid_now(self, obj):
        return now() <= obj.expires_at
