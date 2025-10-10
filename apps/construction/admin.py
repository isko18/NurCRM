from django.contrib import admin
from django.db.models import Q
from django.core.exceptions import PermissionDenied

from apps.construction.models import Department, Cashbox, CashFlow
from apps.users.models import Company
# from apps.users.utils import get_active_branch_for_request  # если нет — см. примечание ниже


# ── helpers ─────────────────────────────────────────────────────────
def _user_company(user):
    if not user or user.is_anonymous:
        return None
    return getattr(user, "company", None) or getattr(user, "owned_company", None)

def _active_branch(request):
    """
    Активный филиал определяем локально:
      1) user.primary_branch() / .primary_branch
      2) request.branch (если мидлварь проставила)
      3) иначе None (глобальный)
    """
    user = getattr(request, "user", None)
    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val:
                return val
        except Exception:
            pass
    if primary:
        return primary
    if hasattr(request, "branch"):
        return request.branch
    return None

class CompanyBranchScopedAdminMixin:
    """Скоуп по компании + (глобальные|мой филиал)."""
    company_field_name = "company"
    branch_field_name = "branch"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        company = _user_company(request.user)
        if not company:
            return qs.none()
        # company строго
        qs = qs.filter(**{self.company_field_name: company})
        # если у модели есть branch — «глобальные или мой филиал»
        model = qs.model
        has_branch = any(f.name == self.branch_field_name for f in model._meta.get_fields())
        if has_branch:
            br = _active_branch(request)
            if br is not None:
                qs = qs.filter(Q(**{f"{self.branch_field_name}__isnull": True}) | Q(**{self.branch_field_name: br}))
            else:
                qs = qs.filter(**{f"{self.branch_field_name}__isnull": True})
        return qs

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            company = _user_company(request.user)
            if not company:
                raise PermissionDenied("У пользователя не настроена компания.")
            # company всегда из контекста
            if hasattr(obj, self.company_field_name):
                setattr(obj, self.company_field_name, company)
            # branch: унаследуем активный; если None — пусть будет глобальный
            if hasattr(obj, self.branch_field_name):
                br = _active_branch(request)
                setattr(obj, self.branch_field_name, br)
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Ограничиваем FK списки по company и branch:
         - если есть активный филиал → показываем глобальные (NULL) и этого филиала
         - иначе → только глобальные
        """
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if request.user.is_superuser:
            return ff

        company = _user_company(request.user)
        if not company or not hasattr(ff, "queryset") or ff.queryset is None:
            return ff

        br = _active_branch(request)

        def scope_qs(qs):
            model = qs.model
            has_branch = any(f.name == "branch" for f in model._meta.get_fields())
            qs = qs.filter(company=company)
            if has_branch:
                if br is not None:
                    qs = qs.filter(Q(branch__isnull=True) | Q(branch=br))
                else:
                    qs = qs.filter(branch__isnull=True)
            return qs

        try:
            kwargs_qs = kwargs.get("queryset", ff.queryset)
            ff.queryset = scope_qs(kwargs_qs)
        except Exception:
            # если что-то пошло не так — оставляем как есть
            pass
        return ff


# ── Department ─────────────────────────────────────────────────────
@admin.register(Department)
class DepartmentAdmin(CompanyBranchScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "branch", "employee_count", "created_at")
    list_filter = ("company", "branch")
    search_fields = ("name", "company__name")
    filter_horizontal = ("employees",)
    readonly_fields = ()  # при желании: ("created_at",)

    def employee_count(self, obj):
        return obj.employees.count()
    employee_count.short_description = "Кол-во сотрудников"


# ── Cashbox ───────────────────────────────────────────────────────
@admin.register(Cashbox)
class CashboxAdmin(CompanyBranchScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "branch", "department")
    list_filter = ("company", "branch", "department")
    search_fields = ("name", "department__name")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # унаследованный скоуп + небольшая донастройка для department
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if not request.user.is_superuser and db_field.name == "department" and hasattr(ff, "queryset"):
            company = _user_company(request.user)
            br = _active_branch(request)
            qs = ff.queryset.filter(company=company)
            if br is not None:
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=br))
            else:
                qs = qs.filter(branch__isnull=True)
            ff.queryset = qs
        return ff


# ── CashFlow ──────────────────────────────────────────────────────
@admin.register(CashFlow)
class CashFlowAdmin(CompanyBranchScopedAdminMixin, admin.ModelAdmin):
    list_display = ("cashbox", "company", "branch", "type", "name", "amount", "created_at")
    list_filter = ("company", "branch", "type", "created_at")
    search_fields = ("name", "cashbox__name", "cashbox__department__name")
    date_hierarchy = "created_at"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # ограничим выбор кассы по company/branch
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if not request.user.is_superuser and db_field.name == "cashbox" and hasattr(ff, "queryset"):
            company = _user_company(request.user)
            br = _active_branch(request)
            qs = ff.queryset.filter(company=company)
            if br is not None:
                qs = qs.filter(Q(branch__isnull=True) | Q(branch=br))
            else:
                qs = qs.filter(branch__isnull=True)
            ff.queryset = qs
        return ff
