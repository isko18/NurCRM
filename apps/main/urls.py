from django.urls import path
from .views import *

urlpatterns = [

    # 🔹 Контакты
    path('contacts/', ContactListCreateAPIView.as_view(), name='contact-list-create'),
    path('contacts/<uuid:pk>/', ContactRetrieveUpdateDestroyAPIView.as_view(), name='contact-detail'),

    # 🔹 Воронки
    path('pipelines/', PipelineListCreateAPIView.as_view(), name='pipeline-list-create'),
    path('pipelines/<uuid:pk>/', PipelineRetrieveUpdateDestroyAPIView.as_view(), name='pipeline-detail'),

    # 🔹 Сделки
    path('deals/', DealListCreateAPIView.as_view(), name='deal-list-create'),
    path('deals/<uuid:pk>/', DealRetrieveUpdateDestroyAPIView.as_view(), name='deal-detail'),

    # 🔹 Задачи
    path('tasks/', TaskListCreateAPIView.as_view(), name='task-list-create'),
    path('tasks/<uuid:pk>/', TaskRetrieveUpdateDestroyAPIView.as_view(), name='task-detail'),

    # 🔹 Заказы и позиции заказов
    path('orders/', OrderListCreateAPIView.as_view(), name='order-list-create'),
    path('orders/<uuid:pk>/', OrderRetrieveUpdateDestroyAPIView.as_view(), name='order-detail'),
    # (если нужна работа с отдельными позициями заказа — реализуй OrderItemAPIView)
    # path('order-items/<uuid:pk>/', OrderItemAPIView.as_view(), name='order-item-detail'),

    # 🔹 Продукты, категории, бренды
    path('products/', ProductListCreateAPIView.as_view(), name='product-list-create'),
    path('products/<uuid:pk>/', ProductRetrieveUpdateDestroyAPIView.as_view(), name='product-detail'),

    path('categories/', ProductCategoryListCreateAPIView.as_view(), name='category-list'),
    path('categories/<uuid:pk>/', ProductCategoryRetrieveUpdateDestroyAPIView.as_view(), name='category-detail'),

    path('brands/', ProductBrandListCreateAPIView.as_view(), name='brand-list'),
    path('brands/<uuid:pk>/', ProductBrandRetrieveUpdateDestroyAPIView.as_view(), name='brand-detail'),

    # 🔹 Склад и складские события
    path('warehouses/', WarehouseListCreateAPIView.as_view(), name='warehouse-list-create'),
    path('warehouses/<uuid:pk>/', WarehouseRetrieveUpdateDestroyAPIView.as_view(), name='warehouse-retrieve-update-destroy'),

    path('warehouse-events/', WarehouseEventListCreateAPIView.as_view(), name='warehouse-event-list-create'),
    path('warehouse-events/<uuid:pk>/', WarehouseEventRetrieveUpdateDestroyAPIView.as_view(), name='warehouse-event-retrieve-update-destroy'),

    # 🔹 Интеграции и аналитика
    path('integrations/', IntegrationListCreateAPIView.as_view(), name='integration-list-create'),
    path('integrations/<uuid:pk>/', IntegrationRetrieveUpdateDestroyAPIView.as_view(), name='integration-detail'),

    path('analytics/', AnalyticsListAPIView.as_view(), name='analytics-list'),

    # 🔹 Отзывы
    path('reviews/', ReviewListCreateAPIView.as_view(), name='review-list-create'),
    path('reviews/<uuid:pk>/', ReviewRetrieveUpdateDestroyAPIView.as_view(), name='review-detail'),

    # 🔹 Уведомления
    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/<uuid:pk>/', NotificationDetailView.as_view(), name='notification-detail'),
    path('notifications/mark-all-read/', MarkAllNotificationsReadView.as_view(), name='mark-all-notifications-read'),

    # 🔹 События
    path('events/', EventListCreateAPIView.as_view(), name='event-list-create'),
    path('events/<uuid:pk>/', EventRetrieveUpdateDestroyAPIView.as_view(), name='event-detail'),
    path('orders/analytics/', OrderAnalyticsView.as_view(), name='order-analytics'),
    
    path('clients/', ClientListCreateAPIView.as_view(), name='client-list-create'),
    path('clients/<uuid:pk>/', ClientRetrieveUpdateDestroyAPIView.as_view(), name='client-detail'),
]

