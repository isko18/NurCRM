# barber_crm/admin.py
from django.contrib import admin
from .models import BarberProfile, Service, Client, Appointment


@admin.register(BarberProfile)
class BarberProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'company', 'phone', 'extra_phone', 'is_active', 'created_at')
    list_filter = ('company', 'is_active')
    search_fields = ('full_name', 'phone', 'extra_phone')
    ordering = ('full_name',)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'price', 'is_active')
    list_filter = ('company', 'is_active')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'company', 'phone', 'email', 'birth_date', 'status', 'created_at')
    list_filter = ('company', 'status')
    search_fields = ('full_name', 'phone', 'email')
    ordering = ('full_name',)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        'client', 'barber', 'service', 'start_at', 'end_at', 'status', 'company'
    )
    list_filter = ('company', 'status', 'barber', 'service')
    search_fields = ('client__full_name', 'barber__full_name', 'service__name')
    date_hierarchy = 'start_at'
    ordering = ('-start_at',)
