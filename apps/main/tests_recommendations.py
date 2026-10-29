"""Умная допродажа: приёмка раздела 2 ТЗ (часть 7)."""
import uuid
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.construction.models import Cashbox
from apps.main.models import (
    Product,
    RecommendationEvent,
    Sale,
    SaleItem,
    SaleReturn,
    SalesTarget,
)
from apps.main.recommendations import store_company_recommendation_pairs
from apps.main.recommendations_tasks import company_pairs_countdown, dispatch_recommendation_pairs
from apps.users.models import Company, SubscriptionPlan, User

EVENTS = "/api/main/recommendations/events/"
LINK = "/api/main/recommendations/events/link-sale/"
STATS = "/api/main/recommendations/stats/"
PAIRS = "/api/main/recommendations/pairs/"
TARGETS = "/api/main/sales-targets/"


class RecoBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email=f"o{uuid.uuid4().hex[:6]}@t.kg", password="x", role="owner")
        plan = SubscriptionPlan.objects.create(name="Стандарт", price=Decimal("1.00"))
        self.company = Company.objects.create(name="Reco Co", owner=self.owner, is_active=True, subscription_plan=plan)
        self.owner.company = self.company
        self.owner.save()
        self.cashier = User.objects.create_user(
            email=f"c{uuid.uuid4().hex[:6]}@t.kg", password="x", company=self.company, role="salesperson"
        )
        self.cashbox = Cashbox.objects.create(name="Касса", company=self.company,
                                              role=Cashbox.CashboxRole.POS_MAIN)
        self.bread = Product.objects.create(company=self.company, name="Хлеб", price=Decimal("40.00"),
                                            purchase_price=Decimal("25.00"), quantity=Decimal("100"))
        self.milk = Product.objects.create(company=self.company, name="Молоко", price=Decimal("80.00"),
                                           purchase_price=Decimal("60.00"), quantity=Decimal("100"))
        self.cigs = Product.objects.create(company=self.company, name="Сигареты", price=Decimal("200.00"),
                                           quantity=Decimal("100"), upsell_excluded=True)
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def event(self, kind="accepted", product=None, cart_id="cart-1", sale_id=None, **extra):
        d = {
            "client_event_id": str(uuid.uuid4()),
            "event": kind,
            "product_id": str((product or self.bread).id),
            "trigger_product_ids": [str(self.milk.id)],
            "cart_id": cart_id,
            "sale_id": str(sale_id) if sale_id else None,
            "price": "40.00",
            "score": 0.73,
            "cashier_id": str(self.cashier.id),
            "device_id": "pos-1",
            "occurred_at": timezone.now().isoformat(),
        }
        d.update(extra)
        return d

    def sale(self, products, status=Sale.Status.PAID):
        s = Sale.objects.create(company=self.company, user=self.cashier, status=status, cashbox=self.cashbox,
                                total=sum((p.price for p in products), Decimal("0")))
        for p in products:
            SaleItem.objects.create(company=self.company, sale=s, product=p, name_snapshot=p.name,
                                    unit_price=p.price, quantity=Decimal("1"),
                                    purchase_price_snapshot=p.purchase_price)
        return s

    def stats(self):
        r = self.api.get(STATS)
        self.assertEqual(r.status_code, 200, r.content)
        return r.data


