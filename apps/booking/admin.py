from django.contrib import admin
from .models import ConferenceRoom, Booking, ManagerAssignment, Hotel

admin.site.register(ConferenceRoom)
admin.site.register(Booking)
admin.site.register(ManagerAssignment)
admin.site.register(Hotel)


