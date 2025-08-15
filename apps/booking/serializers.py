
from rest_framework import serializers
from .models import Hotel
from .models import ConferenceRoom, Booking, ManagerAssignment

class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConferenceRoom
        fields = '__all__'

class BookingSerializer(serializers.ModelSerializer):


    class Meta:
        model = Booking
        fields = '__all__'

class ManagerAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerAssignment
        fields = '__all__'


class HotelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hotel
        # fields = '__all__'  # убрать, чтобы исключить image
        fields = ['id', 'name', 'capacity', 'description', 'price']