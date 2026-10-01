from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Visitor
import json

def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip

def photo_view(request):
    # просто рендерим страницу, без записи в базу
    ip = get_client_ip(request)
    return render(request, "photo.html", {"ip": ip})

@csrf_exempt
def save_location(request):
    if request.method == "POST":
        data = json.loads(request.body)
        lat = data.get("latitude")
        lon = data.get("longitude")

        ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "unknown")

        Visitor.objects.create(
            ip_address=ip,
            user_agent=user_agent,
            latitude=lat,
            longitude=lon,
        )

        return JsonResponse({"status": "saved"})
    return JsonResponse({"status": "error"}, status=400)
