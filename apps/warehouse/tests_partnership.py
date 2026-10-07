"""Партнёрство компаний: права, согласие партнёра, история (stock-partnership.md §9, T1–T30)."""
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.warehouse import models, services_money

User = get_user_model()
Company = apps.get_model("users", "Company")
Branch = apps.get_model("users", "Branch")
W = "/api/warehouse/"


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class PartnershipBase(TestCase):
    def make_company(self, tag):
        owner = User.objects.create(email=f"{tag}@t.kg", password="x", first_name=tag, role="owner")
        company = Company.objects.create(name=f"Компания {tag}", owner=owner)
        owner.company = company
        owner.save()
        branch = Branch.objects.create(company=company, name=f"Филиал {tag}")
        wh = models.Warehouse.objects.create(name=f"Склад {tag}", company=company, branch=branch, location="")
        cat = models.WarehouseProductCategory.objects.create(name=f"Кат {tag}", company=company, branch=branch)
        prod = models.WarehouseProduct.objects.create(
            company=company, branch=branch, warehouse=wh, category=cat, name=f"Нори {tag}", code=f"N{tag}",
            barcode=f"460000000{len(tag)}{ord(tag[0])}", unit="шт", quantity=Decimal("0"),
            purchase_price=Decimal("12.50"), price=Decimal("20.00"),
        )
        models.StockBalance.objects.create(warehouse=wh, product=prod, qty=Decimal("10.000"))
        cash = models.CashRegister.objects.create(company=company, branch=branch, name=f"Касса {tag}")
        for br in (branch, None):
            models.PaymentCategory.objects.get_or_create(company=company, branch=br, title="Инкассация",
                                                         defaults={"system_code": "incassation"})
        cat_in, _ = models.PaymentCategory.objects.get_or_create(company=company, branch=branch, title="Приход")
        models.MoneyDocument.objects.create(
            doc_type=models.MoneyDocument.DocType.MONEY_RECEIPT, status=models.MoneyDocument.Status.POSTED,
            cash_register=cash, company=company, branch=branch, payment_category=cat_in, amount=Decimal("500.00"),
        )
        return owner, company, branch, wh, prod, cash

    def setUp(self):
        self.oa, self.a, self.ba, self.wa, self.pa, self.ca = self.make_company("A")
        self.ob, self.b, self.bb, self.wb, self.pb, self.cb = self.make_company("B")
        self.cashier = User.objects.create(email="cashier@t.kg", password="x", role="salesperson", company=self.a)
        lo, hi = models.canonical_company_pair_ids(self.a.id, self.b.id)
        self.p = models.CompanyStockPartnership.objects.create(company_a_id=lo, company_b_id=hi,
                                                                activated_at=timezone.now())
        self.api_a, self.api_b = _client(self.oa), _client(self.ob)

    def transfer(self, api, wh_from, wh_to, product, qty="3"):
        return api.post(W + "stock-partnerships/transfer/", {
            "warehouse_from": str(wh_from.id), "warehouse_to": str(wh_to.id),
            "items": [{"product": str(product.id), "qty": qty, "price": "999"}],
        }, format="json")

    def bal(self, wh, product):
        return models.StockBalance.objects.get(warehouse=wh, product=product).qty


class PermissionTests(PartnershipBase):
    def test_t1_employee_forbidden(self):
        c = _client(self.cashier)
        for method, url, body in [
            ("get", "stock-partnerships/active/", None),
            ("post", "stock-partnerships/transfer/", {}),
            ("post", "stock-partnerships/cash-incassations/", {}),
            ("get", f"stock-partnerships/companies/{self.b.id}/catalog/", None),
            ("get", f"stock-partnerships/companies/{self.b.id}/warehouses/", None),
            ("post", "stock-partnership-requests/", {"to_company": str(self.b.id)}),
            ("get", f"stock-partnerships/companies/{self.b.id}/sales/", None),
        ]:
            r = getattr(c, method)(W + url, body, format="json") if body is not None else getattr(c, method)(W + url)
            self.assertEqual(r.status_code, 403, (url, r.status_code))

    def test_t21_admin_cannot_delete(self):
        from django.contrib.admin.sites import site

        adm = site._registry[models.CompanyStockPartnership]
        self.assertFalse(adm.has_delete_permission(None, self.p))


