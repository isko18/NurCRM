
from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    CompanyBranchAbstractModel,
    DocumentAbstarctModel
)

from django.db import models

from django.contrib.contenttypes.fields import GenericRelation
from apps.documents.models.related import (
    DocumentProduct,InvoicePayment 
)


class DocumentDisplacement(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    from_warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="displacements_from"
    )

    to_warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="displacements_to"
    )
    
 
    products = GenericRelation(
        DocumentProduct,related_name_query="displacements"
    )

    invoices = GenericRelation(
        InvoicePayment,related_name_query="displacements"
    ) 
    
    class Meta:
        verbose_name = "Перемещение"
        verbose_name_plural = "Перемещения"

