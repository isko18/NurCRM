from django.db import models
import uuid




class UUIDPrimaryKeyMixin(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    
    class Meta:
        abstract = True



class DateTimeMixin(models.Model):
    
    created_date = models.DateTimeField(auto_now_add=True, verbose_name="Дата открытия") 
    updated_date = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        abstract = True

    


