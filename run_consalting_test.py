import os, sys, django
sys.path.append('/home/nur')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from apps.users.models import User, Company, SubscriptionPlan

email = "test_consalting_new_user_456@test.com"
User.objects.filter(email=email).delete()

plan, _ = SubscriptionPlan.objects.get_or_create(name="Pro", defaults={"price": 100})
owner_user = User.objects.create_user(
    email=email,
    password="TestPassword123!",
    first_name="Тест",
    last_name="Консалтинг",
    role="owner"
)
company = Company.objects.create(
    name="Тестовая Компания Консалтинг",
    owner=owner_user,
    subscription_plan=plan
)
owner_user.company = company
owner_user.save()

factory = APIRequestFactory()

results = {
    "total": 0,
    "success": 0,
    "failed_500": 0,
    "failed_other": 0,
    "details": []
}

def test_endpoint(name, method, url, view_class, data=None, kwargs=None, expected_status=None):
    if expected_status is None:
        expected_status = [200, 201, 204]
    results["total"] += 1
    req_func = getattr(factory, method.lower())
    if data is not None:
        request = req_func(url, data, format='json')
    else:
        request = req_func(url)
    
    force_authenticate(request, user=owner_user)
    view = view_class.as_view() if hasattr(view_class, 'as_view') else view_class
    
    status_code = None
    resp_data = None
    error_msg = None
    
    try:
        if kwargs:
            response = view(request, **kwargs)
        else:
            response = view(request)
            
        if hasattr(response, 'render'):
            response.render()
            
        status_code = response.status_code
        resp_data = getattr(response, 'data', str(getattr(response, 'content', '')))
    except Exception as e:
        status_code = 500
        error_msg = f"EXCEPTION: {str(e)}"
    
    is_ok = (status_code in expected_status)
    if is_ok:
        results["success"] += 1
    elif status_code == 500:
        results["failed_500"] += 1
    else:
        results["failed_other"] += 1
        
    entry = {
        "name": name,
        "method": method,
        "url": url,
        "status": status_code,
        "ok": is_ok,
        "error": error_msg or (resp_data if not is_ok else None)
    }
    results["details"].append(entry)
    tag = "OK" if is_ok else ("FAIL_500" if status_code == 500 else f"FAIL_{status_code}")
    print(f"[{tag}] {method} {url} -> {status_code}")
    return status_code, resp_data, entry

print("=== TESTING ALL CONSULTING ENDPOINTS WITH NEW USER ACCOUNT ===")

from apps.consalting.views import (
    ServicesConsaltingListCreateView,
    ServicesConsaltingRetrieveUpdateDestroyView,
    SaleConsaltingListCreateView,
    SaleConsaltingRetrieveUpdateDestroyView,
    SaleConsaltingAnalyticsView,
    ConsaltingDashboardAnalyticsView,
    ConsaltingMessengerAnalyticsView,
    ConsaltingSourceAnalyticsView,
    ConsaltingManagerAnalyticsView,
    SalaryConsaltingListCreateView,
    SalaryConsaltingRetrieveUpdateDestroyView,
    RequestsConsaltingListCreateView,
    RequestsConsaltingRetrieveUpdateDestroyView,
    BookingConsaltingListCreateView,
    BookingConsaltingRetrieveUpdateDestroyView,
    ClientConsaltingListCreateView,
    ClientConsaltingRetrieveUpdateDestroyView,
    FunnelConsaltingListCreateView,
    FunnelConsaltingRetrieveUpdateDestroyView,
    FunnelForRoleView,
    FunnelBoardView,
    FunnelBoardsView,
    FunnelEmployeesView,
    FunnelStageConsaltingListCreateView,
    FunnelStageConsaltingRetrieveUpdateDestroyView,
    LeadConsaltingListCreateView,
    LeadConsaltingRetrieveUpdateDestroyView,
    LeadMoveStageView,
    LeadClaimView,
    LeadReleaseView,
    LeadMarkMessagesReadView,
    LeadArchivedListView,
    LeadAllowedTransitionsView,
    LeadTimelineView,
    LeadActivityCreateView,
    LeadRecalculateScoreView,
    LeadFunnelHistoryView,
    LeadTaskListCreateView,
    LeadTaskRetrieveUpdateDestroyView,
    LossReasonListCreateView,
    LossReasonRetrieveUpdateDestroyView,
    FunnelAnalyticsView,
    ServiceSalaryRateListView,
    SalaryAccrualListView,
    SalarySummaryView,
    SalaryPayoutListCreateView,
    SalarySchemesListView,
    SalaryDefaultsView,
    BonusRuleListCreateView,
    BonusProgressView,
    SalaryAdjustmentListCreateView,
    SalaryPayslipView,
    InboundLeadListCreateView,
    InboundLeadCountersView,
    InboundLeadAnalyticsView,
    LeadDistributionSettingsView,
    SubscriptionMatrixView,
    EmployeeStatsView,
    EmployeesRatingView,
    EmployeeActivityView,
    SalesPlanListCreateView,
    EmployeeFinanceView,
    EmployeeSalesView,
    EmployeeHandoversView,
    EmployeeDebtsView,
    CashRequestsListView,
    CashRequestsCountersView,
    CashOperationsListView,
    CashConfirmationSettingsView,
)