class TransferTests(PartnershipBase):
    def test_t3_give_posts_immediately(self):
        r = self.transfer(self.api_a, self.wa, self.wb, self.pa)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["result"], "posted")
        doc = models.Document.objects.get(pk=r.data["id"])
        self.assertEqual((doc.created_by_id, doc.initiator_company_id), (self.oa.id, self.a.id))
        self.assertEqual(doc.items.get().price, Decimal("12.50"))  # закупочная, не 999
        self.assertEqual(self.bal(self.wa, self.pa), Decimal("7.000"))

    def test_t4_t6_pull_needs_approval(self):
        r = self.transfer(self.api_a, self.wb, self.wa, self.pb)
        self.assertEqual(r.status_code, 202, r.data)
        self.assertEqual(r.data["result"], "pending")
        op_id = r.data["operation"]["id"]
        self.assertEqual(self.bal(self.wb, self.pb), Decimal("10.000"))
        self.assertFalse(models.Document.objects.filter(doc_type="TRANSFER").exists())

        self.assertEqual(self.api_a.post(W + f"stock-partnerships/operations/{op_id}/approve/").status_code, 403)  # T8
        lst = self.api_b.get(W + "stock-partnerships/operations/").data
        self.assertEqual([o["id"] for o in lst["incoming"]], [op_id])
        self.assertEqual(lst["incoming"][0]["items"][0]["product_name"], self.pb.name)

        r = self.api_b.post(W + f"stock-partnerships/operations/{op_id}/approve/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "APPROVED")
        doc = models.Document.objects.get(pk=r.data["document"])
        self.assertEqual((doc.status, doc.initiator_company_id, doc.created_by_id), ("POSTED", self.a.id, self.oa.id))
        self.assertEqual(self.bal(self.wb, self.pb), Decimal("7.000"))
        r = self.api_b.post(W + f"stock-partnerships/operations/{op_id}/approve/")  # T9
        self.assertEqual((r.status_code, r.data["detail"]), (400, "Операция уже обработана."))

    def test_t5_direct_pull_when_partner_allows(self):
        r = self.api_b.patch(W + f"stock-partnerships/companies/{self.a.id}/settings/", {"allow_direct_pull": True},
                             format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["allow_direct_pull"])
        self.assertTrue(models.CompanyStockPartnershipEvent.objects.filter(kind="SETTINGS_CHANGED").exists())
        r = self.transfer(self.api_a, self.wb, self.wa, self.pb)
        self.assertEqual((r.status_code, r.data["result"]), (201, "posted"))

    def test_t7_approve_without_stock_stays_pending(self):
        op_id = self.transfer(self.api_a, self.wb, self.wa, self.pb, qty="8").data["operation"]["id"]
        models.StockBalance.objects.filter(warehouse=self.wb, product=self.pb).update(qty=Decimal("2"))
        r = self.api_b.post(W + f"stock-partnerships/operations/{op_id}/approve/")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(models.PartnerOperationRequest.objects.get(pk=op_id).status, "PENDING")
        self.assertFalse(models.Document.objects.filter(doc_type="TRANSFER").exists())

    def test_reject_and_cancel(self):
        op1 = self.transfer(self.api_a, self.wb, self.wa, self.pb).data["operation"]["id"]
        r = self.api_b.post(W + f"stock-partnerships/operations/{op1}/reject/", {"reason": "нет"}, format="json")
        self.assertEqual((r.data["status"], r.data["reject_reason"]), ("REJECTED", "нет"))
        op2 = self.transfer(self.api_a, self.wb, self.wa, self.pb).data["operation"]["id"]
        self.assertEqual(self.api_b.post(W + f"stock-partnerships/operations/{op2}/cancel/").status_code, 403)
        self.assertEqual(self.api_a.post(W + f"stock-partnerships/operations/{op2}/cancel/").data["status"], "CANCELLED")

    def test_t12_failed_posting_leaves_no_document(self):
        before = models.Document.objects.count()
        r = self.transfer(self.api_a, self.wa, self.wb, self.pa, qty="50")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(models.Document.objects.count(), before)

    def test_pull_request_checks_stock_upfront(self):
        r = self.transfer(self.api_a, self.wb, self.wa, self.pb, qty="50")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertFalse(models.PartnerOperationRequest.objects.exists())

    def test_t15_unpost_only_receiver(self):
        doc_id = self.transfer(self.api_a, self.wa, self.wb, self.pa).data["id"]
        r = self.api_a.post(W + f"documents/{doc_id}/unpost/")
        self.assertEqual(r.status_code, 403, getattr(r, "data", None))
        r = self.api_b.post(W + f"documents/{doc_id}/unpost/")
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))

    def test_t20_same_name_not_merged(self):
        other = models.WarehouseProduct.objects.create(
            company=self.b, branch=self.bb, warehouse=self.wb, name=self.pa.name, code="OTHER", unit="шт",
            quantity=Decimal("0"), purchase_price=Decimal("1"), price=Decimal("2"),
        )
        self.pa.barcode = "1234567890123"
        self.pa.code = "UNIQUE-A"
        self.pa.save()
        self.transfer(self.api_a, self.wa, self.wb, self.pa)
        dest = models.WarehouseProduct.objects.filter(company=self.b, name=self.pa.name).exclude(pk=other.pk)
        self.assertEqual(dest.count(), 1)
        self.assertFalse(models.StockBalance.objects.filter(product=other).exists())


