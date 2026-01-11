from apps.warehouse.models.base import (
    BaseModelId,BaseModelCompanyBranch,BaseModelDate
)

from django.db import models

from django.core.exceptions import ValidationError


class WarehouseProductCharasteristics(BaseModelId,BaseModelCompanyBranch,BaseModelDate):
    
    product = models.OneToOneField(
        "warehouse.WarehouseProduct",
        on_delete=models.CASCADE,
        related_name="characteristics",
        verbose_name="Товар",
    )

    height_cm = models.DecimalField(
        "Высота, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    width_cm = models.DecimalField(
        "Ширина, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    depth_cm = models.DecimalField(
        "Глубина, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    factual_weight_kg = models.DecimalField(
        "Фактический вес, кг",
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
    )
    description = models.TextField(
        "Описание",
        blank=True,
    )

    
    class Meta:
        verbose_name = "Характеристики товара"
        verbose_name_plural = "Характеристики товара"

    def __str__(self):
        return f"Характеристики: {self.product}"

    def clean(self):
        if self.product_id:
            # компания / филиал должны совпадать с товаром
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id != self.branch_id:
                raise ValidationError({"branch": "Филиал должен совпадать с филиалом товара (оба None или одинаковые)."})

    def save(self, *args, **kwargs):
        # если не указали company/branch — подставляем из товара
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id
        super().save(*args, **kwargs)



