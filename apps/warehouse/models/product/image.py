from apps.warehouse.models.base import (
    BaseModelId,BaseModelCompanyBranch,

    product_image_upload_to
)
import uuid
from PIL import Image
from io import BytesIO
from django.core.exceptions import ValidationError
from django.db import models

from django.core.files.base import ContentFile


class WarehouseProductImage(BaseModelId,BaseModelCompanyBranch):
    
    product = models.ForeignKey(
        "WarehouseProduct", 
        on_delete=models.CASCADE, 
        related_name="images", verbose_name="Товар"
    )

    image = models.ImageField(upload_to=product_image_upload_to, null=True, blank=True, verbose_name="Изображение (WebP)")
    
    alt = models.CharField(max_length=255, blank=True, verbose_name="Alt-текст")
    
    is_primary = models.BooleanField(default=False, verbose_name="Основное изображение")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Фото товара"
        verbose_name_plural = "Фото товара"
        constraints = [
            # не более одного основного снимка на продукт
            models.UniqueConstraint(
                fields=("product",),
                condition=models.Q(is_primary=True),
                name="uq_warehouse_primary_product_image",
            )
        ]
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=["company", "branch"]),
            models.Index(fields=["product", "is_primary"]),
        ]

    def __str__(self):
        return f"{self.product.name} — image {self.pk}"

    def clean(self):
        # company/branch должны совпадать с продуктом
        if self.product_id:
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания изображения должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id not in (None, self.branch_id):
                raise ValidationError({"branch": "Филиал изображения должен совпадать с филиалом товара (или быть глобальным вместе с ним)."})

    def save(self, *args, **kwargs):
        # Подставим company/branch от продукта если не заданы
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id

        # Если загружен файл (любой формат) — преобразуем в WebP и перезапишем self.image
        if self.image and hasattr(self.image, "file"):
            try:
                self.image = self._convert_to_webp(self.image)
            except Exception as e:
                raise ValidationError({"image": f"Не удалось конвертировать в WebP: {e}"})

        super().save(*args, **kwargs)

        # Если отмечено как основное — снимем флаг у остальных
        if self.is_primary:
            (type(self).objects
                .filter(product=self.product, is_primary=True)
                .exclude(pk=self.pk)
                .update(is_primary=False))

    def delete(self, *args, **kwargs):
        storage = self.image.storage if self.image else None
        name = self.image.name if self.image else None
        super().delete(*args, **kwargs)
        # удалим файл из хранилища
        if storage and name and storage.exists(name):
            storage.delete(name)

    @staticmethod
    def _convert_to_webp(field_file) -> ContentFile:
        """
        Принимает загруженный файл любого поддерживаемого PIL формата,
        возвращает ContentFile с webp и корректным именем.
        """
        field_file.seek(0)
        im = Image.open(field_file)

        # для WebP нужен RGB
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")

        buf = BytesIO()
        # quality 80 / method 6 — хорошее качество и компрессия
        im.save(buf, format="WEBP", quality=80, method=6)
        buf.seek(0)

        content = ContentFile(buf.read())
        # новое имя с webp-расширением
        content.name = f"{uuid.uuid4().hex}.webp"
        return content

