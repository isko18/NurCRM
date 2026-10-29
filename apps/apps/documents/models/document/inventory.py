
from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    CompanyBranchAbstractModel,
    DocumentAbstarctModel,
    ShopAbstractModel
)

from django.db import models

from django.contrib.contenttypes.fields import GenericRelation
from apps.documents.models.related import (
    DocumentProduct,
    InvoicePayment
)

class DocumentInventory(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    DocumentAbstractModel,
    ShopAbstractModel
):
    
     
    products = GenericRelation(
        DocumentProduct,related_name_query="inventories"
    )

    invoices = GenericRelation(
        InvoicePayment,related_name_query="inventories"
    ) 
    

    class Meta:
        verbose_name = "Инвентаризации"
        verbose_name_plural = "Инвентаризация"

