import os
import time
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.filter(company__isnull=False).first()

client = APIClient(HTTP_HOST="stageapp.nurcrm.kg", HTTP_X_FORWARDED_PROTO="https")
client.force_authenticate(user=user)

endpoints = [
    "/api/users/profile/",
    "/api/users/company/",
    "/api/main/products/list/?page=1&page_size=20",
    "/api/consalting/leads/?page=1&page_size=20",
    "/api/main/debts/",
    "/api/construction/cashboxes/",
    "/api/cafe/orders/?status=open",
]

print("=== major endpoint response time benchmark ===")
for ep in endpoints:
    t0 = time.time()
    res = client.get(ep)
    dt = (time.time() - t0) * 1000
    print("{:<50} | Status: {} | Time: {:.1f} ms".format(ep, res.status_code, dt))
