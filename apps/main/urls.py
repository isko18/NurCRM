from django.urls import path
from .views import *

urlpatterns = [
    path('contacts/', ContactListCreateAPIView.as_view(), name='contact-list-create'),
    path('contacts/<uuid:pk>/', ContactRetrieveUpdateDestroyAPIView.as_view(), name='contact-detail'),

    path('pipelines/', PipelineListCreateAPIView.as_view(), name='pipeline-list-create'),
    path('pipelines/<uuid:pk>/', PipelineRetrieveUpdateDestroyAPIView.as_view(), name='pipeline-detail'),

    path('deals/', DealListCreateAPIView.as_view(), name='deal-list-create'),
    path('deals/<uuid:pk>/', DealRetrieveUpdateDestroyAPIView.as_view(), name='deal-detail'),

    path('tasks/', TaskListCreateAPIView.as_view(), name='task-list-create'),
    path('tasks/<uuid:pk>/', TaskRetrieveUpdateDestroyAPIView.as_view(), name='task-detail'),

    path('integrations/', IntegrationListCreateAPIView.as_view(), name='integration-list-create'),
    path('integrations/<uuid:pk>/', IntegrationRetrieveUpdateDestroyAPIView.as_view(), name='integration-detail'),

    path('analytics/', AnalyticsListAPIView.as_view(), name='analytics-list'),

    path('orders/', OrderListCreateAPIView.as_view(), name='order-list-create'),
    path('orders/<uuid:pk>/', OrderRetrieveUpdateDestroyAPIView.as_view(), name='order-detail'),

    path('products/', ProductListCreateAPIView.as_view(), name='product-list-create'),
    path('products/<uuid:pk>/', ProductRetrieveUpdateDestroyAPIView.as_view(), name='product-detail'),

    path('reviews/', ReviewListCreateAPIView.as_view(), name='review-list-create'),
    path('reviews/<uuid:pk>/', ReviewRetrieveUpdateDestroyAPIView.as_view(), name='review-detail'),

    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/<uuid:pk>/', NotificationDetailView.as_view(), name='notification-detail'),
    path('notifications/mark-all-read/', MarkAllNotificationsReadView.as_view(), name='mark-all-notifications-read'),
    path('events/', EventListCreateAPIView.as_view(), name='event-list-create'),
    path('events/<uuid:pk>/', EventRetrieveUpdateDestroyAPIView.as_view(), name='event-detail'),
    path('warehouses/', WarehouseListCreateAPIView.as_view(), name='warehouse-list-create'),
    path('warehouses/<uuid:id>/', WarehouseRetrieveUpdateDestroyAPIView.as_view(), name='warehouse-retrieve-update-destroy'),

    # URLs для WarehouseEvent (Складские события)
    path('warehouse-events/', WarehouseEventListCreateAPIView.as_view(), name='warehouse-event-list-create'),
    path('warehouse-events/<uuid:id>/', WarehouseEventRetrieveUpdateDestroyAPIView.as_view(), name='warehouse-event-retrieve-update-destroy'),
    path('categories/', ProductCategoryListCreateAPIView.as_view(), name='category-list'),
    path('categories/<uuid:pk>/', ProductCategoryRetrieveUpdateDestroyAPIView.as_view(), name='category-detail'),

    path('brands/', ProductBrandListCreateAPIView.as_view(), name='brand-list'),
    path('brands/<uuid:pk>/', ProductBrandRetrieveUpdateDestroyAPIView.as_view(), name='brand-detail'),
]
