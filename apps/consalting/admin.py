from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import (
    ServicesConsalting,
    SaleConsalting,
    SalaryConsalting,
    RequestsConsalting,
    BookingConsalting,
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
@admin.register(ServicesConsalting)
class ServicesConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "branch", "price", "created_at", "updated_at")
    list_filter = ("company", "branch")
    search_fields = ("name", "description")
    raw_id_fields = ("company", "branch")
    ordering = ("name",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
        if not request.user.is_superuser:
            ro.extend(["company", "branch"])
        return ro


# ========= Sales =========
@admin.register(SaleConsalting)
class SaleConsaltingAdmin(CompanyBranchScopedAdminMixin, TimeStampedAdminMixin, admin.ModelAdmin):
    list_display = ("services", "company", "branch", "user", "client", "short_description", "created_at")
    list_filter = ("company", "branch", "services")
    search_fields = ("description",)
    raw_id_fields = ("company", "branch", "services", "client", "user")
    ordering = ("-created_at",)

    def short_description(self, obj):
        text = obj.description or ""
        return (text[:60] + "...") if len(text) > 60 else text
    short_description.short_description = _("Заметка")

    def get_readonly_fields(self, request, obj=None):
        ro = list(self.readonly_fields)
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