class EventsTests(RecoBase):
    def test_1_batch_dedupe(self):
        batch = {"events": [self.event("shown"), self.event("accepted"), self.event("skipped")]}
        r = self.api.post(EVENTS, batch, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.data["accepted"], r.data["duplicates"]), (3, 0))
        r = self.api.post(EVENTS, batch, format="json")
        self.assertEqual((r.data["accepted"], r.data["duplicates"]), (0, 3))
        self.assertEqual(RecommendationEvent.objects.filter(company=self.company).count(), 3)

    def test_in_batch_duplicate_and_unknown_product(self):
        e = self.event()
        bad = self.event(product_id=str(uuid.uuid4()))
        r = self.api.post(EVENTS, {"events": [e, e, bad]}, format="json")
        self.assertEqual((r.data["accepted"], r.data["duplicates"], r.data["rejected"]), (1, 1, 1))

    def test_max_200(self):
        r = self.api.post(EVENTS, {"events": [self.event() for _ in range(201)]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_foreign_cashier_falls_back_to_request_user(self):
        r = self.api.post(EVENTS, {"events": [self.event(cashier_id=str(uuid.uuid4()))]}, format="json")
        self.assertEqual(r.data["accepted"], 1)
        self.assertEqual(RecommendationEvent.objects.get().cashier_id, self.owner.id)


class StatsTests(RecoBase):
    def test_2_accepted_paid_linked_revenue(self):
        self.api.post(EVENTS, {"events": [self.event("shown"), self.event("accepted")]}, format="json")
        s = self.sale([self.milk, self.bread])
        r = self.api.patch(LINK, {"cart_id": "cart-1", "sale_id": str(s.id)}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["linked_events"], 2)
        data = self.stats()
        self.assertEqual((data["shown"], data["accepted"]), (1, 1))
        self.assertEqual(data["revenue"], "40.00")
        self.assertEqual(data["profit"], "15.00")
        self.assertEqual(data["acceptance_rate"], 100.0)
        self.assertEqual(data["by_product"][0]["revenue"], "40.00")
        self.assertEqual(data["by_cashier"][0]["cashier_id"], str(self.cashier.id))
        self.assertEqual(data["by_day"][0]["revenue"], "40.00")

    def test_3_removed_from_cart_not_counted(self):
        self.api.post(EVENTS, {"events": [self.event("accepted")]}, format="json")
        s = self.sale([self.milk])  # хлеб удалён из чека перед оплатой
        self.api.patch(LINK, {"cart_id": "cart-1", "sale_id": str(s.id)}, format="json")
        self.assertEqual(self.stats()["revenue"], "0.00")

    def test_4_return_subtracted(self):
        self.api.post(EVENTS, {"events": [self.event("accepted")]}, format="json")
        s = self.sale([self.milk, self.bread])
        self.api.patch(LINK, {"cart_id": "cart-1", "sale_id": str(s.id)}, format="json")
        self.assertEqual(self.stats()["revenue"], "40.00")
        item = s.items.get(product=self.bread)
        SaleReturn.objects.create(
            sale=s, company=self.company, idempotency_key="r1", returned_amount=Decimal("40.00"), is_full=False,
            returned_items=[{"sale_item": str(item.id), "product": str(self.bread.id), "qty": "1.000",
                             "total": "40.00"}],
        )
        data = self.stats()
        self.assertEqual((data["revenue"], data["profit"]), ("0.00", "0.00"))

    def test_unpaid_sale_not_counted(self):
        self.api.post(EVENTS, {"events": [self.event("accepted")]}, format="json")
        s = self.sale([self.bread], status=Sale.Status.CANCELED)
        self.api.patch(LINK, {"cart_id": "cart-1", "sale_id": str(s.id)}, format="json")
        self.assertEqual(self.stats()["revenue"], "0.00")

    def test_link_sale_before_sale_synced_and_events_after_link(self):
        sale_id = uuid.uuid4()
        # link-sale пришёл раньше, чем офлайн-продажа и пачка событий
        r = self.api.patch(LINK, {"cart_id": "cart-1", "sale_id": str(sale_id)}, format="json")
        self.assertEqual(r.data["sale_found"], False)
        self.api.post(EVENTS, {"events": [self.event("accepted", sale_id=sale_id)]}, format="json")
        self.assertEqual(self.stats()["revenue"], "0.00")
        s = Sale.objects.create(id=sale_id, company=self.company, cashbox=self.cashbox, status=Sale.Status.PAID, total=Decimal("40"))
        SaleItem.objects.create(company=self.company, sale=s, product=self.bread, name_snapshot="Хлеб",
                                unit_price=Decimal("40.00"), quantity=Decimal("1"))
        self.assertEqual(self.stats()["revenue"], "40.00")

    def test_events_after_link_inherit_sale(self):
        self.api.post(EVENTS, {"events": [self.event("shown")]}, format="json")
        s = self.sale([self.bread])
        self.api.patch(LINK, {"cart_id": "cart-1", "sale_id": str(s.id)}, format="json")
        self.api.post(EVENTS, {"events": [self.event("accepted")]}, format="json")
        self.assertEqual(self.stats()["revenue"], "40.00")

    def test_permission(self):
        api = APIClient()
        api.force_authenticate(self.cashier)
        self.assertEqual(api.get(STATS).status_code, 403)
        self.cashier.can_view_analytics = True
        self.cashier.save()
        self.assertEqual(api.get(STATS).status_code, 200)

    def test_bad_params(self):
        self.assertEqual(self.api.get(STATS + "?branch=abc").status_code, 400)
        self.assertEqual(self.api.get(STATS + "?date_from=2026-13-01").status_code, 400)


class PairsTests(RecoBase):
    def setUp(self):
        super().setUp()
        for _ in range(3):
            self.sale([self.milk, self.bread, self.cigs])
        self.sale([self.milk])

    def test_5_pairs_and_excluded(self):
        store_company_recommendation_pairs(self.company.id)
        r = self.api.get(PAIRS + "?days=90&limit=10")
        self.assertEqual(r.status_code, 200, r.content)
        by_a = {p["product_id"]: p["together"] for p in r.data["pairs"]}
        milk = by_a[str(self.milk.id)]
        self.assertEqual([t["product_id"] for t in milk], [str(self.bread.id)])
        self.assertEqual(milk[0]["count"], 3)
        self.assertEqual(milk[0]["confidence"], 0.75)
        self.assertEqual(milk[0]["lift"], 1.0)
        for together in by_a.values():
            self.assertNotIn(str(self.cigs.id), [t["product_id"] for t in together])

    def test_6_etag_304_and_stable_across_recompute(self):
        store_company_recommendation_pairs(self.company.id)
        r1 = self.api.get(PAIRS)
        etag = r1["ETag"]
        r2 = self.api.get(PAIRS, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r2.status_code, 304)
        store_company_recommendation_pairs(self.company.id)  # следующая ночь, данные те же
        self.assertEqual(self.api.get(PAIRS, HTTP_IF_NONE_MATCH=etag).status_code, 304)
        self.assertEqual(self.api.get(PAIRS, HTTP_IF_NONE_MATCH=f"W/{etag}").status_code, 304)
        self.sale([self.milk, self.bread])
        store_company_recommendation_pairs(self.company.id)
        self.assertEqual(self.api.get(PAIRS, HTTP_IF_NONE_MATCH=etag).status_code, 200)

    def test_no_cache_does_not_compute_inline(self):
        with mock.patch("apps.main.recommendations.compute_company_recommendation_pairs") as comp, \
                mock.patch("apps.main.recommendations_tasks.compute_company_recommendation_pairs_task.apply_async"):
            r = self.api.get(PAIRS)
        comp.assert_not_called()
        self.assertEqual(r.data, {"computed_at": None, "pairs": []})

    def test_dispatch_spread(self):
        with mock.patch(
            "apps.main.recommendations_tasks.compute_company_recommendation_pairs_task.apply_async"
        ) as aa:
            dispatch_recommendation_pairs()
        aa.assert_called_once()
        cd = aa.call_args.kwargs["countdown"]
        self.assertTrue(0 <= cd < 21600)
        self.assertEqual(cd, company_pairs_countdown(self.company.id))


class SalesTargetTests(RecoBase):
    def test_get_put_owner_only(self):
        r = self.api.put(TARGETS, {"month": "2026-10", "revenue_target": "1500000.00"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["revenue_target"], "1500000.00")
        r = self.api.put(TARGETS, {"month": "2026-10", "revenue_target": "1000.00"}, format="json")
        self.assertEqual(SalesTarget.objects.filter(company=self.company).count(), 1)
        r = self.api.get(TARGETS + "?month=2026-10")
        self.assertEqual((r.data["month"], r.data["revenue_target"]), ("2026-10", "1000.00"))

        api = APIClient()
        api.force_authenticate(self.cashier)
        self.assertEqual(api.put(TARGETS, {"month": "2026-10", "revenue_target": "1"}, format="json").status_code, 403)
        self.assertEqual(api.get(TARGETS + "?month=2026-10").status_code, 200)

    def test_validation(self):
        self.assertEqual(self.api.get(TARGETS + "?month=abc").status_code, 400)
        self.assertEqual(self.api.put(TARGETS, {"month": "2026-13", "revenue_target": "1"}, format="json").status_code, 400)
        self.assertEqual(self.api.put(TARGETS, {"month": "2026-10", "revenue_target": "x"}, format="json").status_code, 400)
