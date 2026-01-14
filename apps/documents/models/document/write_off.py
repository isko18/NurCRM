

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

class DocumentWriteOff(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
):
    
 
    products = GenericRelation(
        DocumentProduct,related_name_query="write_offs"
    )

    invoices = GenericRelation(
        InvoicePayment,related_name_query="write_offs"
    ) 
    

    class Meta:
        verbose_name = "Списание"
        verbose_name_plural = "Списание"
