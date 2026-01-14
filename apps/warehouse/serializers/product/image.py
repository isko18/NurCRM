from io import BytesIO
from PIL import Image

from django.core.files.base import ContentFile
from rest_framework import serializers

from apps.warehouse.serializers.mixins import CompanyBranchReadOnlyMixin
from apps.warehouse.models import WarehouseProductImage


def _to_webp(uploaded_file, *, quality: int = 82) -> ContentFile:
    """
    Конвертирует загруженный файл в WebP и возвращает ContentFile,
    который можно присвоить ImageField.
    """
    uploaded_file.seek(0)

    img = Image.open(uploaded_file)

    # Если PNG с альфой — оставим прозрачность
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")

    buf = BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    buf.seek(0)

    base_name = (getattr(uploaded_file, "name", "image") or "image").rsplit(".", 1)[0]
    return ContentFile(buf.read(), name=f"{base_name}.webp")


class WarehouseProductImageSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):
    # принимаем файл на вход
    image = serializers.ImageField(write_only=True, required=True)
    # отдаём ссылку наружу
    image_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = WarehouseProductImage
        fields = ("id", "product", "image", "image_url", "is_primary", "alt", "created_at")
        read_only_fields = ("id", "product", "created_at")

    def get_image_url(self, obj):
        request = self.context.get("request")
        if not getattr(obj, "image", None):
            return None
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url

    def validate_image(self, value):
        """
        Мягкая валидация типа.
        DRF/Pillow уже проверяет что это изображение, но доп. фильтр не помешает.
        """
        ct = getattr(value, "content_type", "") or ""
        allowed = {"image/jpeg", "image/png", "image/webp"}
        if ct and ct not in allowed:
            raise serializers.ValidationError("Разрешены только JPG, PNG, WEBP.")
        return value

    def create(self, validated_data):
        """
        Конвертируем входной файл в webp перед сохранением.
        product сюда не даём менять извне — его должен ставить view.perform_create().
        """
        uploaded = validated_data.pop("image", None)

        if uploaded:
            validated_data["image"] = _to_webp(uploaded)

        return super().create(validated_data)

    def update(self, instance, validated_data):
        uploaded = validated_data.pop("image", None)

        if uploaded:
            validated_data["image"] = _to_webp(uploaded)

        return super().update(instance, validated_data)
