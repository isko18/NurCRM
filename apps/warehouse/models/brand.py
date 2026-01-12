from apps.warehouse.models.base import (
    BaseModelId,BaseModelCompanyBranch
)

from django.db import models 
from django.core.exceptions import ValidationError
from django.db.models import Q, Max, IntegerField

from mptt.models import TreeForeignKey

class WarehouseProductBrand(BaseModelId,BaseModelCompanyBranch):

    name = models.CharField(max_length=128, verbose_name="Название")

    parent = TreeForeignKey(
        'self', 
        on_delete=models.CASCADE, 
        null=True, blank=True,
        related_name='children', verbose_name='Родительский бренд')
    
    
    class MPTTMeta:
        order_insertion_by = ['name']

    class Meta:
        verbose_name = 'Бренд'
        verbose_name_plural = 'Бренды'
        constraints = [
            models.UniqueConstraint(
                fields=('branch', 'name'),
                name='uq_warehouse_brand_name_per_branch',
                condition=models.Q(branch__isnull=False),
            ),
            models.UniqueConstraint(
                fields=('company', 'name'),
                name='uq_warehouse_brand_name_global_per_company',
                condition=models.Q(branch__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'name']),
            models.Index(fields=['company', 'branch', 'name']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({'branch': 'Филиал принадлежит другой компании.'})
        if self.parent_id:
            if self.parent.company_id != self.company_id:
                raise ValidationError({'parent': 'Родительский бренд другой компании.'})
            if (self.parent.branch_id or None) != (self.branch_id or None):
                raise ValidationError({'parent': 'Родительский бренд другого филиала.'})

