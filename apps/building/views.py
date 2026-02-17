from rest_framework import generics, permissions

from .models import ResidentialComplex
from .serializers import ResidentialComplexSerializer, ResidentialComplexCreateSerializer


class CompanyQuerysetMixin:
    """Ограничение выборки объектами компании текущего пользователя."""

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and getattr(user, "company_id", None):
            return qs.filter(company_id=user.company_id)
        if not getattr(user, "is_staff", False) and not getattr(user, "is_superuser", False):
            return qs.none()
        return qs


class ResidentialComplexListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    """
    GET  /api/building/objects/  — список ЖК компании.
    POST /api/building/objects/  — создание ЖК (company из user).
    """
    permission_classes = [permissions.IsAuthenticated]
    queryset = ResidentialComplex.objects.all()

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ResidentialComplexCreateSerializer
        return ResidentialComplexSerializer


class ResidentialComplexDetailView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/building/objects/<uuid>/
    PATCH  /api/building/objects/<uuid>/
    PUT    /api/building/objects/<uuid>/
    DELETE /api/building/objects/<uuid>/
    """
    permission_classes = [permissions.IsAuthenticated]
    queryset = ResidentialComplex.objects.all()
    serializer_class = ResidentialComplexSerializer
