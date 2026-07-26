from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch

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
        self.company = Company.objects.create(name="Test Company Wazzup")
        self.role = CustomRole.objects.create(name="Менеджер продаж", company=self.company)

        self.user1 = User.objects.create_user(
            username="manager1",
            email="mgr1@wazzup.test",
            password="pass",
            company=self.company,
            custom_role=self.role
        )
        self.user2 = User.objects.create_user(
            username="manager2",
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

    def test_wazzup_webhook_creates_lead_and_distributes(self):
        """Тест 1: Входящий вебхук Wazzup автоматически создает лид и распределяет по Round-Robin"""
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

        response = self.client.post("/api/consalting/wazzup/webhook/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Проверяем InboundLeadConsalting
        inbound = InboundLeadConsalting.objects.filter(external_id="wz_msg_1001").first()
        self.assertIsNotNone(inbound)
        self.assertEqual(inbound.phone, "+79991112233")
        self.assertIsNotNone(inbound.owner)

        # Проверяем LeadConsalting в воронке
        lead = LeadConsalting.objects.filter(phone="+79991112233").first()
        self.assertIsNotNone(lead)
        self.assertEqual(lead.owner, inbound.owner)

        # Проверяем WhatsAppMessageConsalting
        wa_msg = WhatsAppMessageConsalting.objects.filter(message_id="wz_msg_1001").first()
        self.assertIsNotNone(wa_msg)
        self.assertEqual(wa_msg.text, "Здравствуйте! Хочу узнать стоимость ваших услуг.")

    def test_wazzup_webhook_idempotency(self):
        """Тест 2: Повторный вебхук с тем же messageId игнорируется (идемпотентность)"""
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
        r1 = self.client.post("/api/consalting/wazzup/webhook/", payload, format="json")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        count_before = WhatsAppMessageConsalting.objects.filter(message_id="wz_msg_duplicate_check").count()
        self.assertEqual(count_before, 1)

        # Повторный вызов дубликата
        r2 = self.client.post("/api/consalting/wazzup/webhook/", payload, format="json")
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        count_after = WhatsAppMessageConsalting.objects.filter(message_id="wz_msg_duplicate_check").count()
        self.assertEqual(count_after, 1)  # Сообщение не продублировалось!

    @patch("requests.post")
    def test_wazzup_send_message_api(self, mock_post):
        """Тест 3: Отправка сообщения из CRM воронки консалтинга через Wazzup API"""
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

        response = self.client.post(
            f"/api/consalting/wazzup-accounts/{self.account.id}/send-message/",
            send_payload,
            format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "sent")
