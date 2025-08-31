from django.contrib import admin
from apps.main.models import (
    Contact, Pipeline, Deal, Task, Client, ClientDeal, Bid, SocialApplications,
    Product, ProductBrand, ProductCategory, GlobalProduct, GlobalBrand, GlobalCategory,
    Warehouse, WarehouseEvent,
    Order, OrderItem,
    Cart, CartItem, Sale, SaleItem,
    Review, Notification, Event,
    Integration, Analytics
)


# ==== ГРУППЫ ==== #
CRM_MODELS = [Contact, Pipeline, Deal, Task, Client, ClientDeal, Bid, SocialApplications]
PRODUCT_MODELS = [Product, ProductBrand, ProductCategory, GlobalProduct, GlobalBrand, GlobalCategory]
WAREHOUSE_MODELS = [Warehouse, WarehouseEvent]
SALES_MODELS = [Cart, CartItem, Sale, SaleItem, Order, OrderItem]
SERVICE_MODELS = [Review, Notification, Event, Integration, Analytics]


# ======== РЕГИСТРАЦИЯ С ГРУППИРОВКОЙ ======== #

# 👥 CRM
for model in CRM_MODELS:
    admin.site.register(model)

# 📦 Продукты
for model in PRODUCT_MODELS:
    admin.site.register(model)

# 🏬 Склад
for model in WAREHOUSE_MODELS:
    admin.site.register(model)

# 💰 Продажи
for model in SALES_MODELS:
    admin.site.register(model)

# ⚙️ Сервисные
for model in SERVICE_MODELS:
    admin.site.register(model)
