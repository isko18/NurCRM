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

    

class CompanyBranchMixin(models.Model):
        
    company = models.ForeignKey(
        "users.Company", 
        on_delete=models.CASCADE, 
        verbose_name='Компания'
    )
    
    branch = models.ForeignKey(
        "users.Branch", 
        on_delete=models.CASCADE, 
        null=True, blank=True, db_index=True, verbose_name='Филиал'    # фильтр по имени (частичное совпадение, ignore case)
    
    class Meta:
        abstract = True
