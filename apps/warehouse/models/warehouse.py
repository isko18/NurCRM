from .base import ( 
    BaseModelId,BaseModelDate,
    BaseModelCompanyBranch
)

from django.db import models


# класс для склада
class Warehouse(BaseModelId,BaseModelDate,BaseModelCompanyBranch):

    name = models.CharField(max_length="Название ", null=True, blank=True)

    location = models.TextField(verbose_name="Локация",blank=False)
    
    class Status(models.TextChoices):
        active = "active","Активен"
        inactive = "inactive","Неактивен"
    
    status = models.CharField(
        max_length=10, 
        choices=Status.choices,
        default=Status.inactive
    )









