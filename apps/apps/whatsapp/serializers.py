# apps/integrations/serializers.py
from rest_framework import serializers
from .models import WhatsAppSession

class WhatsAppSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppSession
        fields = ("company", "status", "last_qr_data_url", "phone_hint", "updated_at")
        read_only_fields = ("updated_at",)