from apps.consalting.wazzup_views import (
    WazzupChatListView,
    WazzupCredentialsView,
)

# 1. Services
test_endpoint("Services List", "GET", "/api/consalting/services/", ServicesConsaltingListCreateView)
status, service_data, _ = test_endpoint("Service Create", "POST", "/api/consalting/services/", ServicesConsaltingListCreateView, data={"name": "Консультация VIP", "price": "5000.00"})

service_id = service_data.get("id") if isinstance(service_data, dict) else None
if service_id:
    test_endpoint("Service Detail", "GET", f"/api/consalting/services/{service_id}/", ServicesConsaltingRetrieveUpdateDestroyView, kwargs={"pk": service_id})

# 2. Clients
test_endpoint("Clients List", "GET", "/api/consalting/clients/", ClientConsaltingListCreateView)
status, client_data, _ = test_endpoint("Client Create", "POST", "/api/consalting/clients/", ClientConsaltingListCreateView, data={"full_name": "Иван Клиент", "phone": "+996555112233"})

client_id = client_data.get("id") if isinstance(client_data, dict) else None
if client_id:
    test_endpoint("Client Detail", "GET", f"/api/consalting/clients/{client_id}/", ClientConsaltingRetrieveUpdateDestroyView, kwargs={"pk": client_id})

# 3. Funnels & Stages
test_endpoint("Funnels List", "GET", "/api/consalting/funnels/", FunnelConsaltingListCreateView)
test_endpoint("Funnels For Role", "GET", "/api/consalting/funnels/for-role/", FunnelForRoleView)
test_endpoint("Funnels Boards", "GET", "/api/consalting/funnels/boards/", FunnelBoardsView)

status, funnel_data, _ = test_endpoint("Funnel Create", "POST", "/api/consalting/funnels/", FunnelConsaltingListCreateView, data={"name": "Воронка Продаж Услуг"})
funnel_id = funnel_data.get("id") if isinstance(funnel_data, dict) else None

if funnel_id:
    test_endpoint("Funnel Detail", "GET", f"/api/consalting/funnels/{funnel_id}/", FunnelConsaltingRetrieveUpdateDestroyView, kwargs={"pk": funnel_id})
    test_endpoint("Funnel Board", "GET", f"/api/consalting/funnels/{funnel_id}/board/", FunnelBoardView, kwargs={"pk": funnel_id})
    test_endpoint("Funnel Employees", "GET", f"/api/consalting/funnels/{funnel_id}/employees/", FunnelEmployeesView, kwargs={"pk": funnel_id})
    test_endpoint("Funnel Analytics", "GET", f"/api/consalting/funnels/{funnel_id}/analytics/", FunnelAnalyticsView, kwargs={"pk": funnel_id})

# Funnel Stages
test_endpoint("Stages List", "GET", "/api/consalting/funnel-stages/", FunnelStageConsaltingListCreateView)
stage_id = None
if funnel_id:
    status, stage_data, _ = test_endpoint("Stage Create", "POST", "/api/consalting/funnel-stages/", FunnelStageConsaltingListCreateView, data={"funnel": funnel_id, "name": "Первичный Контакт", "order": 1})
    stage_id = stage_data.get("id") if isinstance(stage_data, dict) else None
    if stage_id:
        test_endpoint("Stage Detail", "GET", f"/api/consalting/funnel-stages/{stage_id}/", FunnelStageConsaltingRetrieveUpdateDestroyView, kwargs={"pk": stage_id})

# 4. Leads
test_endpoint("Leads List", "GET", "/api/consalting/leads/", LeadConsaltingListCreateView)
test_endpoint("Archived Leads List", "GET", "/api/consalting/leads/archived/", LeadArchivedListView)

lead_payload = {"title": "Лид на консалтинг"}
if funnel_id:
    lead_payload["funnel"] = funnel_id
if stage_id:
    lead_payload["stage"] = stage_id

status, lead_data, _ = test_endpoint("Lead Create", "POST", "/api/consalting/leads/", LeadConsaltingListCreateView, data=lead_payload)
lead_id = lead_data.get("id") if isinstance(lead_data, dict) else None

