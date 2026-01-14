from django.db import models

import uuid


from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import (
    GenericForeignKey,GenericRelation
)


# Миксины

class UUIDPrimaryKeyMixin(models.Model):
    id = models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    
    class Meta:
        abstract = True



class DateTimeMixin(models.Model):
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата открытия") 
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        abstract = True

 



# Асбтрактные классы


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
    )

    class Meta:
        abstract = True




class ShopAbstractModel(models.Model):  
    shop = models.ForeignKey("warehouse.Warehouse",on_delete=models.SET_NULL,null=True)
    
    class Meta:
        abstract = True



# Классы для связей
class DocumentProduct(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel
):
    product = models.ForeignKey(
        "warehouse.WarehouseProduct",on_delete=models.SET_NULL,
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




class InvoicePayment(
    UUIDPrimaryKeyMixin,
    DateTimeMixin,
    CompanyBranchAbstractModel
):

    is_paid = models.BooleanField(verbose_name="Оплачен?")
    
    total_sum = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        db_index=True,
        verbose_name="Сумма"
    )
    
    created_at = models.DateTimeField()
    
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()

    content_object = GenericForeignKey("content_type","object_id")
    
    class Meta:
        verbose_name = "Cчета"
        verbose_name_plural = "Счет"
        indexes = [
            models.Index(fields=["content_type","object_id"])
        ]

# Документ
class Document(
    UUIDPrimaryKeyMixin,
    DateTimeMixin,
    CompanyBranchAbstractModel
):
   

    class DocumentTypes(models.TextChoices):
        CAPITALIZATION = "capitalization", "Оприходывание"
        DISPLACEMENT = "displacement", "Перемещение"
        INVENTORY = "inventory", "Инвентаризация"
        PROCUREMENT = "procurement", "Закупка"
        RETURN_PROCUREMENT = "return_procurement", "Возврат поставщику"
        RETURN_SALE = "return_sale", "Возврат от клиента"
        SALE = "sale", "Продажа"
        WRITE_OFF = "write_off", "Списание"
        EXPENSE = "expense","Расход"
        RECEIPT = "receipt","Приход"
    
    class StatusTypes(models.TextChoices):
        NEW = "new","новый"
        INWORK = "inwork","В работе"
        CLOSED = "closed","закрыт"
        CANCELLED = "cancelled","отменен"  




    document_type = models.CharField(max_length=50,
        choices=DocumentTypes.choices,
        null=False,blank=False,
        verbose_name="Тип документа"
    )
    
    carried_out = models.BooleanField(
        verbose_name="Документ проведен",default=True
    )
    
    document_status = models.CharField(max_length=20,
        choices=StatusTypes.choices,
        null=True,blank=True,default=None,
        verbose_name="Статус"
    )

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["document_type"]),
            models.Index(fields=["company","branch"]),
        ]



#  Типы документов
   

class DocumentCapitalization(
    UUIDPrimaryKeyMixin,
    ShopAbstractModel,
    CompanyBranchAbstractModel
): 
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="capitalization"
    )
    
    products = GenericRelation(
        DocumentProduct,
        related_query_name="capitalizations"
    )

    invoices = GenericRelation(
        InvoicePayment,
        related_query_name="capitalization"
    ) 
    
    class Meta:
        verbose_name = "Оприходование"
        verbose_name_plural = "Оприходование"


class DocumentDisplacement(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="displacement"
    )

    from_warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="displacements_from"
    )

    to_warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="displacements_to"
    )
    
    products = GenericRelation(
        DocumentProduct,
        related_query_name="displacements"
    )

    invoices = GenericRelation(
        InvoicePayment,
        related_query_name="displacements"
    ) 
    
    class Meta:
        verbose_name = "Перемещение"
        verbose_name_plural = "Перемещения"


