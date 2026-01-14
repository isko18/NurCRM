from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    ShopAbstractModel,
    CompanyBranchAbstractModel,
    DocumentAbstarctModel
)

from django.db import models

from django.contrib.contenttypes.fields import GenericRelation

from apps.documents.models.related import (
    DocumentProduct,
    InvoicePayment
)

class DocumentCapitalization(
    UUIDPrimaryMixin,
    ShopAbstarctModel,
    CompanyBranchAbstractModel,
    DocumentAsbtractModel
): 
    
    products = GenericRelation(
        DocumentProduct,related_name_query="capitalizations"
    )

    invoices = GenericRelation(
        InvoicePayment,related_name_query="capitalization"
    ) 
    
    class Meta:
        verbose_name = "Оприходование"
        verbose_name_plural = "Оприходование"

        indexes = [
            models.Index()
        ]




