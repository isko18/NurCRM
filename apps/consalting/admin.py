from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import (
    ServicesConsalting,
    TariffConsalting,
    SaleConsalting,
    SaleItemConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting,
    FunnelConsalting,
    FunnelStageConsalting,
    LeadConsalting,
    LossReasonConsalting,
    LeadActivityConsalting,
    StageTransitionConsalting,
    LeadTaskConsalting,
    AutomationRuleConsalting,
    AutomationLogConsalting,
)
from apps.users.models import Company, User


# ========= helpers =========
def get_company_from_user(user):
    """Безопасно получить компанию из пользователя (owner/employee)."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "company", None) or getattr(user, "owned_company", None)


def get_active_branch(request):
    """
    Определяем активный филиал:
      1) user.primary_branch() / user.primary_branch
      2) request.branch (если middleware кладёт)
      3) None (глобальная область)
    """
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        setattr(request, "branch", None)
        return None

    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val:
                setattr(request, "branch", val)
                return val
        except Exception:
            pass
    if primary:
        setattr(request, "branch", primary)
        return primary

    if hasattr(request, "branch"):
        return request.branch

    setattr(request, "branch", None)
    return None


class TimeStampedAdminMixin:
    readonly_fields = ("created_at", "updated_at")


class CompanyBranchScopedAdminMixin:
    """
    Скоуп по company (+ branch при его наличии у модели).
    При сохранении проставляет company и branch из контекста пользователя.
    Ограничивает FK-поля по company и видимости филиала (глобальные или текущий филиал).
    """
    company_field_name = "company"
    branch_field_name = "branch"

    # ---- utils ----
    def _has_field(self, model_cls, name: str) -> bool:
        try:
            return any(f.name == name for f in model_cls._meta.get_fields())
        except Exception:
            return False

    # ---- queryset ----
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        company = get_company_from_user(request.user)
        if not company:
            return qs.none()

        qs = qs.filter(**{self.company_field_name: company})

        # branch-скоуп, если поле есть у модели
        if self._has_field(qs.model, self.branch_field_name):
            active_branch = get_active_branch(request)  # None или Branch
            if active_branch is not None:
                qs = qs.filter(**{self.branch_field_name: active_branch})
            else:
                qs = qs.filter(**{f"{self.branch_field_name}__isnull": True})
        return qs

    # ---- save ----
    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            company = get_company_from_user(request.user)
            if not company:
                raise PermissionDenied("У пользователя не настроена компания.")

            if hasattr(obj, self.company_field_name):
                setattr(obj, self.company_field_name, company)

            if self._has_field(obj.__class__, self.branch_field_name) and getattr(obj, self.branch_field_name, None) is None:
                setattr(obj, self.branch_field_name, get_active_branch(request))
        super().save_model(request, obj, form, change)

    # ---- FK scoping ----
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Сужаем справочники:
        - company — только текущая компания
        - services/client — по company и доступности глобально/в активном филиале
        - employee/user — по company
        """
        if request.user.is_superuser:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        company = get_company_from_user(request.user)
        if not company:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        active_branch = get_active_branch(request)

        # 1) company сам по себе
        if db_field.name == self.company_field_name:
            kwargs["queryset"] = Company.objects.filter(pk=company.pk)

        # 2) branch (если есть) — ограничим теми, что принадлежат компании
        elif db_field.name == self.branch_field_name:
            # импортить Branch тут не обязательно: queryset подхватится из default
            if "queryset" in kwargs:
                kwargs["queryset"] = kwargs["queryset"].filter(company=company)

        # 3) services — по company и branch-глобальности
        elif db_field.name == "services" and db_field.related_model is ServicesConsalting:
            qs = ServicesConsalting.objects.filter(company=company)
            if active_branch is not None:
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=active_branch))
            else:
                qs = qs.filter(branch__isnull=True)
            kwargs["queryset"] = qs

        # 4) employee/user — по company
        elif db_field.name in ("employee", "user") and db_field.related_model is User:
            kwargs["queryset"] = User.objects.filter(company=company)

        # 5) client — если у модели клиента есть company/branch,
        #    ограничим company и глобально/филиал (используем getattr-защиту)
        elif db_field.name == "client":
            qs = kwargs.get("queryset")
            if qs is not None and hasattr(qs.model, "_meta"):
                fields = {f.name for f in qs.model._meta.get_fields()}
                if "company" in fields:
                    qs = qs.filter(company=company)
                if "branch" in fields:
                    if active_branch is not None:
                        qs = qs.filter(Q(branch__isnull=True) | Q(branch=active_branch))
                    else:
                        qs = qs.filter(branch__isnull=True)
                kwargs["queryset"] = qs

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# ========= Services =========
class TariffInline(admin.TabularInline):
    model = TariffConsalting
    extra = 1
    fields = ("name", "price")


@admin.register(ServicesConsalting)
class ServicesConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "branch", "price", "installation_price", "created_at", "updated_at")
    list_filter = ("company", "branch")
    search_fields = ("name", "description")
    raw_id_fields = ("company", "branch")
    ordering = ("name",)
    inlines = [TariffInline]

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro

    def save_formset(self, request, form, formset, change):
        """Проставляем company/branch тарифам из родительской услуги."""
        if formset.model is TariffConsalting:
            instances = formset.save(commit=False)
            service = form.instance
            for obj in instances:
                obj.company_id = service.company_id
                obj.branch_id = service.branch_id
                obj.save()
            for obj in formset.deleted_objects:
                obj.delete()
            formset.save_m2m()
        else:
            super().save_formset(request, form, formset, change)


# ========= Sales =========
class SaleItemInline(admin.TabularInline):
    model = SaleItemConsalting
    extra = 1
    fields = ("name", "price")