if lead_id:
    test_endpoint("Lead Detail", "GET", f"/api/consalting/leads/{lead_id}/", LeadConsaltingRetrieveUpdateDestroyView, kwargs={"pk": lead_id})
    test_endpoint("Lead Allowed Transitions", "GET", f"/api/consalting/leads/{lead_id}/allowed-transitions/", LeadAllowedTransitionsView, kwargs={"pk": lead_id})
    test_endpoint("Lead Timeline", "GET", f"/api/consalting/leads/{lead_id}/timeline/", LeadTimelineView, kwargs={"pk": lead_id})
    test_endpoint("Lead Funnel History", "GET", f"/api/consalting/leads/{lead_id}/funnel-history/", LeadFunnelHistoryView, kwargs={"pk": lead_id})
    test_endpoint("Lead Claim", "POST", f"/api/consalting/leads/{lead_id}/claim/", LeadClaimView, kwargs={"pk": lead_id})
    test_endpoint("Lead Release", "POST", f"/api/consalting/leads/{lead_id}/release/", LeadReleaseView, kwargs={"pk": lead_id})
    test_endpoint("Lead Mark Read", "POST", f"/api/consalting/leads/{lead_id}/mark-read/", LeadMarkMessagesReadView, kwargs={"pk": lead_id})
    test_endpoint("Lead Recalculate Score", "POST", f"/api/consalting/leads/{lead_id}/recalculate-score/", LeadRecalculateScoreView, kwargs={"pk": lead_id})
    test_endpoint("Lead Activity Create", "POST", f"/api/consalting/leads/{lead_id}/activities/", LeadActivityCreateView, data={"activity_type": "note", "comment": "Звонок клиенту"}, kwargs={"pk": lead_id})

# 5. Lead Tasks & Loss Reasons
test_endpoint("Lead Tasks List", "GET", "/api/consalting/lead-tasks/", LeadTaskListCreateView)
if lead_id:
    status, task_data, _ = test_endpoint("Lead Task Create", "POST", "/api/consalting/lead-tasks/", LeadTaskListCreateView, data={"lead": lead_id, "title": "Перезвонить клиенту"})
    task_id = task_data.get("id") if isinstance(task_data, dict) else None
    if task_id:
        test_endpoint("Lead Task Detail", "GET", f"/api/consalting/lead-tasks/{task_id}/", LeadTaskRetrieveUpdateDestroyView, kwargs={"pk": task_id})

test_endpoint("Loss Reasons List", "GET", "/api/consalting/loss-reasons/", LossReasonListCreateView)
status, loss_data, _ = test_endpoint("Loss Reason Create", "POST", "/api/consalting/loss-reasons/", LossReasonListCreateView, data={"name": "Высокая цена"})
loss_id = loss_data.get("id") if isinstance(loss_data, dict) else None
if loss_id:
    test_endpoint("Loss Reason Detail", "GET", f"/api/consalting/loss-reasons/{loss_id}/", LossReasonRetrieveUpdateDestroyView, kwargs={"pk": loss_id})

# 6. Inbound Leads
test_endpoint("Inbound Leads List", "GET", "/api/consalting/inbound-leads/", InboundLeadListCreateView)
test_endpoint("Inbound Leads Counters", "GET", "/api/consalting/inbound-leads/counters/", InboundLeadCountersView)
test_endpoint("Inbound Leads Analytics", "GET", "/api/consalting/inbound-leads/analytics/", InboundLeadAnalyticsView)
test_endpoint("Lead Distribution Settings", "GET", "/api/consalting/lead-distribution/", LeadDistributionSettingsView)

# 7. Sales, Bookings, Requests
test_endpoint("Sales List", "GET", "/api/consalting/sales/", SaleConsaltingListCreateView)
test_endpoint("Sales Analytics", "GET", "/api/consalting/sales/analytics/", SaleConsaltingAnalyticsView)

sale_payload = {"amount": "1000.00"}
if client_id:
    sale_payload["client"] = client_id
if service_id:
    sale_payload["service"] = service_id

status, sale_data, _ = test_endpoint("Sale Create", "POST", "/api/consalting/sales/", SaleConsaltingListCreateView, data=sale_payload)
sale_id = sale_data.get("id") if isinstance(sale_data, dict) else None
if sale_id:
    test_endpoint("Sale Detail", "GET", f"/api/consalting/sales/{sale_id}/", SaleConsaltingRetrieveUpdateDestroyView, kwargs={"pk": sale_id})

test_endpoint("Requests List", "GET", "/api/consalting/requests/", RequestsConsaltingListCreateView)
test_endpoint("Bookings List", "GET", "/api/consalting/bookings/", BookingConsaltingListCreateView)

