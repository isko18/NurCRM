# apps/users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db import models as dj_models
from django.db.models import Prefetch

from .models import (
    User,
    Company,
    Branch,
    BranchMembership,
    Feature,
    SubscriptionPlan,
    Sector,
    Industry,
    CustomRole,
    ScaleDevice,
)


class CompanyScopedFKMixin:
    """
    Ограничивает ForeignKey(company) выбором компании текущего пользователя, если он не суперпользователь.
    Также ограничивает связанные user/branch тем же контекстом компании.
    """

    def _get_user_company(self, request):
        # Если юзер = владелец компании: owned_company
        # Если сотрудник: company
        return getattr(request.user, "company", None) or getattr(request.user, "owned_company", None)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            user_company = self._get_user_company(request)

            if db_field.name == "company":
                kwargs["queryset"] = Company.objects.filter(id=user_company.id) if user_company else Company.objects.none()

            if db_field.name == "user":
                kwargs["queryset"] = User.objects.filter(company=user_company) if user_company else User.objects.none()

            if db_field.name == "branch":
                kwargs["queryset"] = Branch.objects.filter(company=user_company) if user_company else Branch.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# -------------------- Константы групп прав пользователя --------------------

BASE_PERMS = (
    "can_view_dashboard",
    "can_view_cashbox",
    "can_view_departments",
    "can_view_orders",
    "can_view_analytics",
    "can_view_department_analytics",
    "can_view_products",
    "can_view_booking",
    "can_view_employees",
    "can_view_clients",
    "can_view_brand_category",
    "can_view_settings",
    "can_view_sale",
    "can_view_document",
    "can_view_market_scales",
    "can_view_market_procurement",
    "can_view_market_supplier",
    "can_view_market_discount",
    "can_view_market_edit_price",
    "can_view_market_delete_cart_item",
    "can_view_market_employee_return",
)

BUILDING_PERMS = (
    "can_view_building_analytics",
    "can_view_building_cash_register",
    "can_view_building_clients",
    "can_view_building_department",
    "can_view_building_employess",
    "can_view_building_notification",
    "can_view_building_procurement",
    "can_view_building_projects",
    "can_view_building_salary",
    "can_view_building_sell",
    "can_view_building_stock",
    "can_view_building_treaty",
    "can_view_building_work_process",
    "can_view_building_objects",
    "can_view_additional_services",
    "can_view_debts",
)

BARBER_PERMS = (
    "can_view_barber_clients",
    "can_view_barber_services",
    "can_view_barber_history",
    "can_view_barber_records",
)

HOSTEL_PERMS = (
    "can_view_hostel_rooms",
    "can_view_hostel_booking",
    "can_view_hostel_clients",
    "can_view_hostel_analytics",
)

CAFE_PERMS = (
    "can_view_cafe_menu",
    "can_view_cafe_orders",
    "can_view_cafe_purchasing",
    "can_view_cafe_booking",
    "can_view_cafe_clients",
    "can_view_cafe_tables",
    "can_view_cafe_cook",
    "can_view_cafe_inventory",
    "can_view_cafe_calculation",
    "can_view_cafe_order_pay",
    "can_view_cafe_order_return",
)

SCHOOL_PERMS = (
    "can_view_school_students",
    "can_view_school_groups",
    "can_view_school_lessons",
    "can_view_school_teachers",
    "can_view_school_leads",
    "can_view_school_invoices",
)

EXTRA_PERMS = (
    "can_view_client_requests",
    "can_view_salary",
    "can_view_sales",
    "can_view_services",
    "can_view_agent",
    "can_view_catalog",
    "can_view_branch",
    "can_view_logistics",
    "can_view_request",
    "can_view_shifts",
    "can_view_cashier",
    "can_view_market_label",
)


# -------------------- Inlines --------------------

class BranchMembershipInlineForUser(admin.TabularInline):
    model = BranchMembership
    fk_name = "user"
    extra = 0
    autocomplete_fields = ("branch",)
    fields = ("branch", "role", "is_primary", "created_at")
    readonly_fields = ("created_at",)


class BranchMembershipInlineForBranch(admin.TabularInline):
    model = BranchMembership
    fk_name = "branch"
    extra = 0
    autocomplete_fields = ("user",)
    fields = ("user", "role", "is_primary", "created_at")
    readonly_fields = ("created_at",)


