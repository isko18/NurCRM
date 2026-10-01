import os, sys, django
sys.path.append('/staging/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from apps.users.models import User, Company, SubscriptionPlan
from apps.construction.models import Cashbox, CashShift
from apps.construction.views import CashShiftOpenView

email = "test_shift_open_user@test.com"
User.objects.filter(email=email).delete()

plan, _ = SubscriptionPlan.objects.get_or_create(name="Pro", defaults={"price": 100})
test_user = User.objects.create_user(email=email, password="TestPassword123!", role="owner", first_name="Тест")
company = Company.objects.create(name="Тест Компания Смены", owner=test_user, subscription_plan=plan)
test_user.company = company
test_user.save()

cashbox = Cashbox.objects.create(company=company, name="Тест Касса 1")

factory = APIRequestFactory()

def test_open_shift():
    req = factory.post("/api/construction/shifts/open/", {"opening_cash": "100.00"}, format="json")
    force_authenticate(req, user=test_user)
    view = CashShiftOpenView.as_view()
    resp = view(req)
    if hasattr(resp, 'render'):
        resp.render()
    print(f"RESPONSE STATUS: {resp.status_code}")
    print(f"RESPONSE DATA: {resp.data}")
    return resp.status_code

status_code = test_open_shift()
if status_code == 201:
    print("SUCCESS! CashShiftOpenView returned 201 Created!")
else:
    print(f"FAILED with status code {status_code}")

# Cleanup
test_user.delete()
company.delete()
