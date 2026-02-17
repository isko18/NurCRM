from django.urls import path

from .views import ResidentialComplexListCreateView, ResidentialComplexDetailView

app_name = "building"

urlpatterns = [
    path("objects/", ResidentialComplexListCreateView.as_view(), name="residential-complex-list-create"),
    path("objects/<uuid:pk>/", ResidentialComplexDetailView.as_view(), name="residential-complex-detail"),
]
