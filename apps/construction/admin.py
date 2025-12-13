# apps/construction/admin.py
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.users.models import Branch


# ── helpers ─────────────────────────────────────────────────────────
def _user_company(user):
    if not user or getattr(user, "is_anonymous", False):
        return None
    return getattr(user, "company", None) or getattr(user, "owned_company", None)


def _is_owner_like(user) -> bool:
    if not user or getattr(user, "is_anonymous", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "owned_company", None):
        return True
    if getattr(user, "is_admin", False):
        return True
    role = getattr(user, "role", None)
    return role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор")


def _active_branch(request):
    """
    Активный филиал:
      1) user.primary_branch() / .primary_branch
      2) request.branch (если мидлварь проставила)
      3) иначе None
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


# ── base mixin ─────────────────────────────────────────────────────
class CompanyBranchScopedAdminMixin:
    """
    Скоуп по company всегда.
    Для НЕ owner-like:
      - если у модели есть branch → видит (global NULL) + свой филиал,
        если филиала нет → только global (NULL).
    Для owner-like:
      - видит все филиалы компании.
    """
    company_field_name = "company"
    branch_field_name = "branch"

    def _model_has_field(self, model, field_name: str) -> bool:
        try:
            return any(f.name == field_name for f in model._meta.get_fields())
        except Exception:
            return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        company = _user_company(request.user)
        if not company:
            return qs.none()

        # company строго
        if self._model_has_field(qs.model, self.company_field_name):
            qs = qs.filter(**{self.company_field_name: company})

        # branch scope
        if self._model_has_field(qs.model, self.branch_field_name):
            if _is_owner_like(request.user):
                return qs
            br = _active_branch(request)
            if br is not None:
                qs = qs.filter(
                    Q(**{f"{self.branch_field_name}__isnull": True})
                    | Q(**{self.branch_field_name: br})
                )
            else:
                qs = qs.filter(**{f"{self.branch_field_name}__isnull": True})

        return qs

    def save_model(self, request, obj, form, change):
        if request.user.is_superuser:
            return super().save_model(request, obj, form, change)

        company = _user_company(request.user)
        if not company:
            raise PermissionDenied("У пользователя не настроена компания.")

        # company всегда из контекста
        if hasattr(obj, self.company_field_name):
            setattr(obj, self.company_field_name, company)

        # branch:
        #  - НЕ owner-like → насильно ставим активный (или None → global)
        #  - owner-like → оставляем выбранный в форме, но проверим что филиал этой компании
        if hasattr(obj, self.branch_field_name):
            if _is_owner_like(request.user):
                br = getattr(obj, self.branch_field_name, None)
                if br is not None and getattr(br, "company_id", None) != getattr(company, "id", None):
                    raise PermissionDenied("Филиал принадлежит другой компании.")
            else:
                setattr(obj, self.branch_field_name, _active_branch(request))

        return super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Ограничиваем FK списки по company и branch.
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

            # company
            if self._model_has_field(model, "company"):
                qs = qs.filter(company=company)

            # branch (если есть)
            if self._model_has_field(model, "branch"):
                if _is_owner_like(request.user):
                    return qs
                if br is not None:
                    qs = qs.filter(Q(branch__isnull=True) | Q(branch=br))
                else:
                    qs = qs.filter(branch__isnull=True)

            return qs

        try:
            base_qs = kwargs.get("queryset", ff.queryset)
            ff.queryset = scope_qs(base_qs)
        except Exception:
            pass

        return ff


# ── Cashbox ───────────────────────────────────────────────────────
@admin.register(Cashbox)
class CashboxAdmin(CompanyBranchScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "company", "branch", "is_consumption")
    list_filter = ("company", "branch", "is_consumption")
    search_fields = ("name", "company__name", "branch__name")
    autocomplete_fields = ("company", "branch")
    ordering = ("name",)


# ── CashFlow ──────────────────────────────────────────────────────
@admin.register(CashFlow)
class CashFlowAdmin(CompanyBranchScopedAdminMixin, admin.ModelAdmin):
    list_display = ("cashbox", "company", "branch", "type", "name", "amount", "created_at")
    list_filter = ("company", "branch", "type", "created_at")
    search_fields = ("name", "cashbox__name")
    date_hierarchy = "created_at"
    autocomplete_fields = ("company", "branch", "cashbox")
    ordering = ("-created_at",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # ограничим выбор кассы по company/branch
        ff = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if request.user.is_superuser:
            return ff

        if db_field.name == "cashbox" and hasattr(ff, "queryset") and ff.queryset is not None:
            company = _user_company(request.user)
            if company:
                qs = ff.queryset.filter(company=company)

                if not _is_owner_like(request.user):
                    br = _active_branch(request)
                    if br is not None:
                        qs = qs.filter(Q(branch__isnull=True) | Q(branch=br))
                    else:
                        qs = qs.filter(branch__isnull=True)

                ff.queryset = qs

        return ff


# ── CashShift ──────────────────────────────────────────────────────
@admin.register(CashShift)
class CashShiftAdmin(admin.ModelAdmin):
    """
    Смены лучше показывать:
      - owner/admin: все смены компании
      - кассир: только свои смены
    """
    list_display = (
        "id",
        "status",
        "company",
        "branch",
        "cashbox",
        "cashier",
        "opened_at",
        "closed_at",
        "opening_cash",
        "closing_cash",
        "sales_count",
        "sales_total",
        "income_total",
        "expense_total",
        "expected_cash",
        "cash_diff",
    )
    list_filter = ("status", "company", "branch", "cashbox", "cashier", "opened_at", "closed_at")
    search_fields = ("id", "cashbox__name", "cashier__username", "cashier__email")
    date_hierarchy = "opened_at"
    ordering = ("-opened_at",)

    autocomplete_fields = ("company", "branch", "cashbox", "cashier")

    readonly_fields = (
        "company",
        "branch",
        "cashbox",
        "cashier",
        "status",
        "opened_at",
        "closed_at",
        "opening_cash",
        "closing_cash",
        "income_total",
        "expense_total",
        "sales_count",
        "sales_total",
        "cash_sales_total",
        "noncash_sales_total",
    )

    actions = ("recalc_selected_shifts",)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("company", "branch", "cashbox", "cashier")

        if request.user.is_superuser:
            return qs

        company = _user_company(request.user)
        if not company:
            return qs.none()

        qs = qs.filter(company=company)

        if _is_owner_like(request.user):
            return qs

        # обычный кассир: только свои смены
        return qs.filter(cashier=request.user)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return True

        company = _user_company(request.user)
        if not company or obj.company_id != company.id:
            return False

        if _is_owner_like(request.user):
            return True

        # кассир может смотреть/менять только свои (и то мы readonly почти всё сделали)
        return obj.cashier_id == request.user.id

    def recalc_selected_shifts(self, request, queryset):
        """
        Пересчитать агрегаты (если у модели есть recalc_totals()).
        """
        ok, skipped = 0, 0
        for shift in queryset:
            fn = getattr(shift, "recalc_totals", None)
            if callable(fn):
                try:
                    fn()
                    ok += 1
                except Exception:
                    skipped += 1
            else:
                skipped += 1

        self.message_user(request, f"Пересчитано: {ok}, пропущено: {skipped}")

    recalc_selected_shifts.short_description = "Пересчитать итоги по сменам"
