from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock

from apps.main.models import Company
from apps.users.models import User, CustomRole
from apps.consalting.models import (
    WazzupAccountConsalting,
    InboundLeadConsalting,
    LeadConsalting,
    WhatsAppMessageConsalting,
    LeadDistributionSettingsConsalting,
    FunnelConsalting,
    FunnelStageConsalting,
)


class WazzupConsaltingIntegrationTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create(email="owner@wazzup.test", first_name="Owner")
        self.company = Company.objects.create(name="Test Company Wazzup", owner=self.owner)
        self.owner.company = self.company
        self.owner.save()
        self.role = CustomRole.objects.create(name="Менеджер продаж", company=self.company)

        self.user1 = User.objects.create_user(
            email="mgr1@wazzup.test",
            password="pass",
            company=self.company,
            custom_role=self.role
        )
        self.user2 = User.objects.create_user(
            email="mgr2@wazzup.test",
            password="pass",
            company=self.company,
            custom_role=self.role
        )

        self.account = WazzupAccountConsalting.objects.create(
            company=self.company,
            api_key="test_wazzup_key_123",
            channel_id="channel_wz_test_999",
            integration_type="whatsapp",
            is_active=True
        )

        # Настройки автораспределения лидов (Round-Robin)
        self.settings = LeadDistributionSettingsConsalting.objects.create(
            company=self.company,
            enabled=True,
            strategy=LeadDistributionSettingsConsalting.Strategy.ROUND_ROBIN
        )
        self.settings.roles.add(self.role)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    @patch("apps.consalting.funnel.realtime.reliable_group_send")
    @patch("apps.consalting.tasks.process_wazzup_webhook_side_effects.delay")
    def test_wazzup_webhook_realtime_path_without_celery(self, mock_celery_delay, mock_group_send):
        """Тест 1: Входящий вебхук Wazzup синхронно создает запись в БД и шлет WS даже при отключенном/замоканном Celery"""
        payload = {
            "messages": [
                {
                    "channelId": "channel_wz_test_999",
                    "messageId": "wz_msg_1001",
                    "chatId": "79991112233",
                    "text": "Здравствуйте! Хочу узнать стоимость ваших услуг.",
                    "isInbound": True,
                    "authorName": "Иван Иванов"
                }
            ]
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/consalting/wazzup/webhook/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Подтверждаем, что Celery task была вызвана в фоновом режиме
        self.assertTrue(mock_celery_delay.called)

        # Проверяем, что WhatsAppMessageConsalting мгновенно создан в БД во время HTTP-запроса
        wa_msg = WhatsAppMessageConsalting.objects.filter(message_id="wz_msg_1001").first()
        self.assertIsNotNone(wa_msg)
        self.assertEqual(wa_msg.text, "Здравствуйте! Хочу узнать стоимость ваших услуг.")

        # Проверяем, что WebSocket broadcast с new_message ушел по каналу после commit
        self.assertTrue(mock_group_send.called)
        found_new_msg = False
        for call in mock_group_send.call_args_list:
            groups_and_events = call[0][0]
            for group, envelope in groups_and_events:
                if envelope.get("type") == "wazzup_event" and envelope.get("event", {}).get("type") == "new_message":
                    found_new_msg = True
                    self.assertEqual(envelope["event"]["data"]["message_id"], "wz_msg_1001")
        self.assertTrue(found_new_msg, "WebSocket new_message broadcast was not triggered")

    @patch("apps.consalting.funnel.realtime.reliable_group_send")
    def test_wazzup_webhook_idempotency(self, mock_group_send):
        """Тест 2: Повторный вебхук с тем же messageId игнорируется и не шлет дублирующий WS"""
        payload = {
            "messages": [
                {
                    "channelId": "channel_wz_test_999",
                    "messageId": "wz_msg_duplicate_check",
                    "chatId": "79998887766",
                    "text": "Тестовое сообщение 1",
                    "isInbound": True,
                }
            ]
        }

        # Первый вызов
        with self.captureOnCommitCallbacks(execute=True):
            r1 = self.client.post("/api/consalting/wazzup/webhook/", payload, format="json")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        count_before = WhatsAppMessageConsalting.objects.filter(message_id="wz_msg_duplicate_check").count()
        self.assertEqual(count_before, 1)

        mock_group_send.reset_mock()

        # Повторный вызов дубликата
        with self.captureOnCommitCallbacks(execute=True):
            r2 = self.client.post("/api/consalting/wazzup/webhook/", payload, format="json")
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        count_after = WhatsAppMessageConsalting.objects.filter(message_id="wz_msg_duplicate_check").count()
        self.assertEqual(count_after, 1)
        # Дубликат не должен отправлять второй new_message по WebSocket
        self.assertFalse(mock_group_send.called)

    @patch("apps.consalting.funnel.realtime.reliable_group_send")
    def test_wazzup_webhook_statuses_broadcasts_message_status(self, mock_group_send):
        """Тест 3: Вебхук statuses обновляет статус сообщения и шлет message_status по WS с data.id = wa_message.id"""
        funnel = FunnelConsalting.objects.create(company=self.company, name="Воронка 1")
        stage = FunnelStageConsalting.objects.create(company=self.company, funnel=funnel, name="Стадия 1", order=1)
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=funnel,
            stage=stage,
            title="Лид для статусов",
            phone="+79997778899"
        )
        wa_msg = WhatsAppMessageConsalting.objects.create(
            company=self.company,
            lead=lead,
            message_id="wz_msg_status_test",
            direction=WhatsAppMessageConsalting.Direction.OUTBOUND,
            text="Тест статуса",
            status=WhatsAppMessageConsalting.Status.PENDING
        )

        status_payload = {
            "statuses": [
                {
                    "messageId": "wz_msg_status_test",
                    "status": "delivered"
                }
            ]
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/consalting/wazzup/webhook/", status_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        wa_msg.refresh_from_db()
        self.assertEqual(wa_msg.status, WhatsAppMessageConsalting.Status.DELIVERED)

        # Проверяем WebSocket broadcast события message_status
        found_msg_status = False
        for call in mock_group_send.call_args_list:
            groups_and_events = call[0][0]
            for group, envelope in groups_and_events:
                if envelope.get("type") == "wazzup_event" and envelope.get("event", {}).get("type") == "message_status":
                    found_msg_status = True
                    self.assertEqual(envelope["event"]["data"]["id"], str(wa_msg.id))
                    self.assertEqual(envelope["event"]["data"]["status"], "delivered")
        self.assertTrue(found_msg_status, "WebSocket message_status broadcast was not triggered")

    @patch("requests.post")
    def test_wazzup_send_message_api(self, mock_post):
        """Тест 4: Отправка сообщения из CRM воронки консалтинга через Wazzup API"""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"messageId": "wz_out_resp_123"}

        funnel = FunnelConsalting.objects.create(company=self.company, name="Воронка 1")
        stage = FunnelStageConsalting.objects.create(company=self.company, funnel=funnel, name="Стадия 1", order=1)
        lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=funnel,
            stage=stage,
            title="Лид для теста отправки",
            phone="+79990001122"
        )

        send_payload = {
            "lead_id": str(lead.id),
            "message": "Приветствуем Вас!"
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/api/consalting/wazzup-accounts/{self.account.id}/send-message/",
                send_payload,
                format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "pending")

        wa_msg = WhatsAppMessageConsalting.objects.get(id=response.data["id"])
        self.assertEqual(wa_msg.status, "sent")
        self.assertEqual(wa_msg.message_id, "wz_out_resp_123")

    def test_wazzup_upload_media_api(self):
        """Тест 5: Загрузка медиафайла менеджером через POST /upload/"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        test_file = SimpleUploadedFile("photo.jpg", b"file_bytes_content", content_type="image/jpeg")

        response = self.client.post(
            f"/api/consalting/wazzup-accounts/{self.account.id}/upload/",
            {"file": test_file},
            format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("url", response.data)
        self.assertIn("content_uri", response.data)

