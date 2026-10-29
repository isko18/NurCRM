from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.construction.models import Cashbox
from apps.ekassa.sale_bridge import _sale_item_to_good, enrich_ekassa_fiscal_with_item_ids
from apps.main.document import SaleReceiptAPIView
from apps.main.models import Product, Sale, SaleItem
from apps.main.pos_views import SaleReceiptDataAPIView
from apps.users.models import Company, SubscriptionPlan

User = get_user_model()


class ReceiptItemMatchingAPITests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        email_owner = f"owner_rc_{uuid.uuid4().hex[:6]}@test.com"
        self.owner = User.objects.create_user(email=email_owner, password="pass", role="owner")
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(name="Receipt Test Co", owner=self.owner, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()

        self.cashbox = Cashbox.objects.create(name="Receipt Cashbox", company=self.company)

        self.prod_cola = Product.objects.create(
            name="Кола",
            company=self.company,
            price=Decimal("50.00"),
            barcode="460000000001",
        )

        self.sale = Sale.objects.create(
            company=self.company,
            user=self.owner,
            cashbox=self.cashbox,
            status=Sale.Status.PAID,
            payment_method=Sale.PaymentMethod.CASH,
            subtotal=Decimal("100.00"),
            discount_total=Decimal("25.00"),
            total=Decimal("75.00"),
            cash_received=Decimal("100.00"),
        )

        # Line 1: Cola with 5.00 discount (line_total = 45.00)
        self.item1 = SaleItem.objects.create(
            sale=self.sale,
            company=self.company,
            product=self.prod_cola,
            name_snapshot="Кола",
            quantity=Decimal("1.00"),
            unit_price=Decimal("50.00"),
            line_discount=Decimal("5.00"),
        )

        # Line 2: Cola with 20.00 discount (line_total = 30.00)
        self.item2 = SaleItem.objects.create(
            sale=self.sale,
            company=self.company,
            product=self.prod_cola,
            name_snapshot="Кола",
            quantity=Decimal("1.00"),
            unit_price=Decimal("50.00"),
            line_discount=Decimal("20.00"),
        )

        # Set fake ekassa_fiscal with tag 1059
        self.sale.ekassa_fiscal = {
            "status": "ok",
            "fd_number": 12345,
            "fields": {
                "1040": 12345,
                "1059": [
                    {"1030": "Кола", "1023": 1.0, "1076": 5000, "1043": 4500},
                    {"1030": "Кола", "1023": 1.0, "1076": 5000, "1043": 3000},
                ],
            },
        }
        self.sale.save()

    def test_sale_item_to_good_includes_item_id(self):
        good = _sale_item_to_good(self.item1)
        self.assertEqual(good["item_id"], str(self.item1.id))
        self.assertEqual(good["name"], "Кола")
        self.assertEqual(good["price"], 4500)

    def test_enrich_ekassa_fiscal_with_item_ids(self):
        enriched = enrich_ekassa_fiscal_with_item_ids(self.sale.ekassa_fiscal, self.sale)
        tag_1059 = enriched["fields"]["1059"]
        self.assertEqual(len(tag_1059), 2)
        db_items = list(self.sale.items.all().order_by("id"))
        self.assertEqual(tag_1059[0]["item_id"], str(db_items[0].id))
        self.assertEqual(tag_1059[0]["line_id"], str(db_items[0].id))
        self.assertEqual(tag_1059[1]["item_id"], str(db_items[1].id))
        self.assertEqual(tag_1059[1]["line_id"], str(db_items[1].id))

    def test_sale_receipt_json_endpoint_has_matching_item_ids(self):
        req = self.factory.get(f"/api/main/sales/json/{self.sale.id}/receipt/")
        force_authenticate(req, user=self.owner)
        view = SaleReceiptAPIView.as_view()
        resp = view(req, pk=self.sale.id)
        self.assertEqual(resp.status_code, 200)

        data = resp.data
        items = data["items"]
        self.assertEqual(len(items), 2)

        item_by_id = {it["item_id"]: it for it in items}
        self.assertIn(str(self.item1.id), item_by_id)
        self.assertIn(str(self.item2.id), item_by_id)

        it1 = item_by_id[str(self.item1.id)]
        self.assertEqual(it1["id"], str(self.item1.id))
        self.assertEqual(it1["line_id"], str(self.item1.id))
        self.assertEqual(Decimal(str(it1["line_discount"])), Decimal("5.00"))

        it2 = item_by_id[str(self.item2.id)]
        self.assertEqual(it2["id"], str(self.item2.id))
        self.assertEqual(it2["line_id"], str(self.item2.id))
        self.assertEqual(Decimal(str(it2["line_discount"])), Decimal("20.00"))

        # eKassa tag 1059 matching
        ekassa_1059 = data["ekassa"]["fields"]["1059"]
        ekassa_ids = [pos["item_id"] for pos in ekassa_1059]
        self.assertIn(str(self.item1.id), ekassa_ids)
        self.assertIn(str(self.item2.id), ekassa_ids)

    def test_pos_sale_receipt_endpoint_has_matching_item_ids(self):
        req = self.factory.get(f"/api/main/pos/sales/{self.sale.id}/receipt/")
        force_authenticate(req, user=self.owner)
        view = SaleReceiptDataAPIView.as_view()
        resp = view(req, pk=self.sale.id)
        self.assertEqual(resp.status_code, 200)

        data = resp.data
        items = data["items"]
        self.assertEqual(len(items), 2)

        items_ids = [it["item_id"] for it in items]
        self.assertIn(str(self.item1.id), items_ids)
        self.assertIn(str(self.item2.id), items_ids)

        ekassa_1059 = data["ekassa"]["fields"]["1059"]
        ekassa_ids = [pos["item_id"] for pos in ekassa_1059]
        self.assertIn(str(self.item1.id), ekassa_ids)
        self.assertIn(str(self.item2.id), ekassa_ids)
