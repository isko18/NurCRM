import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.models import Company
from apps.main.models import (
    Client,
    Product,
    Sale,
    SaleItem,
    SalePayment,
    ShowcaseOrder,
)
from apps.construction.models import CashShift, Cashbox
from apps.main.telegram_bot.models import (
    TelegramBotSettings,
    TelegramInquiry,
    TelegramCustomerProfile,
    TelegramMessageLog,
    TelegramProcessedUpdate,
)
from apps.main.telegram_bot.services import (
    telegram_api,
    owner_handler,
    customer_handler,
    events_handler,
)
from apps.main.telegram_bot.tasks import process_telegram_update

User = get_user_model()


class TelegramBotTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="owner@nurmarket.bot.test",
            password="password123",
            role="owner",
            is_staff=True,
        )
        self.company = Company.objects.create(
            name="Nur Market Bot Test",
            slug="nur-market-bot-test",
            owner=self.owner,
        )
        self.owner.company = self.company
        self.owner.owned_company = self.company
        self.owner.save()

        self.client.force_authenticate(user=self.owner)

        # Создаем кассу для продаж
        self.cashbox = Cashbox.objects.create(
            company=self.company,
            name="Основная касса",
        )

        # Создаем базовые товары для тестов
        self.p1 = Product.objects.create(
            company=self.company,
            name="Кола 1л",
            price=Decimal("80.00"),
            quantity=Decimal("15.00"),
            minimum_quantity=Decimal("5.00"),
            barcode="111222333",
        )
        self.p2 = Product.objects.create(
            company=self.company,
            name="Влажные салфетки",
            price=Decimal("88.00"),
            quantity=Decimal("2.00"),  # мало
            minimum_quantity=Decimal("10.00"),
        )

    def test_settings_get_default(self):
        """GET /api/main/telegram-bot/settings/ создает и возвращает дефолтные настройки."""
        url = "/api/main/telegram-bot/settings/"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(data["mode"], "server")
        self.assertFalse(data["token_set"])
        self.assertFalse(data["ai_key_set"])
        self.assertNotIn("token", data)
        self.assertNotIn("ai_key", data)

    @patch("apps.main.telegram_bot.services.telegram_api.get_me")
    @patch("apps.main.telegram_bot.services.telegram_api.set_webhook")
    def test_settings_patch_token_success(self, mock_set_wh, mock_get_me):
        """PATCH /api/main/telegram-bot/settings/ проверяет токен через getMe и ставит webhook."""
        mock_get_me.return_value = {"id": 12345, "username": "test_store_bot", "first_name": "Test Bot"}
        mock_set_wh.return_value = {"ok": True}

        url = "/api/main/telegram-bot/settings/"
        payload = {
            "token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            "ai_key": "AIzaSyTestKey123456",
            "owner_phone": "+996700123456",
            "shift_summary_enabled": True,
        }
        resp = self.client.patch(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data

        self.assertTrue(data["token_set"])
        self.assertTrue(data["ai_key_set"])
        self.assertEqual(data["bot_username"], "test_store_bot")
        self.assertTrue(data["webhook_ok"])
        self.assertIsNone(data["webhook_error"])
        self.assertNotIn("token", data)
        self.assertNotIn("ai_key", data)

        # Проверяем в БД, что токен зашифрован
        settings = TelegramBotSettings.objects.get(company=self.company)
        self.assertNotEqual(settings.encrypted_token, "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        self.assertEqual(settings.token, "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        self.assertEqual(settings.ai_key, "AIzaSyTestKey123456")

    @patch("apps.main.telegram_bot.services.telegram_api.get_me")
    def test_settings_patch_invalid_token_returns_400(self, mock_get_me):
        """При невалидном токене возвращается 400 {"token": ["Telegram: ..."]}."""
        mock_get_me.side_effect = telegram_api.TelegramAPIError("Telegram: Unauthorized", status_code=401)

        url = "/api/main/telegram-bot/settings/"
        resp = self.client.patch(url, {"token": "bad_token"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("token", resp.data)

    def test_detect_owner_chat(self):
        """POST /api/main/telegram-bot/detect-owner-chat/ сохраняет последний чат с /start."""
        settings = TelegramBotSettings.objects.create(
            company=self.company,
            mode=TelegramBotSettings.Mode.SERVER,
        )
        TelegramMessageLog.objects.create(
            bot_settings=settings,
            chat_id="99887766",
            chat_title="My Store Group",
            sender_name="Azamat",
            text="/start",
        )

        url = "/api/main/telegram-bot/detect-owner-chat/"
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["owner_chat_id"], "99887766")

        settings.refresh_from_db()
        self.assertEqual(settings.owner_chat_id, "99887766")

    @patch("apps.main.telegram_bot.services.telegram_api.send_message")
    def test_test_message_endpoint(self, mock_send):
        """POST /api/main/telegram-bot/test-message/ отправляет проверочное сообщение."""
        mock_send.return_value = {"ok": True}
        settings = TelegramBotSettings.objects.create(
            company=self.company,
            token="valid_token_123",
            owner_chat_id="123456",
        )

        url = "/api/main/telegram-bot/test-message/"
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["ok"])
        mock_send.assert_called_once()

    def test_stats_and_inquiries_endpoints(self):
        """GET stats/ и inquiries/ возвращают агрегированные метрики и историю."""
        order = ShowcaseOrder.objects.create(
            company=self.company,
            number=1,
            customer_name="Айбек",
            customer_phone="+996555112233",
            total=Decimal("176.00"),
            source="telegram",
        )
        TelegramInquiry.objects.create(
            company=self.company,
            chat_id="555",
            name="Айбек",
            username="aibek",
            text="Влажные салфетки есть?",
            reply="Да, 88 сом, есть в наличии.",
            is_voice=False,
            order=order,
        )

        # 1. Проверяем inquiries
        url_inq = "/api/main/telegram-bot/inquiries/"
        resp_inq = self.client.get(url_inq)
        self.assertEqual(resp_inq.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_inq.data["count"], 1)
        res0 = resp_inq.data["results"][0]
        self.assertEqual(res0["chat_id"], "555")
        self.assertEqual(res0["text"], "Влажные салфетки есть?")
        self.assertIsNotNone(res0["order"])
        self.assertEqual(res0["order"]["number"], 1)

        # 2. Проверяем stats
        url_stats = "/api/main/telegram-bot/stats/"
        resp_stats = self.client.get(url_stats)
        self.assertEqual(resp_stats.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_stats.data["messages"], 1)
        self.assertEqual(resp_stats.data["people"], 1)
        self.assertEqual(resp_stats.data["orders"], 1)
        self.assertEqual(resp_stats.data["orders_total"], "176.00")
        self.assertEqual(len(resp_stats.data["by_day"]), 1)

    def test_public_webhook_verification_and_deduplication(self):
        """Вебхук проверяет secret_token и не обрабатывает дублирующий update_id."""
        settings = TelegramBotSettings.objects.create(
            company=self.company,
            secret_token="my_secret_token_123",
            mode=TelegramBotSettings.Mode.SERVER,
            token="dummy_token",
        )
        url = f"/api/telegram/webhook/{settings.bot_uuid}/"

        # 1. Неверный secret_token -> 403
        resp_bad = self.client.post(
            url,
            {"update_id": 1001, "message": {"text": "hello"}},
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong_token",
        )
        self.assertEqual(resp_bad.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Верный secret_token -> 200 OK
        with patch("apps.main.telegram_bot.tasks.process_telegram_update.delay") as mock_task:
            resp_ok = self.client.post(
                url,
                {"update_id": 1001, "message": {"text": "hello"}},
                format="json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="my_secret_token_123",
            )
            self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)
            mock_task.assert_called_once()

        # 3. Повторная доставка того же update_id -> 200 duplicate, задача не ставится повторно
        with patch("apps.main.telegram_bot.tasks.process_telegram_update.delay") as mock_task2:
            resp_dup = self.client.post(
                url,
                {"update_id": 1001, "message": {"text": "hello"}},
                format="json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="my_secret_token_123",
            )
            self.assertEqual(resp_dup.status_code, status.HTTP_200_OK)
            self.assertEqual(resp_dup.data.get("status"), "duplicate")
            mock_task2.assert_not_called()

    @patch("apps.main.telegram_bot.services.telegram_api.send_message")
    def test_owner_commands_and_keywords(self, mock_send):
        """Владелец получает отчёты по командам /segodnya, /top, /ostatki и ключевым словам."""
        settings = TelegramBotSettings.objects.create(
            company=self.company,
            token="token123",
            owner_chat_id="999",
            mode=TelegramBotSettings.Mode.SERVER,
        )

        # Создаем тестовую продажу за сегодня
        sale = Sale.objects.create(
            company=self.company,
            cashbox=self.cashbox,
            doc_number=101,
            status=Sale.Status.PAID,
            total=Decimal("500.00"),
            cash_amount=Decimal("300.00"),
            card_amount=Decimal("200.00"),
            paid_at=timezone.now(),
        )
        SaleItem.objects.create(
            sale=sale,
            company=self.company,
            product=self.p1,
            name_snapshot=self.p1.name,
            quantity=Decimal("5.00"),
            unit_price=Decimal("80.00"),
        )

        # 1. Команда /segodnya
        owner_handler.handle_owner_message(settings, "999", "/segodnya")
        self.assertTrue(mock_send.called)
        sent_text = mock_send.call_args[0][2]
        self.assertIn("500.00", sent_text)
        self.assertIn("Выручка за сегодня", sent_text)

        # 2. Ключевые слова "сколько заработали сегодня"
        mock_send.reset_mock()
        owner_handler.handle_owner_message(settings, "999", "сколько заработали сегодня")
        self.assertTrue(mock_send.called)
        self.assertIn("500.00", mock_send.call_args[0][2])

        # 3. Ключевые слова "что заканчивается"
        mock_send.reset_mock()
        owner_handler.handle_owner_message(settings, "999", "что заканчивается")
        self.assertTrue(mock_send.called)
        self.assertIn("Влажные салфетки", mock_send.call_args[0][2])

    @patch("apps.main.telegram_bot.services.telegram_api.send_message")
    def test_customer_ai_consultant_and_order_creation(self, mock_send):
        """Покупатель консультируется, фин. инфо скрыта, подтверждение заказа создает ShowcaseOrder."""
        settings = TelegramBotSettings.objects.create(
            company=self.company,
            token="token123",
            owner_chat_id="999",
            mode=TelegramBotSettings.Mode.SERVER,
            consultant_enabled=True,
            ai_enabled=True,
            ai_key="test_ai_key",
        )

        # 1. Покупатель спрашивает про выручку -> вежливый отказ
        mock_send.reset_mock()
        customer_handler.handle_customer_message(
            settings=settings,
            chat_id="888",
            from_user={"first_name": "Бекзат", "username": "bekzat"},
            text="Какая выручка магазина за сегодня?",
        )
        self.assertTrue(mock_send.called)
        reply = mock_send.call_args[0][2]
        self.assertIn("закрытыми", reply)

        # 2. Покупатель подтверждает заказ -> создается ShowcaseOrder с source="telegram"
        fake_ai_order_json = (
            'Конечно! Оформляю ваш заказ.\n\n'
            'ЗАКАЗ: {"name": "Бекзат", "phone": "+996700555666", "items": [{"title": "Кола 1л", "qty": 2}], "comment": "быстрее пожалуйста"}'
        )

        with patch("apps.main.telegram_bot.services.ai_service.generate_chat_response") as mock_ai:
            mock_ai.return_value = (fake_ai_order_json, "gemini-3.5-flash-lite")
            mock_send.reset_mock()

            customer_handler.handle_customer_message(
                settings=settings,
                chat_id="888",
                from_user={"first_name": "Бекзат", "username": "bekzat"},
                text="Да, оформляйте заказ!",
            )

            # Проверяем, что ShowcaseOrder создан
            order = ShowcaseOrder.objects.filter(company=self.company, source="telegram").first()
            self.assertIsNotNone(order)
            self.assertEqual(order.customer_name, "Бекзат")
            self.assertEqual(order.customer_phone, "+996700555666")
            self.assertEqual(order.delivery_type, ShowcaseOrder.DeliveryType.PICKUP)
            self.assertEqual(order.total, Decimal("160.00"))  # 2 x 80.00

            # Служебная строка ЗАКАЗ не должна показываться покупателю
            customer_msg = mock_send.call_args_list[-1][0][2]
            self.assertNotIn('ЗАКАЗ: {"name"', customer_msg)
            self.assertIn("успешно оформлен", customer_msg)

    @patch("apps.main.telegram_bot.services.telegram_api.send_message")
    def test_customer_start_subscribe_and_dolg(self, mock_send):
        """Покупатель /start <client_id> привязывается к клиенту и может проверять долг через /dolg."""
        settings = TelegramBotSettings.objects.create(
            company=self.company,
            token="token123",
            mode=TelegramBotSettings.Mode.SERVER,
        )

        client_rec = Client.objects.create(
            company=self.company,
            full_name="Эркинбек",
            phone="+996777000111",
        )

        # 1. /start <client_id>
        customer_handler.handle_customer_message(
            settings=settings,
            chat_id="777",
            from_user={"first_name": "Эркин"},
            text=f"/start {client_rec.id}",
        )
        client_rec.refresh_from_db()
        self.assertEqual(client_rec.telegram_chat_id, "777")

        # 2. Создаем продажу в долг
        sale_debt = Sale.objects.create(
            company=self.company,
            cashbox=self.cashbox,
            client=client_rec,
            doc_number=102,
            status=Sale.Status.PAID,
            total=Decimal("450.00"),
            payment_method=Sale.PaymentMethod.DEBT,
            debt_remaining=Decimal("450.00"),
            debt_initial=Decimal("450.00"),
        )

        # 3. /dolg
        mock_send.reset_mock()
        customer_handler.handle_customer_message(
            settings=settings,
            chat_id="777",
            from_user={"first_name": "Эркин"},
            text="/dolg",
        )
        self.assertTrue(mock_send.called)
        self.assertIn("450.00", mock_send.call_args[0][2])