# 8. Analytics & Dashboard
test_endpoint("Consulting General Analytics", "GET", "/api/consalting/analytics/", SaleConsaltingAnalyticsView)
test_endpoint("Analytics Dashboard", "GET", "/api/consalting/analytics/dashboard/", ConsaltingDashboardAnalyticsView)
test_endpoint("Analytics Messenger", "GET", "/api/consalting/analytics/messenger/", ConsaltingMessengerAnalyticsView)
test_endpoint("Analytics Sources", "GET", "/api/consalting/analytics/sources/", ConsaltingSourceAnalyticsView)
test_endpoint("Analytics Managers", "GET", "/api/consalting/analytics/managers/", ConsaltingManagerAnalyticsView)

# 9. Employee & Rating & Finance
test_endpoint("Employees Rating", "GET", "/api/consalting/employees/rating/", EmployeesRatingView)
test_endpoint("Employee Stats", "GET", f"/api/consalting/employees/{owner_user.id}/stats/", EmployeeStatsView, kwargs={"pk": owner_user.id})
test_endpoint("Employee Activity", "GET", f"/api/consalting/employees/{owner_user.id}/activity/", EmployeeActivityView, kwargs={"pk": owner_user.id})
test_endpoint("Employee Finance", "GET", f"/api/consalting/employees/{owner_user.id}/finance/", EmployeeFinanceView, kwargs={"pk": owner_user.id})
test_endpoint("Employee Sales", "GET", f"/api/consalting/employees/{owner_user.id}/sales/", EmployeeSalesView, kwargs={"pk": owner_user.id})
test_endpoint("Employee Handovers", "GET", f"/api/consalting/employees/{owner_user.id}/handovers/", EmployeeHandoversView, kwargs={"pk": owner_user.id})
test_endpoint("Employee Debts", "GET", f"/api/consalting/employees/{owner_user.id}/debts/", EmployeeDebtsView, kwargs={"pk": owner_user.id})
test_endpoint("Sales Plans List", "GET", "/api/consalting/sales-plans/", SalesPlanListCreateView)

# 10. Cashbox & Operations
test_endpoint("Cashbox Requests", "GET", "/api/consalting/cashbox/requests/", CashRequestsListView)
test_endpoint("Cashbox Requests Counters", "GET", "/api/consalting/cashbox/requests/counters/", CashRequestsCountersView)
test_endpoint("Cashbox Operations", "GET", "/api/consalting/cashbox/operations/", CashOperationsListView)
test_endpoint("Cashbox Confirmation Settings", "GET", "/api/consalting/cashbox/confirmation-settings/", CashConfirmationSettingsView)

# 11. Salary System
test_endpoint("Salary Schemes", "GET", "/api/consalting/salary/schemes/", SalarySchemesListView)
test_endpoint("Salary Defaults", "GET", "/api/consalting/salary/defaults/", SalaryDefaultsView)
test_endpoint("Salary Rates", "GET", "/api/consalting/salary/rates/", ServiceSalaryRateListView)
test_endpoint("Salary Bonus Rules", "GET", "/api/consalting/salary/bonus-rules/", BonusRuleListCreateView)
test_endpoint("Salary Bonus Progress", "GET", "/api/consalting/salary/bonus-progress/", BonusProgressView)
test_endpoint("Salary Adjustments", "GET", "/api/consalting/salary/adjustments/", SalaryAdjustmentListCreateView)
test_endpoint("Salary Accruals", "GET", "/api/consalting/salary/accruals/", SalaryAccrualListView)
test_endpoint("Salary Payslip", "GET", "/api/consalting/salary/payslip/", SalaryPayslipView)
test_endpoint("Salary Summary", "GET", "/api/consalting/salary/summary/", SalarySummaryView)
test_endpoint("Salary Payouts", "GET", "/api/consalting/salary/payouts/", SalaryPayoutListCreateView)
test_endpoint("Salaries List", "GET", "/api/consalting/salaries/", SalaryConsaltingListCreateView)

# 12. Subscriptions Matrix
test_endpoint("Subscription Matrix", "GET", "/api/consalting/subscription-matrix/", SubscriptionMatrixView)

# 13. Wazzup & Messenger
test_endpoint("Wazzup Credentials", "GET", "/api/consalting/wazzup-credentials/", WazzupCredentialsView)
test_endpoint("Wazzup Chats List", "GET", "/api/consalting/chats/", WazzupChatListView)

# Cleanup
owner_user.delete()
company.delete()

print("TOTAL_ENDPOINTS=" + str(results['total']))
print("SUCCESS_COUNT=" + str(results['success']))
print("FAILED_500_COUNT=" + str(results['failed_500']))
print("FAILED_OTHER_COUNT=" + str(results['failed_other']))

print("
--- DETAILED ERRORS ---")
for d in results["details"]:
    if not d["ok"]:
        print("FAIL [" + str(d['status']) + "] " + d['method'] + " " + d['url'] + " -> " + str(d['error']))

