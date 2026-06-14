from rest_framework import serializers

from .models import ClientRelease


class ClientReleaseSerializer(serializers.ModelSerializer):
    """Публичный ответ GET /api/version/ — контракт прежний (version, zip_url, release_notes)."""

    zip_url = serializers.SerializerMethodField()

    class Meta:
        model = ClientRelease
        fields = ["version", "zip_url", "release_notes"]

    def get_zip_url(self, obj):
        if not obj.zip_file:
            return None
        url = obj.zip_file.url
        request = self.context.get("request")
        # Абсолютный URL для exe. Нормализуем ведущий слэш на случай MEDIA_URL без него
        # ("media/"), чтобы build_absolute_uri не приклеил путь к адресу запроса.
        if request and not url.startswith(("http://", "https://")):
            url = request.build_absolute_uri("/" + url.lstrip("/"))
        return url


class ClientReleaseUploadSerializer(serializers.ModelSerializer):
    """Загрузка новой версии (multipart): version, zip_file, release_notes."""

    class Meta:
        model = ClientRelease
        fields = ["version", "zip_file", "release_notes"]

    def validate_version(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Версия обязательна.")
        return value

    def validate_zip_file(self, value):
        name = (getattr(value, "name", "") or "").lower()
        if not name.endswith(".zip"):
            raise serializers.ValidationError("Файл должен быть ZIP-архивом (.zip).")
        return value
