from django.urls import path




#
# documents/ - list
# 
#  create|update|delete
#-------------------------------- 
# <uuid>/capitalization/
#  
# <uuid>/sale/
#
# <uuid>/procurement/
#
# <uuid>/return_sale/
#
# <uuid>/return_procurement/
#
# <uuid>/write_off/
#
# <uuid>/expense/
#
# <uuid>/repeit/
#
# <uuid>/displacement/
#
# <uuid>/inventory/
#----------------------------------
#
# documents/<uuid>/products/ <- list|create 
# documents/products-detail/<uuid> <- retrieve|update|delete
# 
# documents/<uuid>/invoices/ <- list|delete
# documents/invoice-detail/<uuid> <- retrieve|update|delete
#

urlpatterns = [
# Базовая работа с документами 
  
    # (фильтры и поиск документов) 
    #path("" , , name="document"),
    
# Работа с разными типами




]


