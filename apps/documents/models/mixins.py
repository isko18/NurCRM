from django.db import models
import uuid




class UUIDPrimaryKeyMixin(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    
    class Meta:
        abstract = True



class DateTimeMixin(models.Model):
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата открытия") 
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        abstract = True

    


