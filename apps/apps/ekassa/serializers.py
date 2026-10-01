from rest_framework import serializers

from apps.ekassa.crypto import encrypt_secret
from apps.ekassa.models import EkassaIntegration


class EkassaIntegrationReadSerializer(serializers.ModelSerializer):
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = EkassaIntegration
        fields = (
            "is_enabled",
            "api_base_url",
            "login_email",
            "fiscal_number",
            "has_password",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_has_password(self, obj) -> bool:
        if obj is None:
            return False
        return obj.has_stored_password


class EkassaIntegrationWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")

    class Meta:
        model = EkassaIntegration
        fields = (
            "is_enabled",
            "api_base_url",
            "login_email",
            "fiscal_number",
            "password",
        )

    def validate(self, attrs):
        is_enabled = attrs.get("is_enabled", getattr(self.instance, "is_enabled", False))
        if not is_enabled:
            return attrs

        fiscal = attrs.get("fiscal_number", getattr(self.instance, "fiscal_number", "") or "")
        email = attrs.get("login_email", getattr(self.instance, "login_email", "") or "")
        pwd_in = attrs.get("password", "")
        has_old = bool(self.instance and self.instance.has_stored_password)

        if not str(fiscal).strip():
            raise serializers.ValidationError({"fiscal_number": "Обязательно при включённой интеграции."})
        if not str(email).strip():
            raise serializers.ValidationError({"login_email": "Обязательно при включённой интеграции."})
        if not str(pwd_in).strip() and not has_old:
            raise serializers.ValidationError({"password": "Укажите пароль eKassa."})

        # Смена email без нового пароля: в БД остаётся старый cipher → логин в eKassa даёт 401 / Credentials mismatch
        if self.instance and is_enabled:
            old_email = (self.instance.login_email or "").strip().lower()
            new_email = str(email).strip().lower()
            if old_email != new_email:
                pwd_from_request = ""
                init = getattr(self, "initial_data", None)
                if isinstance(init, dict):
                    pwd_from_request = str(init.get("password") or "").strip()
                if not pwd_from_request:
                    raise serializers.ValidationError(
                        {
                            "password": (
                                "При смене login_email обязательно передайте пароль от учётной записи eKassa "
                                "(под этим email)."
                            )
                        }
                    )

        return attrs

    def create(self, validated_data):
        pwd = (validated_data.pop("password", None) or "").strip()
        obj = EkassaIntegration.objects.create(**validated_data)
        if pwd:
            obj.password_cipher = encrypt_secret(pwd)
            obj.save(update_fields=["password_cipher", "updated_at"])
        return obj

    def update(self, instance, validated_data):
        pwd = validated_data.pop("password", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        update_fields = list(validated_data.keys())
        if pwd is not None and str(pwd).strip():
            instance.password_cipher = encrypt_secret(pwd.strip())
            update_fields.append("password_cipher")
        update_fields.append("updated_at")
        instance.save(update_fields=update_fields)
        return instance


def default_settings_payload():
    return {
        "is_enabled": False,
        "api_base_url": "",
        "login_email": "",
        "fiscal_number": "",
        "has_password": False,
        "created_at": None,
        "updated_at": None,
    }
