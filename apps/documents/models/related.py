

from django.db import models

from apps.documents.models.abstract import (
    DocumentAbstractModel
) 

from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)



from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey




class DocumentProduct(
    UUIDPrimaryKeyMixin
):
    product = models.ForeignKey(
        "warehouse.WarehouseProduct",on_delete=models.SET_NULL
        verbose_name="Товар",null=True,blank=True
    )

    unit = models.CharField(max_length=20,verbose_name="Ед измерения")

    name = models.CharField(max_length=255,verbose_name="Название")

    quantity = models.PositiveIntegerField(default=0,verbose_name="Количество")
    
    # Первый раз использую - Это связь с любыми моделями
    # Generic ForeignKey 
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    
    content_object = GenericForeignKey("content_type","object_id") 

    class Meta:
        verbose_name = "Документы-товары"
        verbose_name_plural = "Документ-товар"
        indexes = [
           models.Index(fields=["content_type","object_id"])
        ]
    



class InvoicePayment(models.Model):

    is_paid = models.BooleanField(verbose_name="Оплачен?")
    
    total_sum = models.

    created_at = models.DateTimField()
    
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()

    content_object = GenericForeignKey("content_type","object_id")
    
    class Meta:
        verbose_name = "Cчета"
        verbose_name_plural = "Счет"
        indexes = [
            models.Index(fields=["content_type","object_id"])
        ]








