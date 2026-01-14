

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

class DocumentSale(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    sale = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
 
    products = GenericRelation(
        DocumentProduct,related_name_query="sales"
    )

    invoices = GenericRelation(
        InvoicePayment,related_name_query="sales"
    ) 
    

    class Meta:
        verbose_name = "Продажы"
        verbose_name_plural = "Продажа"
