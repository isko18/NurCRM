

from django.db import models

from apps.documents.models.abstract import (
    DocumentAbstractModel
) 

from apps.documents.models.mixins import (
    UUIDPrimaryKeyMixin
)




class DocumentProduct(
    UUIDPrimaryKeyMixin,
    DocumentAsbtractModel
):
    product = models.ForeignKey(
        "warehouse.WarehouseProduct",on_delete=models.SET_NULL
        verbose_name="Товар",null=True,blank=True
    )

    unit = models.CharField(max_length=20,verbose_name="Ед измерения")

    name = models.CharField(max_length=255,verbose_name="Название")

    quantity = models.PositiveIntegerField(default=0,verbose_name="Количество")
    

    class Meta:
        pass
    



class InvoicePayment():
    pass
