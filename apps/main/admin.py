# admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.utils.timezone import now
from django.db.models import Sum
from mptt.admin import MPTTModelAdmin

from .models import (
    # CRM core
    Contact, Pipeline, Deal, Task,
    # Orders
    Order, OrderItem,
    # Global dictionaries
    GlobalBrand, GlobalCategory, GlobalProduct,
    # Company-scoped taxonomies & goods
    ProductCategory, ProductBrand, Product, ItemMake,
    # POS
    Cart, CartItem, MobileScannerToken, Sale, SaleItem,
    # Others
    Review, Notification, Integration, Analytics, Event,
    # Warehouse
    Warehouse, WarehouseEvent,
    # Clients & deals
    Client, ClientDeal, DealInstallment,
    # Leads/forms
    Bid, SocialApplications,
    # Finance
    TransactionRecord, DebtPayment, Debt,
    # Object items/sales
    ObjectItem, ObjectSale, ObjectSaleItem,
    # Subreal / agent pathway
    ManufactureSubreal, Acceptance, ReturnFromAgent, ProductImage
)

admin.site.site_header = "nurCRM Admin"
admin.site.site_title = "nurCRM Admin"
admin.site.index_title = "Управление данными"

# ======== Простые реестры (без спец-настроек) ========
@admin.register(Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "company", "branch", "amount", "due_date", "created_at")
    list_filter = ("company", "branch", "due_date", "created_at")
    search_fields = ("name", "phone")
    list_select_related = ("company", "branch")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("company", "branch")

@admin.register(DebtPayment)
class DebtPaymentAdmin(admin.ModelAdmin):
    list_display = ("debt", "company", "branch", "amount", "paid_at", "created_at")
    list_filter = ("company", "branch", "paid_at", "created_at")
    search_fields = ("debt__name", "debt__phone")
    list_select_related = ("company", "branch", "debt")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("company", "branch", "debt")

@admin.register(ObjectItem)
class ObjectItemAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "branch", "price", "quantity", "date", "created_at")
    list_filter = ("company", "branch", "date")
    search_fields = ("name",)
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch")

@admin.register(ObjectSale)
class ObjectSaleAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "branch", "client", "status", "sold_at", "subtotal")
    list_filter = ("company", "branch", "status", "sold_at")
    search_fields = ("id", "client__full_name")
    list_select_related = ("company", "branch", "client")
    readonly_fields = ("created_at", "subtotal")
    autocomplete_fields = ("company", "branch", "client")

@admin.register(ObjectSaleItem)
class ObjectSaleItemAdmin(admin.ModelAdmin):
    list_display = ("sale", "name_snapshot", "unit_price", "quantity", "object_item")
    list_filter = ("sale__company",)
    search_fields = ("sale__id", "name_snapshot", "object_item__name")
    list_select_related = ("sale", "object_item")
    autocomplete_fields = ("sale", "object_item")

@admin.register(ManufactureSubreal)
class ManufactureSubrealAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "branch", "user", "agent", "product",
                    "qty_transferred", "qty_accepted", "qty_returned",
                    "status", "created_at")
    list_filter = ("company", "branch", "status", "created_at")
    search_fields = ("agent__email", "product__name")
    list_select_related = ("company", "branch", "user", "agent", "product")
    autocomplete_fields = ("company", "branch", "user", "agent", "product")

@admin.register(Acceptance)
class AcceptanceAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "branch", "subreal", "accepted_by", "qty", "accepted_at")
    list_filter = ("company", "branch", "accepted_at")
    search_fields = ("subreal__agent__email", "subreal__product__name")
    list_select_related = ("company", "branch", "subreal", "accepted_by")
    autocomplete_fields = ("company", "branch", "subreal", "accepted_by")

