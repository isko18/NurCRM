# from django.db import models

# # Create your models here.
# class Product(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
#     company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='products', verbose_name='Компания')
#     name = models.CharField(max_length=255)
#     barcode = models.CharField(max_length=64, null=True, blank=True)
#     brand = models.ForeignKey(ProductBrand, on_delete=models.SET_NULL, null=True, blank=True)
#     category = models.ForeignKey(ProductCategory, on_delete=models.SET_NULL, null=True, blank=True)
#     quantity = models.PositiveIntegerField(default=0)
#     price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         unique_together = ('company', 'barcode')
#         verbose_name = 'Товар'
#         verbose_name_plural = 'Товары'

#     def __str__(self):
#         return self.name
    