class IncassationTests(PartnershipBase):
    def test_t10_pull_cash_needs_approval(self):
        r = self.api_a.post(W + "stock-partnerships/cash-incassations/", {
            "cash_register_from": str(self.cb.id), "cash_register_to": str(self.ca.id), "amount": "200.00",
        }, format="json")
        self.assertEqual((r.status_code, r.data["result"]), (202, "pending"))
        self.assertEqual(services_money.cash_register_balance(self.cb), Decimal("500.00"))
        op_id = r.data["operation"]["id"]
        r = self.api_b.post(W + f"stock-partnerships/operations/{op_id}/approve/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIsNotNone(r.data["incassation"])
        self.assertEqual(services_money.cash_register_balance(self.cb), Decimal("300.00"))
        inc = models.CompanyCashIncassation.objects.get()
        self.assertEqual(str(inc.partner_operation_id), op_id)

    def test_give_cash_posts_and_overdraft_400(self):
        r = self.api_a.post(W + "stock-partnerships/cash-incassations/", {
            "cash_register_from": str(self.ca.id), "cash_register_to": str(self.cb.id), "amount": "200.00",
        }, format="json")
        self.assertEqual((r.status_code, r.data["result"]), (201, "posted"))
        r = self.api_a.post(W + "stock-partnerships/cash-incassations/", {
            "cash_register_from": str(self.ca.id), "cash_register_to": str(self.cb.id), "amount": "400.00",
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Недостаточно средств", r.data["detail"])


class LifecycleTests(PartnershipBase):
    def test_t13_t14_terminate_and_reactivate(self):
        op_id = self.transfer(self.api_a, self.wb, self.wa, self.pb).data["operation"]["id"]
        self.api_b.patch(W + f"stock-partnerships/companies/{self.a.id}/settings/", {"allow_direct_pull": True},
                         format="json")
        r = self.api_a.post(W + f"stock-partnerships/companies/{self.b.id}/terminate/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["status"], r.data["cancelled_operations"]), ("TERMINATED", 1))
        self.assertEqual(models.PartnerOperationRequest.objects.get(pk=op_id).status, "CANCELLED")
        self.assertEqual(self.transfer(self.api_a, self.wa, self.wb, self.pa).status_code, 400)
        self.assertEqual(self.api_a.get(W + "stock-partnerships/active/").data["partners"], [])
        self.assertEqual(self.api_a.post(W + f"stock-partnerships/companies/{self.b.id}/terminate/").status_code, 404)

        req = self.api_a.post(W + "stock-partnership-requests/", {"to_company": str(self.b.id)}, format="json")
        self.assertEqual(req.status_code, 201, req.data)
        r = self.api_b.post(W + f"stock-partnership-requests/{req.data['id']}/accept/")
        self.assertEqual(r.status_code, 200, r.data)
        self.p.refresh_from_db()
        self.assertEqual(models.CompanyStockPartnership.objects.count(), 1)
        self.assertEqual(self.p.status, "ACTIVE")
        self.assertFalse(self.p.allows_direct_pull_from(self.b.id))  # флаги сброшены
        kinds = list(models.CompanyStockPartnershipEvent.objects.order_by("created_at").values_list("kind", flat=True))
        self.assertEqual(kinds, ["SETTINGS_CHANGED", "TERMINATED", "ACTIVATED"])

    def test_t16_counter_request(self):
        self.api_a.post(W + f"stock-partnerships/companies/{self.b.id}/terminate/")
        self.api_a.post(W + "stock-partnership-requests/", {"to_company": str(self.b.id)}, format="json")
        r = self.api_b.post(W + "stock-partnership-requests/", {"to_company": str(self.a.id)}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(str(r.data["code"]), "incoming_request_exists")

    def test_active_payload(self):
        row = self.api_a.get(W + "stock-partnerships/active/").data["partners"][0]
        self.assertEqual(row["id"], str(self.b.id))
        for k in ("partnership_id", "since", "allow_direct_pull", "partner_allows_direct_pull",
                  "share_sales_history", "partner_shares_sales_history", "pending_operations_in"):
            self.assertIn(k, row)
        r = self.api_a.patch(W + f"stock-partnerships/companies/{self.b.id}/settings/", {"x": 1}, format="json")
        self.assertEqual(r.status_code, 400)


class CatalogTests(PartnershipBase):
    def test_t17_search(self):
        self.assertEqual(self.api_a.get(W + "stock-partnerships/companies/search/?search=ab").status_code, 400)
        Company.objects.create(name="Компания C", owner=User.objects.create(email="c@t.kg", password="x"))
        r = self.api_a.get(W + "stock-partnerships/companies/search/?search=Компания")
        self.assertEqual(r.status_code, 200)
        names = {x["name"]: x["partnership_status"] for x in r.data}
        self.assertNotIn(self.a.name, names)
        self.assertEqual((names[self.b.name], names["Компания C"]), ("ACTIVE", None))
        self.assertEqual(self.api_a.get(W + "agents/companies/search/?search=ко").status_code, 400)

    def test_t18_warehouses_hide_balance(self):
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/warehouses/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["warehouses"][0]["products_count"], 1)
        self.assertIsNone(r.data["cash_registers"][0]["balance"])
        self.api_b.patch(W + f"stock-partnerships/companies/{self.a.id}/settings/", {"allow_direct_pull": True},
                         format="json")
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/warehouses/")
        self.assertEqual(r.data["cash_registers"][0]["balance"], "500.00")

    def test_t19_products_paginated_and_foreign_404(self):
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/warehouses/{self.wb.id}/products/")
        self.assertEqual((r.status_code, r.data["count"], r.data["results"][0]["qty"]), (200, 1, "10.000"))
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/warehouses/{self.wa.id}/products/")
        self.assertEqual(r.status_code, 404)

    def test_cash_registers_list_hides_partner_totals(self):
        r = self.api_a.get(W + "cash-registers/?include_partners=1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["receipts_total"], "500.00")  # только своя касса
        r = self.api_a.get(W + f"cash-registers/{self.cb.id}/operations/?include_partners=1")
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        self.assertIsNone(r.data["balance"])


class SalesHistoryTests(PartnershipBase):
    def make_sale(self, number, total, status="POSTED", doc_type="SALE", wh=None, client_name="ИП Асан"):
        cp = models.Counterparty.objects.create(name=client_name, phone="+996700111222", inn="123",
                                                type=models.Counterparty.Type.CLIENT, company=self.b)
        doc = models.Document.objects.create(
            doc_type=doc_type, status=status, number=number, warehouse_from=wh or self.wb, counterparty=cp,
            total=Decimal(total), comment="секрет", date=timezone.now(),
        )
        models.DocumentItem.objects.create(document=doc, product=self.pb, qty=Decimal("2"), price=Decimal(total) / 2)
        models.Document.objects.filter(pk=doc.pk).update(status=status, total=Decimal(total))
        return doc

    def test_t22_t25_t30_list(self):
        s1 = self.make_sale("S-101", "1000")
        self.make_sale("S-102", "500", status="CASH_PENDING", client_name="ОсОО Бета")
        self.make_sale("S-103", "700", status="DRAFT")
        self.make_sale("SR-1", "300", doc_type="SALE_RETURN")
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["summary"]["count"], 2)
        self.assertEqual(r.data["summary"]["amount"], "1500.00")
        self.assertEqual({x["number"] for x in r.data["results"]}, {"S-101", "S-102"})
        an = self.api_a.get(W + f"owner/partners/{self.b.id}/analytics/").data
        self.assertEqual(r.data["summary"]["amount"], an["summary"]["gross_sales_amount"])  # T25
        self.assertEqual(an["partner_branches"][0]["name"], self.bb.name)
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/?search=S-10")
        self.assertEqual(r.data["count"], 2)
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/?search=Бета")
        self.assertEqual([x["number"] for x in r.data["results"]], ["S-102"])
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/?doc_type=SALE_RETURN")
        self.assertEqual(r.data["summary"]["amount"], "300.00")
        d = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/{s1.id}/")
        self.assertEqual(d.status_code, 200, d.data)
        self.assertEqual(d.data["items"][0]["net_amount"], "1000.00")
        for key in ("comment", "counterparty", "prepayment_amount", "cash_register", "payment_category"):  # T28
            self.assertNotIn(key, d.data)
        self.assertNotIn("purchase_price", d.data["items"][0])
        for value in ("секрет", "+996700111222", "12.50"):
            self.assertNotIn(value, str(d.data))

    def test_t23_hidden_and_t27_404(self):
        draft = self.make_sale("S-1", "100", status="DRAFT")
        own = self.make_sale("S-2", "100", wh=self.wa)
        for doc in (draft, own):
            self.assertEqual(self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/{doc.id}/").status_code, 404)
        self.api_b.patch(W + f"stock-partnerships/companies/{self.a.id}/settings/", {"share_sales_history": False},
                         format="json")
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/")
        self.assertEqual((r.status_code, str(r.data["code"])), (403, "sales_history_hidden"))
        self.assertFalse(self.api_a.get(W + "stock-partnerships/active/").data["partners"][0]["partner_shares_sales_history"])

    def test_t29_period_limit(self):
        r = self.api_a.get(W + f"stock-partnerships/companies/{self.b.id}/sales/?period=custom&date_from=2025-01-01&date_to=2026-06-01")
        self.assertEqual(r.status_code, 400)
