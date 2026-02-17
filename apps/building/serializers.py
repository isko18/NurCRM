from rest_framework import serializers

from .models import ResidentialComplex


class ResidentialComplexSerializer(serializers.ModelSerializer):
    """Сериализатор для ЖК: список и детали."""

    class Meta:
        model = ResidentialComplex
        fields = [
            "id",
            "company",
            "name",
            "address",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "company", "created_at", "updated_at"]


class ResidentialComplexCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания ЖК. company подставляется из request.user."""

    class Meta:
        model = ResidentialComplex
        fields = [
            "id",
            "name",
            "address",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def create(self, validated_data):
        company = self.context["request"].user.company
        if not company:
            raise serializers.ValidationError({"company": "У пользователя не указана компания."})
        validated_data["company_id"] = company.id
        return super().create(validated_data)
