from rest_framework import serializers

from .crypto import encrypt_secret
from .models import OneCIntegration, OneCSyncRecord


class OneCIntegrationSerializer(serializers.ModelSerializer):
    # Секреты только на запись, наружу не отдаём.
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    inbound_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_stored_password = serializers.BooleanField(read_only=True)

    class Meta:
        model = OneCIntegration
        fields = [
            "id", "is_enabled", "base_url", "auth_type", "login",
            "password", "inbound_secret", "has_stored_password",
            "currency", "enabled_sources", "request_timeout",
            "last_pull_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "last_pull_at", "created_at", "updated_at"]

    def _apply_secret(self, validated, key, cipher_field):
        if key in validated:
            plain = validated.pop(key)
            if plain:
                validated[cipher_field] = encrypt_secret(plain)
        return validated

    def update(self, instance, validated_data):
        validated_data = self._apply_secret(validated_data, "password", "password_cipher")
        validated_data = self._apply_secret(validated_data, "inbound_secret", "inbound_secret_cipher")
        return super().update(instance, validated_data)

    def create(self, validated_data):
        validated_data = self._apply_secret(validated_data, "password", "password_cipher")
        validated_data = self._apply_secret(validated_data, "inbound_secret", "inbound_secret_cipher")
        return super().create(validated_data)


class OneCSyncRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = OneCSyncRecord
        fields = [
            "id", "direction", "source_type", "source_id", "operation",
            "idempotency_key", "endpoint", "onec_doc_type", "status", "attempts",
            "last_error", "onec_external_id", "onec_number", "onec_posted_at",
            "request_payload", "response_payload", "created_at", "updated_at",
        ]
        read_only_fields = fields
