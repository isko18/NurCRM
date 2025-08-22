from rest_framework import serializers
from .models import Message, WhatsAppSession


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = "__all__"


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WhatsAppSession
        fields = "__all__"