@admin.register(SaleConsalting)
class SaleConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("services", "tariff", "company", "branch", "user", "client",
                    "discount", "markup", "total", "short_description", "created_at")
    list_filter = ("company", "branch", "services")
    search_fields = ("description",)
    raw_id_fields = ("company", "branch", "services", "tariff", "client", "user")
    ordering = ("-created_at",)
    inlines = [SaleItemInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # пересчёт итога после сохранения доп. товаров
        form.instance.recalc_total(save=True)

    def short_description(self, obj):
        text = obj.description or ""
        return (text[:60] + "...") if len(text) > 60 else text
    short_description.short_description = _("Заметка")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields) + ["total"]
        if not request.user.is_superuser:
            ro.extend(["company", "branch", "user"])
        return ro


# ========= Salaries =========
@admin.register(SalaryConsalting)
class SalaryConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("company", "branch", "user", "amount", "percent", "created_at")
    list_filter = ("company", "branch", "user")
    search_fields = ("description",)
    raw_id_fields = ("company", "branch", "user")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "branch", "user"])
        return ro


# ========= Requests =========
@admin.register(RequestsConsalting)
class RequestsConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "branch", "client", "status", "created_at")
    list_filter = ("company", "branch", "status")
    search_fields = ("name", "description")
    raw_id_fields = ("company", "branch", "client")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro


# ========= Funnels =========
class FunnelStageInline(admin.TabularInline):
    model = FunnelStageConsalting
    extra = 1
    fields = ("name", "order", "stage_type", "color", "sla_hours", "allow_skip")
    ordering = ("order",)


@admin.register(FunnelConsalting)
class FunnelConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "branch", "is_active", "created_at")
    list_filter = ("company", "branch", "is_active")
    search_fields = ("name", "description")
    raw_id_fields = ("company", "branch")
    ordering = ("-created_at",)
    inlines = [FunnelStageInline]

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro


# ========= Funnel stages =========
@admin.register(FunnelStageConsalting)
class FunnelStageConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "funnel", "order", "stage_type", "company", "branch", "is_final", "is_success")
    list_filter = ("company", "branch", "stage_type")
    search_fields = ("name",)
    raw_id_fields = ("company", "branch", "funnel")
    ordering = ("funnel", "order")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields) + ["is_final", "is_success"]
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro


# ========= Leads =========
@admin.register(LeadConsalting)
class LeadConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("title", "company", "branch", "funnel", "stage", "status",
                    "score_grade", "is_at_risk", "owner", "estimated_value", "next_action_date", "created_at")
    list_filter = ("company", "branch", "funnel", "stage", "status", "score_grade", "is_at_risk", "urgency")
    search_fields = ("title", "description", "full_name", "phone", "email")
    raw_id_fields = ("company", "branch", "funnel", "stage", "client", "owner", "loss_reason")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields) + [
            "score_grade", "score_value", "score_updated_at",
            "is_at_risk", "risk_reason", "last_activity_at", "stage_entered_at",
            "won_at", "lost_at", "completed_at", "first_contact_at",
        ]
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro


# ========= Loss reasons =========
@admin.register(LossReasonConsalting)
class LossReasonConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("label", "code", "company", "is_active", "created_at")
    list_filter = ("company", "is_active")
    search_fields = ("label", "code")
    raw_id_fields = ("company",)
    ordering = ("label",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
        return ro


# ========= Lead activities (read-only audit) =========
@admin.register(LeadActivityConsalting)
class LeadActivityConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("type", "title", "lead", "actor", "company", "branch", "created_at")
    list_filter = ("company", "branch", "type")
    search_fields = ("title", "body")
    raw_id_fields = ("company", "branch", "lead", "actor")
    ordering = ("-created_at",)

    def has_change_permission(self, request, obj=None):
        return False  # лента неизменяема

    def has_add_permission(self, request):
        return False


# ========= Stage transitions (read-only) =========
@admin.register(StageTransitionConsalting)
class StageTransitionConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("lead", "from_type", "to_type", "actor", "automated", "seconds_in_prev", "created_at")
    list_filter = ("company", "branch", "automated", "to_type")
    raw_id_fields = ("company", "branch", "lead", "from_stage", "to_stage", "actor")
    ordering = ("-created_at",)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


# ========= Lead tasks =========
@admin.register(LeadTaskConsalting)
class LeadTaskConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("title", "lead", "type", "status", "due_date", "assignee", "company", "branch", "created_at")
    list_filter = ("company", "branch", "status", "type")
    search_fields = ("title",)
    raw_id_fields = ("company", "branch", "lead", "assignee", "created_by")
    ordering = ("due_date",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro


# ========= Automation rules =========
@admin.register(AutomationRuleConsalting)
class AutomationRuleConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "trigger", "company", "funnel", "is_active", "priority")
    list_filter = ("company", "trigger", "is_active")
    search_fields = ("name",)
    raw_id_fields = ("company", "funnel")
    ordering = ("priority", "name")
    branch_field_name = "__none__"  # у модели нет branch

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.append("company")
        return ro


# ========= Automation logs (read-only) =========
@admin.register(AutomationLogConsalting)
class AutomationLogConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("trigger", "rule", "lead", "matched", "company", "created_at")
    list_filter = ("company", "trigger", "matched")
    raw_id_fields = ("company", "rule", "lead")
    ordering = ("-created_at",)
    branch_field_name = "__none__"

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


# ========= Bookings =========
@admin.register(BookingConsalting)
class BookingConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("title", "company", "branch", "employee", "date", "time", "created_at")
    list_filter = ("company", "branch", "date", "employee")
    search_fields = ("title", "note")
    raw_id_fields = ("company", "branch", "employee")
    ordering = ("-date", "time")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro
