from apps.warehouse.models.base import (
    BaseModelId,BaseModelCompanyBranch
)

from django.db import models

from django.core.exceptions import ValidationError


class WarehouseProductPackage(BaseModelId,BaseModelCompanyBranch):

    product = models.ForeignKey(
        "warehouse.WarehouseProduct",
        on_delete=models.CASCADE,
        related_name="packages",
        verbose_name="Товар",
    )

    name = models.CharField(
        "Упаковка",
        max_length=64,
        help_text="Например: коробка, пачка, блок, рулон",
    )

    quantity_in_package = models.DecimalField(
        "Количество в упаковке",
        max_digits=10,
        decimal_places=3,
        help_text="Сколько базовых единиц в одной упаковке",
    )

    unit = models.CharField(
        "Ед. изм.",
        max_length=32,
        blank=True,
        help_text="Если пусто — берём единицу товара",
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Упаковка товара"
        verbose_name_plural = "Упаковки товара"

    def __str__(self):
        return f"{self.name}: {self.quantity_in_package} {self.unit or self.product.unit}"

    def clean(self):
        if self.quantity_in_package is not None and self.quantity_in_package <= 0:
            raise ValidationError(
                {"quantity_in_package": "Количество в упаковке должно быть больше 0."}
            )

        if self.product_id:
            if self.company_id and self.product.company_id != self.company_id:
                raise ValidationError({"company": "Компания должна совпадать с компанией товара."})
            if self.branch_id is not None and self.product.branch_id != self.branch_id:
                raise ValidationError({"branch": "Филиал должен совпадать с филиалом товара (оба None или одинаковые)."})

    def save(self, *args, **kwargs):
        # автоподстановка company/branch из товара, если не заданы
        if self.product_id:
            if not self.company_id:
                self.company_id = self.product.company_id
            if self.branch_id is None:
                self.branch_id = self.product.branch_id
            # если unit не указали — наследуем от товара
            if not self.unit:
                self.unit = self.product.unit

        super().save(*args, **kwargs)


