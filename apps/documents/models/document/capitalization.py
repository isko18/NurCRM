from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)

from apps.documents.models.abstract import (
    ShopAbstractModel,
    CompanyBranchAbstractModel,
    DocumentAbstarctModel
)

from django.db import models

class DocumentCapitalization(
    UUIDPrimaryMixin,
    ShopAbstarctModel,
    CompanyBranchAbstractModel,
    DocumentAsbtractModel
): 
    

    
    class Meta:
        verbose_name = "Оприходование"
        verbose_name_plural = "Оприходование"

        indexes = [
            models.Index()
        ]




