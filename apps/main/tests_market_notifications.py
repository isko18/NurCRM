"""
Контрактные тесты market-уведомлений (realtime-notifications-backend.md, чеклист P0/P1):
каждое событие → запись Notification у получателя + WS-payload с id/title/is_read,
идемпотентность по (company, user, type, meta.source_id), unread_count в REST.
"""
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.main import notifications_market as nm
from apps.main.models import Debt, DebtPayment, Notification
from apps.main.realtime import notification_payload
from apps.users.models import Branch, Company, SubscriptionPlan

User = get_user_model()


class MarketNotificationsTests(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex[:6]
        self.owner = User.objects.create_user(
            email=f"owner_mn_{suffix}@test.com", password="pass", role="owner"
        )
        plan = SubscriptionPlan.objects.create(name="Pro", price=Decimal("100.00"))
        self.company = Company.objects.create(
            name="Market Notif Co", owner=self.owner, subscription_plan=plan
        )
        self.owner.company = self.company
        self.owner.save()

        self.branch = Branch.objects.create(name="Main", company=self.company)
        self.cashier = User.objects.create_user(
            email=f"cashier_mn_{suffix}@test.com", password="pass",
            role="salesperson", company=self.company,
        )

    def _commit(self):
        """TestCase держит тест в транзакции — on_commit-колбэки надо выполнить явно."""
        return self.captureOnCommitCallbacks(execute=True)

    # ── получатели ──
    def test_owner_like_users_excludes_actor(self):
        self.assertEqual([u.id for u in nm.owner_like_users(self.company)], [self.owner.id])
        self.assertEqual(nm.owner_like_users(self.company, exclude=[self.owner]), [])

    # ── публикация и идемпотентность ──
    def test_publish_event_creates_notification_with_meta(self):
        with self._commit():
            nm.publish_event(
                company=self.company,
                branch=self.branch,
                recipients=[self.owner],
                event_type=nm.SALE_CREATED,
                title="Новая продажа",
                message="Касса «Основная»: 1 250,00 сом",
                url="/crm/market/cashier",
                cta_label="Открыть кассу",
                source_kind="pos_sale",
                source_id="sale-1",
                meta={"amount": "1250.00"},
            )
        notif = Notification.objects.get(user=self.owner, type=nm.SALE_CREATED)
        self.assertFalse(notif.is_read)
        self.assertEqual(notif.data["source_id"], "sale-1")
        self.assertEqual(notif.data["source_kind"], "pos_sale")
        self.assertEqual(notif.data["company_id"], str(self.company.id))
        self.assertEqual(notif.data["cta_label"], "Открыть кассу")

        payload = notification_payload(notif)
        self.assertEqual(payload["id"], str(notif.id))
        self.assertEqual(payload["title"], "Новая продажа")
        self.assertEqual(payload["cta_label"], "Открыть кассу")
        self.assertEqual(payload["meta"], payload["data"])
        self.assertIs(payload["is_read"], False)

    def test_publish_event_is_idempotent_on_source_id(self):
        for _ in range(2):
            with self._commit():
                nm.publish_event(
                    company=self.company, recipients=[self.owner],
                    event_type=nm.CASHFLOW_PENDING, title="Операция ждёт подтверждения",
                    source_kind="pos_sale", source_id="cf-1",
                )
        self.assertEqual(
            Notification.objects.filter(user=self.owner, type=nm.CASHFLOW_PENDING).count(), 1
        )

    def test_publish_event_without_recipients_is_noop(self):
        with self._commit():
            nm.publish_event(company=self.company, recipients=[], event_type=nm.SALE_CREATED, title="x")
        self.assertEqual(Notification.objects.count(), 0)

    # ── долги (P1) ──
    def test_debt_created_and_paid_notify_owner(self):
        with self._commit():
            debt = Debt.objects.create(
                company=self.company, branch=self.branch, name="Иван", phone="+996700000000",
                amount=Decimal("1000.00"),
            )
        created = Notification.objects.get(user=self.owner, type=nm.DEBT_CREATED)
        self.assertEqual(created.data["debt_id"], str(debt.id))

        with self._commit():
            DebtPayment.objects.create(
                company=self.company, branch=self.branch, debt=debt, amount=Decimal("400.00"),
            )
        paid = Notification.objects.get(user=self.owner, type=nm.DEBT_PAID)
        self.assertEqual(paid.level, "success")
        self.assertEqual(paid.data["balance"], "600.00")
        self.assertIn("остаток", paid.message)

    # ── REST: unread_count ──
    def test_rest_unread_count_and_mark_read(self):
        client = APIClient()
        client.force_authenticate(self.owner)

        with self._commit():
            nm.publish_event(
                company=self.company, recipients=[self.owner], event_type=nm.SALE_CREATED,
                title="Новая продажа", source_id="sale-2",
            )
        notif = Notification.objects.get(user=self.owner)

        resp = client.get(reverse("notification-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["unread_count"], 1)

        resp = client.post(reverse("notification-read", args=[notif.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["unread_count"], 0)
        self.assertTrue(Notification.objects.get(pk=notif.id).is_read)

        self.assertEqual(client.get(reverse("notification-list")).data["unread_count"], 0)

    def test_rest_mark_all_read_alias(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        for i in range(3):
            with self._commit():
                nm.publish_event(
                    company=self.company, recipients=[self.owner], event_type=nm.SALE_CREATED,
                    title="Новая продажа", source_id=f"sale-batch-{i}",
                )
        resp = client.post(reverse("notifications-read-all"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["unread_count"], 0)
        self.assertEqual(Notification.objects.filter(user=self.owner, is_read=False).count(), 0)

    # ── формат денег ──
    def test_fmt_money(self):
        self.assertEqual(nm.fmt_money(Decimal("1250")), "1 250,00")
        self.assertEqual(nm.fmt_money(0), "0,00")
        self.assertEqual(nm.fmt_money(Decimal("-99.5")), "-99,50")
