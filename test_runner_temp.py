
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
sys.path.insert(0, '/home/nur')
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from apps.users.models import User
from apps.main.models import Company
from apps.consalting.models import FunnelConsalting, FunnelStageConsalting, LeadConsalting
from apps.consalting.views import (
    LeadConsaltingListCreateView,
    LeadRegisterPaymentView,
    SubscriptionConsaltingListCreateView,
    SubscriptionAccrualConsaltingListCreateView,
    SalaryConsaltingListCreateView,
    BonusRuleConsaltingListCreateView,
    CashConfirmationListView
)

user = User.objects.filter(is_superuser=True).first() or User.objects.first()
print(f"Testing with user: {user}")

factory = APIRequestFactory()

def test_view(name, view_class, method='GET', data=None, url='/'):
    try:
        view = view_class.as_view()
        if method == 'GET':
            req = factory.get(url)
        else:
            req = factory.post(url, data=data, format='json')
        force_authenticate(req, user=user)
        res = view(req)
        print(f"[{res.status_code}] {name}")
        if res.status_code >= 400:
            print(f"   Data: {getattr(res, 'data', None)}")
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        import traceback
        traceback.print_exc()

test_view("Leads List", LeadConsaltingListCreateView, 'GET', url='/api/consalting/leads/')
test_view("Subscriptions List", SubscriptionConsaltingListCreateView, 'GET', url='/api/consalting/subscriptions/')
test_view("Subscription Accruals List", SubscriptionAccrualConsaltingListCreateView, 'GET', url='/api/consalting/subscription-accruals/')
test_view("Salaries List", SalaryConsaltingListCreateView, 'GET', url='/api/consalting/salaries/')
test_view("Bonus Rules List", BonusRuleConsaltingListCreateView, 'GET', url='/api/consalting/bonus-rules/')
test_view("Cash Confirmations List", CashConfirmationListView, 'GET', url='/api/consalting/cash-confirmations/')
