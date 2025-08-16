# barber_crm/views.py
from rest_framework import generics, permissions
from django_filters.rest_framework import DjangoFilterBackend
from .models import BarberProfile, Service, Client, Appointment
from .serializers import (
    BarberProfileSerializer,
    ServiceSerializer,
    ClientSerializer,
    AppointmentSerializer
)


class CompanyQuerysetMixin:
    """
    Миксин для скоупа по компании текущего пользователя + защита company на update.
    """
    def get_queryset(self):
        # Fix для drf_yasg
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset.none()

        qs = super().get_queryset()
        company = getattr(self.request.user, 'company', None) or getattr(self.request.user, 'owned_company', None)
        return qs.filter(company=company) if company else qs

    def perform_create(self, serializer):
        company = getattr(self.request.user, 'company', None) or getattr(self.request.user, 'owned_company', None)
        serializer.save(company=company)

    def perform_update(self, serializer):
        """
        Не даём сменить company при обновлении.
        """
        company = getattr(self.request.user, 'company', None) or getattr(self.request.user, 'owned_company', None)
        serializer.save(company=company)


# ==== Barber ====
class BarberListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = BarberProfile.objects.all()
    serializer_class = BarberProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in BarberProfile._meta.get_fields() if not f.is_relation or f.many_to_one]


class BarberRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BarberProfile.objects.all()
    serializer_class = BarberProfileSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Service ====
class ServiceListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Service._meta.get_fields() if not f.is_relation or f.many_to_one]


class ServiceRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Client ====
class ClientListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Client._meta.get_fields() if not f.is_relation or f.many_to_one]


class ClientRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Appointment ====
class AppointmentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = (Appointment.objects
                .select_related('client', 'barber', 'service')
                .all())
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = [f.name for f in Appointment._meta.get_fields() if not f.is_relation or f.many_to_one]


class AppointmentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = (Appointment.objects
                .select_related('client', 'barber', 'service')
                .all())
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]
