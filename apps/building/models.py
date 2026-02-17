import uuid

from django.db import models

from apps.users.models import Company


class ResidentialComplex(models.Model):
    """
    Жилой комплекс (ЖК) — объект строительной компании.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="building_residential_complexes",
        verbose_name="Компания",
    )

    name = models.CharField(max_length=255, verbose_name="Название ЖК")
    address = models.CharField(max_length=512, blank=True, null=True, verbose_name="Адрес")
    description = models.TextField(blank=True, null=True, verbose_name="Описание")

    is_active = models.BooleanField(default=True, verbose_name="Активен")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Жилой комплекс (ЖК)"
        verbose_name_plural = "Жилые комплексы (ЖК)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company"]),
            models.Index(fields=["company", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.company.name})"
