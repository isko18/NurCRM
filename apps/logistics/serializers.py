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
    """Сериализатор для логистики с автоматической компанией/филиалом."""

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
        )

    # ------------------------------------------------------------------
    # === ДИНАМИЧЕСКИЕ queryset ДЛЯ ПОЛЕЙ (company / branch / client)
    # ------------------------------------------------------------------
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        request = self.context.get("request")
        company = _company_from_ctx(self)
        branch = _active_branch(self)

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
        """Автоподстановка company/branch."""
        company = attrs.get("company") or _company_from_ctx(self)
        branch = attrs.get("branch") or _active_branch(self)

        attrs["company"] = company
        attrs["branch"] = branch

        return attrs

    def create(self, validated_data):
        """created_by = request.user автоматически."""
        req = self.context.get("request")
        if req and getattr(req, "user", None) and req.user.is_authenticated:
            validated_data["created_by"] = req.user

        return super().create(validated_data)
