from rest_framework import serializers

class ButtonSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=20)
    type = serializers.ChoiceField(choices=["text"])
    payload = serializers.CharField(max_length=255, required=False, allow_blank=True)


class SendMessageSerializer(serializers.Serializer):
    phone = serializers.CharField(required=False)
    chat_id = serializers.CharField(required=False, allow_blank=True)
    username = serializers.CharField(required=False, allow_blank=True)
    channel_id = serializers.CharField()
    chat_type = serializers.ChoiceField(default="whatsapp", choices=[
        "whatsapp", "whatsgroup", "viber", "instagram", "telegram", "telegroup", "vk", "avito"
    ])

    text = serializers.CharField(required=False, allow_blank=True)
    content_uri = serializers.URLField(required=False, allow_blank=True)

    crm_user_id = serializers.CharField(required=False, allow_blank=True)
    crm_message_id = serializers.CharField(required=False, allow_blank=True)
    ref_message_id = serializers.CharField(required=False, allow_blank=True)
    clear_unanswered = serializers.BooleanField(default=True)

    buttons = ButtonSerializer(many=True, required=False)

    def validate(self, data):
        if not data.get("text") and not data.get("content_uri"):
            raise serializers.ValidationError("Нужно указать либо 'text', либо 'content_uri'.")

        if data.get("text") and data.get("content_uri"):
            raise serializers.ValidationError("Нельзя передавать 'text' и 'content_uri' одновременно.")

        if not data.get("phone") and not data.get("chat_id") and not data.get("username"):
            raise serializers.ValidationError("Нужно указать один из идентификаторов: phone, chat_id или username.")

        return data
