# barber_crm/views.py
from rest_framework import generics, permissions
from .models import BarberProfile, Service, Client, Appointment
from .serializers import (
    BarberProfileSerializer,
    ServiceSerializer,
    ClientSerializer,
    AppointmentSerializer
)


class CompanyQuerysetMixin:
    """
    Миксин для фильтрации по компании текущего пользователя
    (ожидается, что у пользователя есть company или owned_company).
    """
    def get_queryset(self):
        qs = super().get_queryset()
        company = getattr(self.request.user, 'company', None) or getattr(self.request.user, 'owned_company', None)
        return qs.filter(company=company) if company else qs

    def perform_create(self, serializer):
        company = getattr(self.request.user, 'company', None) or getattr(self.request.user, 'owned_company', None)
        if company:
            serializer.save(company=company)
        else:
            serializer.save()


# ==== Barber ====
class BarberListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = BarberProfile.objects.all()
    serializer_class = BarberProfileSerializer
    permission_classes = [permissions.IsAuthenticated]


class BarberRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BarberProfile.objects.all()
    serializer_class = BarberProfileSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Service ====
class ServiceListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]


class ServiceRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Client ====
class ClientListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated]


class ClientRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [permissions.IsAuthenticated]


# ==== Appointment ====
class AppointmentListCreateView(CompanyQuerysetMixin, generics.ListCreateAPIView):
    queryset = Appointment.objects.select_related('client', 'barber', 'service').all()
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]


class AppointmentRetrieveUpdateDestroyView(CompanyQuerysetMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Appointment.objects.select_related('client', 'barber', 'service').all()
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated]