@admin.register(ReturnFromAgent)
class ReturnFromAgentAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "branch", "subreal", "returned_by", "qty",
                    "status", "returned_at", "accepted_by", "accepted_at")
    list_filter = ("company", "branch", "status", "returned_at", "accepted_at")
    search_fields = ("subreal__agent__email", "subreal__product__name")
    list_select_related = ("company", "branch", "subreal", "returned_by", "accepted_by")
    autocomplete_fields = ("company", "branch", "subreal", "returned_by", "accepted_by")

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
    fields = ("product", "custom_name", "quantity", "unit_price")

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
    list_display = ("name", "company", "branch", "parent")
    list_filter = ("company", "branch")
    search_fields = ("name",)
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch", "parent")

@admin.register(ProductCategory)
class ProductCategoryAdmin(MPTTModelAdmin):
    list_display = ("name", "company", "branch", "parent")
    list_filter = ("company", "branch")
    search_fields = ("name",)
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch", "parent")

@admin.register(GlobalProduct)
class GlobalProductAdmin(admin.ModelAdmin):
    list_display = ("name", "barcode", "brand", "category", "created_at")
    search_fields = ("name", "barcode")
    list_filter = ("brand", "category", "created_at")
    list_select_related = ("brand", "category")
    autocomplete_fields = ("brand", "category")

# ========= Клиенты и сделки =========
@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "client_company", "phone", "email", "company", "branch", "owner", "created_at")
    list_filter = ("company", "branch", "department", "created_at")
    search_fields = ("name", "client_company", "phone", "email")
    list_select_related = ("company", "branch", "owner")
    autocomplete_fields = ("company", "branch", "owner")

@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "branch", "owner", "created_at")
    list_filter = ("company", "branch", "created_at")
    search_fields = ("name",)
    list_select_related = ("company", "branch", "owner")
    autocomplete_fields = ("company", "branch", "owner")

@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "branch", "pipeline", "contact", "assigned_to", "value", "status", "stage", "created_at")
    list_filter = ("company", "branch", "status", "pipeline", "assigned_to", "created_at")
    search_fields = ("title", "contact__name")
    list_select_related = ("company", "branch", "pipeline", "contact", "assigned_to")
    autocomplete_fields = ("company", "branch", "pipeline", "contact", "assigned_to")

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "branch", "assigned_to", "deal", "due_date", "status", "created_at")
    list_filter = ("company", "branch", "status", "assigned_to", "due_date")
    search_fields = ("title", "description")
    list_select_related = ("company", "branch", "assigned_to", "deal")
    autocomplete_fields = ("company", "branch", "assigned_to", "deal")

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "type", "status", "company", "branch", "created_at")
    list_filter = ("company", "branch", "status", "type", "created_at")
    search_fields = ("full_name", "phone", "email")
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch", "salesperson", "service")

@admin.register(ClientDeal)
class ClientDealAdmin(admin.ModelAdmin):
    inlines = (DealInstallmentInline,)
    list_display = ("title", "client", "company", "branch", "kind", "amount", "prepayment",
                    "debt_months", "remaining_debt", "created_at")
    list_filter = ("company", "branch", "kind", "created_at")
    search_fields = ("title", "note", "client__full_name")
    list_select_related = ("company", "branch", "client")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("company", "branch", "client")

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

class ItemMakeInline(admin.TabularInline):
    model = Product.item_make.through
    extra = 1
    verbose_name = "Единица товара"
    verbose_name_plural = "Единицы товара"

