import os
import time
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.db import connection, reset_queries
from django.conf import settings
settings.DEBUG = True
reset_queries()

from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.filter(company__isnull=False).first()

client = APIClient(HTTP_HOST="stageapp.nurcrm.kg", HTTP_X_FORWARDED_PROTO="https")
client.force_authenticate(user=user)

reset_queries()
t0 = time.time()
res = client.get("/api/main/products/list/?page=1&page_size=20")
t1 = time.time()

print("Status Code:", res.status_code)
print("Total Response Time: {:.1f} ms".format((t1 - t0)*1000))
print("Total SQL Queries Executed:", len(connection.queries))

sql_time = sum(float(q["time"]) for q in connection.queries) * 1000
print("Total SQL Execution Time: {:.1f} ms".format(sql_time))

print("\n--- TOP 10 SLOWEST SQL QUERIES ---")
sorted_q = sorted(connection.queries, key=lambda x: float(x["time"]), reverse=True)
for q in sorted_q[:10]:
    print("[{:.1f} ms] {}".format(float(q["time"])*1000, q["sql"][:250]))