# -------------------- User --------------------

@admin.register(User)
class UserAdmin(CompanyScopedFKMixin, BaseUserAdmin):
    list_display = (
        "email",
        "first_name",
        "last_name",
        "company",
        "role_display",
        "custom_role",
        "primary_branch_display",
        "branches_display",
        "can_view_cashbox",
        "can_view_orders",
        "can_view_clients",
        "can_view_settings",
        "can_view_sale",
        "can_view_market_discount",
        "can_view_market_edit_price",
        "can_view_market_delete_cart_item",
        "can_view_market_employee_return",
        "is_staff",
        "is_active",
    )
    list_filter = ("role", "custom_role", "company", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name", "phone_number", "track_number")
    ordering = ("email",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [BranchMembershipInlineForUser]
    autocomplete_fields = ("company", "custom_role")
    list_select_related = ("company", "custom_role")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related(
            Prefetch(
                "branch_memberships",
                queryset=BranchMembership.objects.select_related("branch"),
            ),
            "branches",
        )

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Персональная информация",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "avatar",
                    "phone_number",
                    "track_number",
                    "company",
                    "role",
                    "custom_role",
                )
            },
        ),
        ("Разрешения — базовые", {"fields": BASE_PERMS}),
        ("Разрешения — строительство", {"fields": BUILDING_PERMS}),
        ("Разрешения — барбершоп", {"fields": BARBER_PERMS}),
        ("Разрешения — хостел", {"fields": HOSTEL_PERMS}),
        ("Разрешения — кафе", {"fields": CAFE_PERMS}),
        ("Разрешения — школа", {"fields": SCHOOL_PERMS}),
        ("Разрешения — прочие", {"fields": EXTRA_PERMS}),
        (
            "Права доступа",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Даты", {"fields": ("last_login", "created_at", "updated_at")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "avatar",
                    "phone_number",
                    "track_number",
                    "company",
                    "role",
                    "custom_role",
                ),
            },
        ),
        ("Разрешения — базовые", {"fields": BASE_PERMS}),
        ("Разрешения — строительство", {"fields": BUILDING_PERMS}),
        ("Разрешения — барбершоп", {"fields": BARBER_PERMS}),
        ("Разрешения — хостел", {"fields": HOSTEL_PERMS}),
        ("Разрешения — кафе", {"fields": CAFE_PERMS}),
        ("Разрешения — школа", {"fields": SCHOOL_PERMS}),
        ("Разрешения — прочие", {"fields": EXTRA_PERMS}),
        (
            "Права доступа",
            {"fields": ("is_staff", "is_superuser", "is_active", "groups", "user_permissions")},
        ),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is not None and "password" in form.base_fields:
            form.base_fields["password"].required = False
        return form

    def save_model(self, request, obj, form, change):
        # Для owner/admin включаем все can_* флаги автоматически
        if obj.role in ["owner", "admin"]:
            for f in obj._meta.get_fields():
                if isinstance(f, dj_models.BooleanField) and f.name.startswith("can_"):
                    setattr(obj, f.name, True)
        super().save_model(request, obj, form, change)

    def primary_branch_display(self, obj):
        for mb in obj.branch_memberships.all():
            if mb.is_primary:
                return mb.branch.name if mb.branch else "-"
        return "-"

    primary_branch_display.short_description = "Основной филиал"

    def branches_display(self, obj):
        all_branches = list(obj.branches.all())
        if not all_branches:
            return "-"
        names = [b.name for b in all_branches[:5]]
        result = ", ".join(names)
        total = len(all_branches)
        if total > 5:
            result += f" и ещё {total - 5}"
        return result

    branches_display.short_description = "Филиалы"


