from django.db import models
from django.conf import settings

class Warehouse(models.Model):
    company = models.ForeignKey(
        "Company",
        on_delete=models.CASCADE,
        related_name="warehouses",
        verbose_name="Компания"
    )
    name = models.CharField(
        max_length=255,
        verbose_name="Название"
    )
    address = models.CharField(
        max_length=500,
        verbose_name="Адрес",
        blank=True,
        null=True
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата создания"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Дата обновления"
    )

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.company})"
