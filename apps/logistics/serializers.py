# apps/logistics/serializers.py

from rest_framework import serializers
from .models import Logistics
from apps.users.models import Company, Branch, User
from apps.main.models import Client


from apps.main.serializers import (
    _company_from_ctx,
    _active_branch,
    _restrict_pk_queryset_strict,
)

class LogisticsSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    class Meta:
        model = Logistics
        fields = [
            "id",
            "company",
            "branch",
            "client",
            "created_by",
            "title",
            "description",
            "price_car",
            "price_service",
            "sale_price",
            "revenue",
            "arrival_date",
            "status",
            "status_display",
            "created_at",
            "updated_at",
        ]
        read_only_fields = (
            "id",
            "created_by",
            "created_at",
            "updated_at",
            "status_display",
        )

    # ------------------------------------------------------------------
    # === ДИНАМИЧЕСКИЕ queryset ДЛЯ ПОЛЕЙ (company / branch / client)
    # ------------------------------------------------------------------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        company = _company_from_ctx(self)
        branch = _active_branch(self)

        # created_by - только чтение (на всякий случай)
        if "created_by" in self.fields:
            self.fields["created_by"].read_only = True

        # ------ Ограничиваем company ------
        if "company" in self.fields:
            qs = Company.objects.all()
            if company:
                qs = qs.filter(id=company.id)
            self.fields["company"].queryset = qs

        # ------ Ограничиваем branch ------
        if "branch" in self.fields:
            _restrict_pk_queryset_strict(
                field=self.fields["branch"],
                base_qs=Branch.objects.all(),
                company=company,
                branch=branch,
            )

        # ------ Ограничиваем client ------
        if "client" in self.fields:
            _restrict_pk_queryset_strict(
                field=self.fields["client"],
                base_qs=Client.objects.all(),
                company=company,
                branch=branch,
            )

    # ------------------------------------------------------------------
    # === ПЕРЕД СОХРАНЕНИЕМ
    # ------------------------------------------------------------------
    def validate(self, attrs):
        """
        Автоподстановка company/branch.
        Важно: не перезаписываем на None, иначе получишь ошибки сохранения.
        """
        ctx_company = _company_from_ctx(self)
        ctx_branch = _active_branch(self)

        company = attrs.get("company") or ctx_company
        if not company:
            raise serializers.ValidationError(
                {"company": "Компания не определена (нет в запросе и нет в контексте)."}
            )

        # branch может быть null по модели, поэтому тут мягко
        branch = attrs.get("branch") if "branch" in attrs else ctx_branch

        attrs["company"] = company
        attrs["branch"] = branch

        return attrs

    def create(self, validated_data):
        """created_by = request.user автоматически."""
        req = self.context.get("request")
        if req and getattr(req, "user", None) and req.user.is_authenticated:
            validated_data["created_by"] = req.user

        return super().create(validated_data)