@admin.register(ItemMake)
class ItemMakeAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "quantity", "price", "company", "branch", "created_at")
    search_fields = ("name",)
    list_filter = ("company", "branch", "unit")
    ordering = ("name",)
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch")

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ("image", "preview", "alt", "is_primary", "created_at")
    readonly_fields = ("preview", "created_at")
    autocomplete_fields = ()  # не нужно, product подтянется от родителя
    can_delete = True

    def preview(self, obj):
        if obj and getattr(obj, "image", None) and getattr(obj.image, "url", None):
            return format_html(
                '<img src="{}" style="height:70px;object-fit:cover;border-radius:6px;" />',
                obj.image.url
            )
        return "—"
    preview.short_description = "Превью"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "primary_image_thumb",  # миниатюра
        "name", "barcode", "company", "branch", "brand", "category", "client",
        "quantity", "purchase_price", "price", "status", "created_at"
    )
    list_filter = ("company", "branch", "brand", "category", "status", "created_at")
    search_fields = ("name", "barcode")
    ordering = ("name",)
    inlines = [ItemMakeInline, ProductImageInline]   # ← добавили инлайн фото
    exclude = ("item_make",)  # используем inline для M2M
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("company", "branch", "brand", "category", "client")
    autocomplete_fields = ("company", "branch", "brand", "category", "client", "created_by")
    actions = (accept_products, reject_products, clear_status)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # префетчим изображения, чтобы миниатюры не били по БД
        return qs.prefetch_related("images")

    def primary_image_thumb(self, obj):
        # показываем основное изображение, если есть; иначе первое
        img = None
        for im in obj.images.all():
            if im.is_primary and getattr(im, "image", None) and getattr(im.image, "url", None):
                img = im
                break
        if img is None:
            for im in obj.images.all():
                if getattr(im, "image", None) and getattr(im.image, "url", None):
                    img = im
                    break
        if img:
            return format_html(
                '<img src="{}" width="45" style="object-fit:cover;border-radius:6px;" />',
                img.image.url
            )
        return "—"
    primary_image_thumb.short_description = "Фото"
    primary_image_thumb.admin_order_field = "created_at"  # чтобы было сортируемо «как-то»

# (опционально) отдельная админка для ProductImage, если нужно управлять отдельно
@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("product", "is_primary", "alt", "created_at")
    list_filter = ("is_primary", "created_at")
    search_fields = ("product__name", "product__barcode")
    autocomplete_fields = ("product",)

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    inlines = (CartItemInline,)
    list_display = ("id", "company", "branch", "user", "status", "subtotal", "discount_total",
                    "order_discount_total", "tax_total", "total", "updated_at")
    list_filter = ("company", "branch", "status", "updated_at")
    search_fields = ("id", "session_key", "user__email")
    list_select_related = ("company", "branch", "user")
    readonly_fields = ("subtotal", "discount_total", "order_discount_total", "tax_total",
                       "total", "created_at", "updated_at")
    autocomplete_fields = ("company", "branch", "user")

@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("cart", "product", "custom_name", "company", "branch", "quantity", "unit_price")
    list_filter = ("company", "branch")
    search_fields = ("cart__id", "product__name", "product__barcode", "custom_name")
    list_select_related = ("cart", "product", "company", "branch")
    autocomplete_fields = ("cart", "product", "company", "branch")

@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    inlines = (SaleItemInline,)
    list_display = ("id", "company", "branch", "user", "client", "status", "subtotal",
                    "discount_total", "tax_total", "total", "created_at", "paid_at")
    list_filter = ("company", "branch", "status", "created_at", "paid_at")
    search_fields = ("id", "client__full_name", "user__email")
    list_select_related = ("company", "branch", "user", "client")
    readonly_fields = ("created_at", "paid_at", "subtotal", "discount_total", "tax_total", "total")
    autocomplete_fields = ("company", "branch", "user", "client")

    @admin.action(description="Отметить как оплаченные")
    def mark_paid_action(self, request, queryset):
        for sale in queryset:
            sale.mark_paid()

    actions = ("mark_paid_action",)

@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ("sale", "product", "name_snapshot", "barcode_snapshot", "quantity", "unit_price", "company", "branch")
    list_filter = ("company", "branch")
    search_fields = ("sale__id", "product__name", "name_snapshot", "barcode_snapshot")
    list_select_related = ("sale", "product", "company", "branch")
    autocomplete_fields = ("sale", "product", "company", "branch")

# ========= Заказы =========
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    inlines = (OrderItemInline,)
    list_display = ("order_number", "company", "branch", "customer_name", "date_ordered",
                    "status", "phone", "department", "total_qty", "total_amount")
    list_filter = ("company", "branch", "status", "date_ordered", "department")
    search_fields = ("order_number", "customer_name", "phone")
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch")

    @admin.display(description="Кол-во")
    def total_qty(self, obj):
        # В модели Order нет total_quantity — посчитаем по инлайнам
        return obj.items.aggregate(s=Sum("quantity"))["s"] or 0

    @admin.display(description="Итого")
    def total_amount(self, obj):
        return obj.total

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "company", "branch", "quantity", "price", "total")
    list_filter = ("company", "branch")
    search_fields = ("order__order_number", "product__name", "product__barcode")
    list_select_related = ("order", "product", "company", "branch")
    autocomplete_fields = ("order", "product", "company", "branch")

