"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings

from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

# Настройки для документации Swagger и ReDoc
schema_view = get_schema_view(
   openapi.Info(
      title="Nur CRM API",
      default_version='v1',
      description="API для проекта Nur CRM",
      terms_of_service="#",
      contact=openapi.Contact(email="support@NurCRM.com"),
      license=openapi.License(name="Nur CRM License"),
   ),
   public=True,
   permission_classes=(permissions.AllowAny,),
)

# Пути для включения различных приложений
apps_includes = [
    path('main/', include('apps.main.urls')),  
    path('users/', include('apps.users.urls')),  
    path('construction/', include('apps.construction.urls')),  
]

# API-роуты
api_urlpatterns = [
    path('api/', include(apps_includes)),
]

# Основные пути проекта
urlpatterns = [
    path('admin/', admin.site.urls),  # Админка

    # Подключение API
    path('', include(api_urlpatterns)),

    # Swagger и ReDoc для документации
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
]

# Статические и медиафайлы
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)