from django.db import models
from django.core.exceptions import ValidationError
# from django.contrib.auth.models import User
from apps.users.models import User

class Hotel(models.Model):
    name = models.CharField(max_length=200)
   # image = models.ImageField(upload_to='hotels/', blank=True, null=True)  # поменяли на ImageField
    capacity = models.IntegerField()
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return self.name

class ConferenceRoom(models.Model):  # ← это Room
    name = models.CharField(max_length=100)
    capacity = models.IntegerField()
    location = models.CharField(max_length=255)

    def __str__(self):
        return self.name

class Booking(models.Model):
    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, null=True, blank=True)
    room = models.ForeignKey(ConferenceRoom, on_delete=models.CASCADE, null=True, blank=True)
    reserved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    purpose = models.CharField(max_length=255, blank=True)

    def clean(self):
        # Проверяем, что выбрали ровно один из вариантов: hotel или room
        if (self.hotel and self.room) or (not self.hotel and not self.room):
            raise ValidationError("Выберите либо гостиницу, либо комнату, но не обе одновременно.")

    def __str__(self):
        # Чтобы не было ошибки, если hotel или room нет
        hotel_name = self.hotel.name if self.hotel else "No Hotel"
        room_name = self.room.name if self.room else "No Room"
        reserved_by_name = self.reserved_by.username if self.reserved_by else "Unknown"
        return f"{hotel_name} / {room_name} by {reserved_by_name}"

class ManagerAssignment(models.Model):
    room = models.OneToOneField(ConferenceRoom, on_delete=models.CASCADE)
    manager = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.manager} manages {self.room}"