# -------------------- Company --------------------

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "owner",
        "phone",
        "phones_howcase",
        "whatsapp_phone",
        "subscription_plan",
        "industry",
        "sector",
        "region",
        "start_date",
        "end_date",
        "can_view_documents",
        "can_view_whatsapp",
        "can_view_instagram",
        "can_view_telegram",
        "can_view_showcase",
        "created_at",
    )
    list_filter = (
        "subscription_plan",
        "industry",
        "sector",
        "can_view_documents",
        "can_view_whatsapp",
        "can_view_instagram",
        "can_view_telegram",
        "can_view_showcase",
    )
    search_fields = ("name", "slug", "owner__email", "phone", "phones_howcase", "whatsapp_phone", "llc", "inn", "address")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("owner", "subscription_plan", "industry", "sector")
    actions = ["delete_selected_cascade"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if "delete_selected" in actions:
            del actions["delete_selected"]
        return actions

    @admin.action(description="Удалить выбранные компании (каскадно)")
    def delete_selected_cascade(self, request, queryset):
        from django.contrib import messages
        count = 0
        for company in queryset:
            try:
                delete_company_cascade(company)
                count += 1
            except Exception as e:
                self.message_user(request, f"Ошибка при удалении компании {company.name}: {e}", level=messages.ERROR)
        if count > 0:
            self.message_user(request, f"Успешно удалено компаний: {count}.", level=messages.SUCCESS)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "phone",
                    "phones_howcase",
                    "whatsapp_phone",
                    "owner",
                    "subscription_plan",
                    "industry",
                    "sector",
                    "region",
                )
            },
        ),
        (
            "Юр./банковские данные",
            {"fields": ("llc", "inn", "okpo", "score", "bik", "address")},
        ),
        (
            "Доступы к каналам",
            {
                "fields": (
                    "can_view_documents",
                    "can_view_whatsapp",
                    "can_view_instagram",
                    "can_view_telegram",
                    "can_view_showcase",
                )
            },
        ),
        ("Срок действия", {"fields": ("start_date", "end_date", "created_at")}),
    )

    def delete_model(self, request, obj):
        delete_company_cascade(obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            delete_company_cascade(obj)

    def delete_view(self, request, object_id, extra_context=None):
        from django.contrib.admin.utils import unquote
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        from django.contrib import messages
        from django.shortcuts import render

        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            return HttpResponseRedirect(reverse("admin:users_company_changelist"))

        if request.method == "POST":
            company_name = obj.name
            try:
                delete_company_cascade(obj)
                self.message_user(
                    request,
                    f"Компания '{company_name}' и все привязанные данные успешно удалены.",
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(reverse("admin:users_company_changelist"))
            except Exception as exc:
                self.message_user(
                    request,
                    f"Ошибка при удалении компании: {exc}",
                    level=messages.ERROR,
                )
                return HttpResponseRedirect(request.path)

        context = {
            **self.admin_site.each_context(request),
            "object_name": self.model._meta.verbose_name,
            "object": obj,
            "opts": self.model._meta,
            "app_label": self.model._meta.app_label,
            "title": f"Удалить компанию {obj.name}?",
            **(extra_context or {}),
        }
        return render(request, "admin/users/company/delete_confirmation.html", context)


def delete_company_cascade(company):
    """
    Безопасное каскадное удаление компании и всех связанных данных со всех модулей CRM.
    Обходит ограничения Foreign Key (on_delete=models.PROTECT).
    """
    from django.db import transaction, connection

    with transaction.atomic():
        company_id = str(company.id)

        with connection.cursor() as cursor:
            tables_sql = [
                # 1. Сначала запросы одобрения (ссылаются на document и money_document)
                "DELETE FROM warehouse_cashapprovalrequest WHERE money_document_id IN (SELECT id FROM warehouse_moneydocument WHERE company_id = %s)",
                "DELETE FROM warehouse_cashapprovalrequest WHERE document_id IN (SELECT id FROM warehouse_document WHERE warehouse_from_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s) OR warehouse_to_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s))",
                
                # 2. Складские движения (ссылаются на document и warehouse)
                "DELETE FROM warehouse_stockmove WHERE document_id IN (SELECT id FROM warehouse_document WHERE warehouse_from_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s) OR warehouse_to_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s))",
                "DELETE FROM warehouse_stockmove WHERE warehouse_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s)",
                "DELETE FROM warehouse_agentstockmove WHERE document_id IN (SELECT id FROM warehouse_document WHERE warehouse_from_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s) OR warehouse_to_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s))",
                "DELETE FROM warehouse_agentstockmove WHERE warehouse_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s)",
                "DELETE FROM warehouse_stockbalance WHERE warehouse_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s)",
                "DELETE FROM warehouse_agentstockbalance WHERE warehouse_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s)",

                # 3. Элементы документов и сами документы
                "DELETE FROM warehouse_documentitem WHERE document_id IN (SELECT id FROM warehouse_document WHERE warehouse_from_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s) OR warehouse_to_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s))",
                "DELETE FROM warehouse_document WHERE warehouse_from_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s) OR warehouse_to_id IN (SELECT id FROM warehouse_warehouse WHERE company_id = %s)",
                "DELETE FROM warehouse_moneydocument WHERE company_id = %s",

                # 4. Модуль main (POS, розница, кассы, смены, корзины, продажи)
                "DELETE FROM main_saleitem WHERE sale_id IN (SELECT id FROM main_sale WHERE company_id = %s)",
                "DELETE FROM main_sale WHERE company_id = %s",
                "DELETE FROM main_cartitem WHERE cart_id IN (SELECT id FROM main_cart WHERE company_id = %s)",
                "DELETE FROM main_cart WHERE company_id = %s",
                "DELETE FROM main_cashshift WHERE company_id = %s",
                "DELETE FROM main_cashbox WHERE company_id = %s",
                "DELETE FROM main_product WHERE company_id = %s",
                "DELETE FROM main_category WHERE company_id = %s",
                "DELETE FROM main_brand WHERE company_id = %s",
                "DELETE FROM main_client WHERE company_id = %s",
                "DELETE FROM main_shiftreceipt WHERE company_id = %s",

                # 5. Товары и склады
                "DELETE FROM warehouse_warehouseproductalternatebarcode WHERE product_id IN (SELECT id FROM warehouse_warehouseproduct WHERE company_id = %s)",
                "DELETE FROM warehouse_warehouseproductcharasteristics WHERE company_id = %s",
                "DELETE FROM warehouse_warehouseproductpackage WHERE product_id IN (SELECT id FROM warehouse_warehouseproduct WHERE company_id = %s)",
                "DELETE FROM warehouse_warehouseproductimage WHERE product_id IN (SELECT id FROM warehouse_warehouseproduct WHERE company_id = %s)",
                "DELETE FROM warehouse_warehouseproduct WHERE company_id = %s",
                "DELETE FROM warehouse_warehouseproductbrand WHERE company_id = %s",
                "DELETE FROM warehouse_warehouseproductcategory WHERE company_id = %s",
                "DELETE FROM warehouse_warehouseproductgroup WHERE company_id = %s",
                "DELETE FROM warehouse_warehouse WHERE company_id = %s",
                "DELETE FROM warehouse_cashregister WHERE company_id = %s",
                "DELETE FROM warehouse_paymentcategory WHERE company_id = %s",
                "DELETE FROM warehouse_counterparty WHERE company_id = %s",
                "DELETE FROM warehouse_agentrequestitem WHERE cart_id IN (SELECT id FROM warehouse_agentcart WHERE company_id = %s)",
                "DELETE FROM warehouse_agentcart WHERE company_id = %s",

                # 6. Кафе
                "DELETE FROM cafe_orderitem WHERE order_id IN (SELECT id FROM cafe_order WHERE company_id = %s)",
                "DELETE FROM cafe_order WHERE company_id = %s",
                "DELETE FROM cafe_table WHERE company_id = %s",
                "DELETE FROM cafe_menuitem WHERE company_id = %s",
                "DELETE FROM cafe_category WHERE company_id = %s",
                "DELETE FROM cafe_cafeshift WHERE company_id = %s",

                # 7. Застройщики и Здания
                "DELETE FROM construction_cashflow WHERE company_id = %s",
                "DELETE FROM construction_cashbox WHERE company_id = %s",
                "DELETE FROM construction_shiftitem WHERE company_id = %s",
                "DELETE FROM construction_shift WHERE company_id = %s",
                "DELETE FROM construction_clientpayment WHERE company_id = %s",
                "DELETE FROM construction_clientoffer WHERE company_id = %s",
                "DELETE FROM construction_supplierinvoice WHERE company_id = %s",
                "DELETE FROM building_apartmentpayment WHERE company_id = %s",
                "DELETE FROM building_apartmentcontract WHERE company_id = %s",
                "DELETE FROM building_apartment WHERE object_id IN (SELECT id FROM building_constructionobject WHERE company_id = %s)",
                "DELETE FROM building_objectcost WHERE company_id = %s",
                "DELETE FROM building_constructionobject WHERE company_id = %s",

                # 8. Барбер
                "DELETE FROM barber_appointment WHERE company_id = %s",
                "DELETE FROM barber_mastersalarypayout WHERE company_id = %s",
                "DELETE FROM barber_mastersalaryaccrual WHERE company_id = %s",
                "DELETE FROM barber_servicesalaryrate WHERE company_id = %s",
                "DELETE FROM barber_barberprofile WHERE company_id = %s",
                "DELETE FROM barber_service WHERE company_id = %s",
                "DELETE FROM barber_servicecategory WHERE company_id = %s",
                "DELETE FROM barber_client WHERE company_id = %s",

                # 9. Пользователи и Филиалы
                "DELETE FROM users_branchmembership WHERE branch_id IN (SELECT id FROM users_branch WHERE company_id = %s)",
                "DELETE FROM users_branch WHERE company_id = %s",
                "DELETE FROM users_user WHERE company_id = %s",
            ]

            for query in tables_sql:
                sid = transaction.savepoint()
                try:
                    num_params = query.count('%s')
                    params = [company_id] * num_params
                    cursor.execute(query, params)
                    transaction.savepoint_commit(sid)
                except Exception:
                    transaction.savepoint_rollback(sid)

        company.delete()


# -------------------- Branch --------------------

@admin.register(Branch)
class BranchAdmin(CompanyScopedFKMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "code",
        "subscription_plan",
        "is_active",
        "created_at",
        "updated_at",
        "features_display",
    )
    list_filter = ("company", "subscription_plan", "is_active")
    search_fields = ("name", "code", "company__name", "email")
    readonly_fields = ("created_at", "updated_at")
    inlines = [BranchMembershipInlineForBranch]
    autocomplete_fields = ("company", "subscription_plan")
    filter_horizontal = ("features",)

    def features_display(self, obj):
        names = list(obj.features.values_list("name", flat=True)[:5])
        total = obj.features.count()
        if not names:
            return "-"
        return ", ".join(names) + (f" и ещё {total - 5}" if total > 5 else "")

    features_display.short_description = "Функции"


# -------------------- BranchMembership --------------------

@admin.register(BranchMembership)
class BranchMembershipAdmin(CompanyScopedFKMixin, admin.ModelAdmin):
    list_display = ("user", "branch", "role", "is_primary", "created_at")
    list_filter = ("is_primary", "branch__company")
    search_fields = ("user__email", "user__first_name", "user__last_name", "branch__name", "role")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("user", "branch")


# -------------------- Feature / SubscriptionPlan --------------------

@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("name", "description_short")
    search_fields = ("name",)

    def description_short(self, obj):
        if not obj.description:
            return "-"
        return (obj.description[:80] + "…") if len(obj.description) > 80 else obj.description

    description_short.short_description = "Описание"


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "features_display")
    search_fields = ("name",)
    filter_horizontal = ("features",)

    def features_display(self, obj):
        names = list(obj.features.values_list("name", flat=True)[:5])
        total = obj.features.count()
        if not names:
            return "-"
        return ", ".join(names) + (f" и ещё {total - 5}" if total > 5 else "")

    features_display.short_description = "Функции"


# -------------------- Sector / Industry --------------------

@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Industry)
class IndustryAdmin(admin.ModelAdmin):
    list_display = ("name", "sectors_display")
    search_fields = ("name", "sectors__name")
    filter_horizontal = ("sectors",)

    def sectors_display(self, obj):
        names = list(obj.sectors.values_list("name", flat=True)[:5])
        total = obj.sectors.count()
        if not names:
            return "-"
        return ", ".join(names) + (f" и ещё {total - 5}" if total > 5 else "")

    sectors_display.short_description = "Отрасли"


# -------------------- CustomRole --------------------

@admin.register(CustomRole)
class CustomRoleAdmin(CompanyScopedFKMixin, admin.ModelAdmin):
    list_display = ("name", "company")
    list_filter = ("company",)
    search_fields = ("name", "company__name")
    autocomplete_fields = ("company",)


# -------------------- ScaleDevice --------------------

@admin.register(ScaleDevice)
class ScaleDeviceAdmin(CompanyScopedFKMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "branch",
        "ip_address",
        "is_active",
        "last_seen_at",
        "products_last_sync_at",
        "created_at",
    )
    list_filter = ("company", "branch", "is_active")
    search_fields = ("name", "ip_address", "company__name", "branch__name")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("company", "branch")
