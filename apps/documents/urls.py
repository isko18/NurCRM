from django.urls import path





# documents/
# documents/<uuid>/
#
# documents/<uuid>/sale
# documents/<uuid>/sale/products/
# documents/<uuid>/sale/products/<uuid> 
# documents/<uuid>:/sale/invoices/
# documents/<uuid>/sale/invoices/<uuid>
# 
#  
# 
# 
# 
# 
# 



urlpatterns = [
# Основная работа с документами 
    # (какие вообше есть документы? фильтры получение и т.д ) 
    path("" , , name="document"),
    # (все что есть внутри конкретного документы? также это обновление )
    path("<uuid:pk>/" , , name="documents-detail"),

# Работа с разными типами документа

    # Оприходование
    

    # Перемещение 


    # Инвентаризация



    # Закупка



    # Возврат закупки



    # Продажа


    
    # Возврат продажи




    # Cписание


   


    # Расход





    # Приход





]


