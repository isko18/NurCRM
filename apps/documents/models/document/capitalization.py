from apps.documents.models.base import (
    UUIDPrimaryKeyMixin
)

from django.db import models

class DocumentCapitalization(UUIDPrimaryMixin):
    
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="capitalization")
    

    
    class Meta:
        verbose_name = ""
        verbose_name_plural = ""

        indexes = [
            models.Index()
        ]