# ========= Прочее =========
@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("company", "branch", "user", "rating", "created_at")
    list_filter = ("company", "branch", "rating", "created_at")
    search_fields = ("user__email", "company__name")
    list_select_related = ("company", "branch", "user")
    autocomplete_fields = ("company", "branch", "user")

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("company", "branch", "user", "short_message", "is_read", "created_at")
    list_filter = ("company", "branch", "is_read", "created_at")
    search_fields = ("message", "user__email")
    list_select_related = ("company", "branch", "user")
    autocomplete_fields = ("company", "branch", "user")

    @admin.display(description="Сообщение")
    def short_message(self, obj):
        return (obj.message[:60] + "…") if len(obj.message) > 60 else obj.message

@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ("company", "branch", "type", "status", "created_at", "updated_at")
    list_filter = ("company", "branch", "type", "status", "created_at")
    search_fields = ("company__name",)
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch")

@admin.register(Analytics)
class AnalyticsAdmin(admin.ModelAdmin):
    list_display = ("company", "branch", "type", "metric", "created_at")
    list_filter = ("company", "branch", "type", "created_at")
    search_fields = ("data",)
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch")

    @admin.display(description="Метрика")
    def metric(self, obj):
        return obj.data.get("metric", "")

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "branch", "datetime", "participants_count", "created_at", "updated_at")
    list_filter = ("company", "branch", "datetime")
    search_fields = ("title", "notes")
    filter_horizontal = ("participants",)
    list_select_related = ("company", "branch")

    @admin.display(description="Участники")
    def participants_count(self, obj):
        return obj.participants.count()

@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "branch", "location", "created_at", "updated_at")
    list_filter = ("company", "branch")
    search_fields = ("name", "location")
    list_select_related = ("company", "branch")
    autocomplete_fields = ("company", "branch")

@admin.register(WarehouseEvent)
class WarehouseEventAdmin(admin.ModelAdmin):
    list_display = ("title", "warehouse", "company", "branch", "status", "event_date", "amount", "responsible_person", "created_at")
    list_filter = ("company", "branch", "warehouse", "status", "event_date", "created_at")
    search_fields = ("title", "client_name", "description")
    list_select_related = ("warehouse", "company", "branch", "responsible_person")
    autocomplete_fields = ("warehouse", "company", "branch", "responsible_person")

@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("full_name", "phone", "text")

@admin.register(SocialApplications)
class SocialApplicationsAdmin(admin.ModelAdmin):
    list_display = ("id", "company", "status", "created_at", "text_short")
    list_filter = ("status", "created_at")
    search_fields = ("company", "text")

    @admin.display(description="Текст")
    def text_short(self, obj):
        t = obj.text or ""
        return (t[:60] + "…") if len(t) > 60 else t

@admin.register(TransactionRecord)
class TransactionRecordAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "branch", "department", "amount", "status", "date", "created_at")
    list_filter = ("company", "branch", "status", "department", "date")
    search_fields = ("name", "description")
    list_select_related = ("company", "branch", "department")
    autocomplete_fields = ("company", "branch", "department")

@admin.register(MobileScannerToken)
class MobileScannerTokenAdmin(admin.ModelAdmin):
    list_display = ("token", "company", "branch", "cart", "expires_at", "is_valid_now")
    list_filter = ("company", "branch", "expires_at")
    search_fields = ("token", "cart__id")
    list_select_related = ("company", "branch", "cart")
    autocomplete_fields = ("company", "branch", "cart")

    @admin.display(boolean=True, description="Действителен")
    def is_valid_now(self, obj):
        return now() <= obj.expires_at
