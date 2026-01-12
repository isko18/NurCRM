from django.urls import path 

from .views.warehouse import (
    WarehouseView,WarehouseDetailView
)

# / -> create | list 
# /<uuid> -> update | delete | retrieve


# /<uuid>/products -> list | create  
# /products/<uuid> -> update | delete | retrieve

# products/<uuid>/images -> 
# product-images/<uuid> ->

# products/<uuid>/packages -> 
# products-packages/<uuid> ->

# products/<uuid>/charasteristics -> 
# products-charasteristics/<uuid> ->


# warehouses-brands/ -> create | list 
# warehouses-category/ -> create | list

# warehouses-brands/<uuid> -> update | retireve
# warehouses-category/<uuid> -> update | retrieve


urlpatterns = [

    path("",WarehouseView.as_view() ,name="warehouse"),
    path("<uuid:pk>/",WarehouseDetailView.as_view() ,name="warehouse-detail")
    


]