class DocumentInventory(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
    ShopAbstractModel
):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="inventory"
    )
     
    products = GenericRelation(
        DocumentProduct,
        related_query_name="inventories"
    )

    invoices = GenericRelation(
        InvoicePayment,
        related_query_name="inventories"
    ) 
    

    class Meta:
        verbose_name = "Инвентаризации"
        verbose_name_plural = "Инвентаризация"



class DocumentProcurement(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="procurements"
    )
 

    supplier = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
    
     
    products = GenericRelation(
        DocumentProduct,related_query_name="procurements"
    )

    invoices = GenericRelation(
        InvoicePayment,related_query_name="procurements"
    ) 
    

    class Meta:
        verbose_name = "Закупки"
        verbose_name_plural = "Закупка"






class DocumentReturnProcurement(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="return_procurements"
    )
 

    supplier = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
    
     
    products = GenericRelation(
        DocumentProduct,related_query_name="return_procurements"
    )

    invoices = GenericRelation(
        InvoicePayment,related_query_name="return_procurements"
    ) 
    

    class Meta:
        verbose_name = "Возвраты (закупки)"
        verbose_name_plural = "Возврат (закупки)"


class DocumentReturnSale(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="return_sales"
    )
 

    client = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
    
    
    products = GenericRelation(
        DocumentProduct,related_query_name="return_sales"
    )

    invoices = GenericRelation(
        InvoicePayment,related_query_name="return_sales"
    ) 
    
 
    class Meta:
        verbose_name = "Возвраты (продажи)"
        verbose_name_plural = "Возврат (продаж)"



class DocumentSale(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="sales"
    )


    sale = models.ForeignKey(
        "main.Client",
        on_delete=models.SET_NULL,
        null=True,blank=True
    )
 
    products = GenericRelation(
        DocumentProduct,related_query_name="sales"
    )

    invoices = GenericRelation(
        InvoicePayment,related_query_name="sales"
    ) 
    

    class Meta:
        verbose_name = "Продажы"
        verbose_name_plural = "Продажа"



class DocumentWriteOff(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel,
):
    
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="write_offs"
    )
    
 
    products = GenericRelation(
        DocumentProduct,related_query_name="write_offs"
    )

    invoices = GenericRelation(
        InvoicePayment,related_query_name="write_offs"
    ) 
    

    class Meta:
        verbose_name = "Списание"
        verbose_name_plural = "Списание"


# Финансы

class PaymentCategory(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel
):
 

    title = models.CharField(max_length=255, verbose_name="Название")

    def __str__(self):
        return self.title




class DocumentExpense(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel
):
    
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="expenses"
    )
 
    
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.CASCADE,
        db_index=True,
    )

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        db_index=True,
    )


    payment_category = models.ForeignKey(
        "documents.PaymentCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="money_documents"
    )

    total_sum = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        db_index=True,
        verbose_name="Сумма"
    )
    
    comment = models.CharField(max_length=255, blank=True)

        
    class Meta:
        verbose_name = "Расходы"
        verbose_name_plural = "Расход"

        indexes = [
            models.Index(fields=["client", "user"]),
            models.Index(fields=["client", "payment_category"]),
            models.Index(fields=["user", "payment_category"]),
        ]






class DocumentReceipt(
    UUIDPrimaryKeyMixin,
    CompanyBranchAbstractModel
):
    document = models.OneToOneField(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="receipts"
    )
    
    
    
    client = models.ForeignKey(
        "main.Client",
        on_delete=models.CASCADE,
        db_index=True,
    )

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        db_index=True,
    )


    payment_category = models.ForeignKey(
        "documents.PaymentCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )

    total_sum = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        db_index=True,
        verbose_name="Сумма"
    )
    
    comment = models.CharField(max_length=255, blank=True)

        
    class Meta:
        verbose_name = "Приходы"
        verbose_name_plural = "Приход"

        indexes = [
            models.Index(fields=["client", "user"]),
            models.Index(fields=["client", "payment_category"]),
            models.Index(fields=["user", "payment_category"]),
        ]





