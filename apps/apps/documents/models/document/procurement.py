

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

class DocumentProcurement(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    supplier = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
    
     
    products = GenericRelation(
        DocumentProduct,related_name_query="procurements"
    )

    invoices = GenericRelation(
        InvoicePayment,related_name_query="procurements"
    ) 
    

    class Meta:
        verbose_name = "Закупки"
        verbose_name_plural = "Закупка"
