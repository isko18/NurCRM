from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.construction.models import Cashbox, CashFlow, CashShift
from apps.main.models import Branch, Client, Product, ProductCategory, Sale, SupplierReceipt
from apps.main.pos_views import SaleListAPIView
from apps.main.views import ProductListView, ClientListCreateAPIView, ProductCategoryListCreateAPIView, SupplierReceiptListAPIView
from apps.construction.views import CashboxListCreateView, CashFlowListCreateView, CashShiftListView
from apps.users.models import BranchMembership, Company, SubscriptionPlan
from apps.users.views import EmployeeListAPIView

User = get_user_model()


class BranchTabsAPITests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(email=email_owner, password="pass", role="owner")
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(name="Branch Test Co", owner=self.owner, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()

        email_emp = f"emp_{uuid.uuid4().hex[:6]}@test.com"
        self.employee = User.objects.create_user(email=email_emp, password="pass", role="cashier")
        self.employee.company = self.company
        self.employee.save()

        self.branch_a = Branch.objects.create(name="Branch A", company=self.company)
        self.branch_b = Branch.objects.create(name="Branch B", company=self.company)

        # Assign employee ONLY to branch A
        BranchMembership.objects.create(
            user=self.employee,
            branch=self.branch_a,
            is_primary=True,
        )

        # Create items for Branch A
        self.cashbox_a = Cashbox.objects.create(name="Cashbox A", company=self.company, branch=self.branch_a)
        self.shift_a = CashShift.objects.create(company=self.company, branch=self.branch_a, cashbox=self.cashbox_a, cashier=self.employee, status=CashShift.Status.OPEN)
        self.sale_a = Sale.objects.create(company=self.company, branch=self.branch_a, cashbox=self.cashbox_a, user=self.employee, total=Decimal("100.00"), status=Sale.Status.PAID)
        self.prod_a = Product.objects.create(name="Prod A", company=self.company, branch=self.branch_a, price=Decimal("10.00"), quantity=Decimal("5.00"))
        self.cf_a = CashFlow.objects.create(company=self.company, branch=self.branch_a, cashbox=self.cashbox_a, cashier=self.employee, amount=Decimal("100.00"), type=CashFlow.Type.INCOME, status=CashFlow.Status.APPROVED)
        self.client_a = Client.objects.create(full_name="Client A", company=self.company, branch=self.branch_a)
        self.cat_a = ProductCategory.objects.create(name="Cat A", company=self.company, branch=self.branch_a)
        self.rec_a = SupplierReceipt.objects.create(company=self.company, branch=self.branch_a, supplier=self.client_a)

        # Create items for Branch B
        self.cashbox_b = Cashbox.objects.create(name="Cashbox B", company=self.company, branch=self.branch_b)
        self.shift_b = CashShift.objects.create(company=self.company, branch=self.branch_b, cashbox=self.cashbox_b, cashier=self.owner, status=CashShift.Status.OPEN)
        self.sale_b = Sale.objects.create(company=self.company, branch=self.branch_b, cashbox=self.cashbox_b, user=self.owner, total=Decimal("200.00"), status=Sale.Status.PAID)
        self.prod_b = Product.objects.create(name="Prod B", company=self.company, branch=self.branch_b, price=Decimal("20.00"), quantity=Decimal("10.00"))
        self.cf_b = CashFlow.objects.create(company=self.company, branch=self.branch_b, cashbox=self.cashbox_b, cashier=self.owner, amount=Decimal("200.00"), type=CashFlow.Type.INCOME, status=CashFlow.Status.APPROVED)
        self.client_b = Client.objects.create(full_name="Client B", company=self.company, branch=self.branch_b)
        self.cat_b = ProductCategory.objects.create(name="Cat B", company=self.company, branch=self.branch_b)
        self.rec_b = SupplierReceipt.objects.create(company=self.company, branch=self.branch_b, supplier=self.client_b)

        # Create global item without branch
        self.prod_global = Product.objects.create(name="Prod Global", company=self.company, branch=None, price=Decimal("5.00"), quantity=Decimal("1.00"))

    def test_owner_filters_sales_by_branch(self):
        req = self.factory.get(f"/api/main/pos/sales/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = SaleListAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.sale_a.id), ids)
        self.assertNotIn(str(self.sale_b.id), ids)
        self.assertEqual(data[0]["branch"], self.branch_a.id)

    def test_owner_filters_cashboxes_by_branch(self):
        req = self.factory.get(f"/api/construction/cashboxes/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = CashboxListCreateView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.cashbox_a.id), ids)
        self.assertNotIn(str(self.cashbox_b.id), ids)
        self.assertIn("balance", data[0])
        self.assertIn("currency", data[0])
        self.assertIn("is_active", data[0])

    def test_owner_filters_shifts_by_branch(self):
        req = self.factory.get(f"/api/construction/shifts/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = CashShiftListView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.shift_a.id), ids)
        self.assertNotIn(str(self.shift_b.id), ids)

    def test_owner_filters_products_by_branch(self):
        # Strict branch filtering: does not return global or other branch
        req = self.factory.get(f"/api/main/products/list/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = ProductListView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.prod_a.id), ids)
        self.assertNotIn(str(self.prod_b.id), ids)
        self.assertNotIn(str(self.prod_global.id), ids)

    def test_owner_filters_products_include_global(self):
        req = self.factory.get(f"/api/main/products/list/?branch={self.branch_a.id}&include_global=1")
        force_authenticate(req, user=self.owner)
        view = ProductListView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.prod_a.id), ids)
        self.assertIn(str(self.prod_global.id), ids)
        self.assertNotIn(str(self.prod_b.id), ids)

    def test_owner_filters_cashflows_by_branch(self):
        req = self.factory.get(f"/api/construction/cashflows/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = CashFlowListCreateView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.cf_a.id), ids)
        self.assertNotIn(str(self.cf_b.id), ids)

    def test_owner_filters_clients_by_branch(self):
        req = self.factory.get(f"/api/main/clients/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = ClientListCreateAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.client_a.id), ids)
        self.assertNotIn(str(self.client_b.id), ids)

    def test_owner_filters_supplier_receipts_by_branch(self):
        req = self.factory.get(f"/api/main/suppliers/receipts/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = SupplierReceiptListAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [item["id"] for item in data]
        self.assertIn(str(self.rec_a.id), ids)
        self.assertNotIn(str(self.rec_b.id), ids)

    def test_owner_filters_employees_by_branch(self):
        req = self.factory.get(f"/api/users/employees/?branch={self.branch_a.id}")
        force_authenticate(req, user=self.owner)
        view = EmployeeListAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("results", resp.data)
        ids = [str(item["id"]) for item in data]
        self.assertIn(str(self.employee.id), ids)

    def test_employee_forbidden_other_branch(self):
        req = self.factory.get(f"/api/main/pos/sales/?branch={self.branch_b.id}")
        force_authenticate(req, user=self.employee)
        view = SaleListAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 403)

    def test_invalid_uuid_raises_400(self):
        req = self.factory.get("/api/main/pos/sales/?branch=not-a-uuid")
        force_authenticate(req, user=self.owner)
        view = SaleListAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_branch_raises_404(self):
        random_uuid = uuid.uuid4()
        req = self.factory.get(f"/api/main/pos/sales/?branch={random_uuid}")
        force_authenticate(req, user=self.owner)
        view = SaleListAPIView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 404)
