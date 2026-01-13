from django.db import models

class CompanyBranchAbstractModel(models.Model):
        
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




class ShopAbstractModel(models.Model):  
    shop = models.ForeignKey("warehouse.Warehouse",on_delete=models.SET_NULL,null=True)
    
    class Meta:
        abstract = True


class DocumentAbstractModel(models.Model):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="capitalization", verbose_name="Документ (обязателен)"
    )
    
    class Meta:
        abstract = True






