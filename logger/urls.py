from django.urls import path
from .views import photo_view, save_location

urlpatterns = [
    path("", photo_view, name="photo"),
    path("save-location/", save_location, name="save_location"),
]
