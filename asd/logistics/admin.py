# apps/logistics/admin.py

from django.contrib import admin

from .models import Logistics
from apps.users.models import Company, Branch
from apps.main.models import Client
from apps.main.views import _get_company  # там же, где и CompanyBranchRestrictedMixin


@admin.register(Logistics)
class LogisticsAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "company",
        "branch",
        "client",
        "status",
        "price_car",
        "price_service",
        "arrival_date",
        "created_at",
        "created_by",
    )

    list_filter = (
        "status",
        "company",
        "branch",
        "arrival_date",   
        "created_at",
    )
    search_fields = (
        "title",
        "description",
        "client__full_name",
        "client__phone",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "created_by",
    )
    ordering = ("-created_at",)

    autocomplete_fields = (
        "company",
        "branch",
        "client",
        "created_by",
    )

    # --------- ограничения по компании ---------

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user

        # суперюзер видит всё
        if user.is_superuser:
            return qs

        company = _get_company(user)
        if company is None:
            return qs.none()

        return qs.filter(company=company)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        В формах в админке показываем только объекты своей компании.
        """
        user = request.user
        company = _get_company(user)

        if company is not None:
            if db_field.name == "company":
                kwargs["queryset"] = Company.objects.filter(id=company.id)
            elif db_field.name == "branch":
                kwargs["queryset"] = Branch.objects.filter(company=company)
            elif db_field.name == "client":
                # если у Client есть поле company – фильтруем по нему
                qs = Client.objects.all()
                if hasattr(Client, "company"):
                    qs = qs.filter(company=company)
                kwargs["queryset"] = qs

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        """
        Авто-подстановка company и created_by.
        """
        user = request.user

        # company — из пользователя, если не указана явно
        if not obj.company_id:
            company = _get_company(user)
            if company is not None:
                obj.company = company

        # created_by только при создании
        if not change and not obj.created_by_id and user.is_authenticated:
            obj.created_by = user

        super().save_model(request, obj, form, change)
