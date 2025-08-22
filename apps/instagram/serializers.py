from rest_framework import serializers
from .models import CompanyIGAccount


class AccountConnectSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    code = serializers.CharField(required=False, allow_blank=True)


class CompanyIGAccountOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyIGAccount
        fields = [
        "id","company","username","is_active","is_logged_in","last_login_at","created_at","updated_at"
        ]