from django.http import JsonResponse
import os

def health(request):
    return JsonResponse({"status": "ok"})

def version(request):
    return JsonResponse({
        "app": "mini-crm-backend",
        "env": "prod" if os.getenv("DEBUG","0")!="1" else "dev"
    })
