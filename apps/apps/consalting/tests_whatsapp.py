from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User, Company
from apps.consalting.models import (
    LeadConsalting, FunnelConsalting, FunnelStageConsalting, WhatsAppMessageConsalting
)
from apps.consalting.funnel.whatsapp import WhatsAppConsaltingService


class WhatsAppConsaltingServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(email="owner@test.com", first_name="Owner")
        self.company = Company.objects.create(name="Consulting Co", owner=self.owner)
        self.funnel = FunnelConsalting.objects.create(company=self.company, name="Main Funnel")
        self.stage = FunnelStageConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            name="New Lead",
            stage_type=FunnelStageConsalting.StageType.NEW_LEAD,
            order=1
        )
        self.lead = LeadConsalting.objects.create(
            company=self.company,
            funnel=self.funnel,
            stage=self.stage,
            title="Test WhatsApp Lead",
            phone="+79991234567"
        )
        self.client = APIClient()

    def test_send_message_simulation(self):
        """Проверка отправки сообщения в режиме симуляции (без ключей Meta/Node)."""
        wa_msg = WhatsAppConsaltingService.send_message(self.lead, "Привет! Ознакомьтесь с КП.")
        self.assertIsNotNone(wa_msg)
        self.assertEqual(wa_msg.direction, WhatsAppMessageConsalting.Direction.OUTBOUND)
        self.assertEqual(wa_msg.status, WhatsAppMessageConsalting.Status.SENT)
        self.assertEqual(wa_msg.text, "Привет! Ознакомьтесь с КП.")
        self.assertTrue(self.lead.activities.filter(title="WhatsApp (исходящее)").exists())

    @patch("requests.post")
    def test_send_message_meta_api(self, mock_post):
        """Проверка отправки сообщения через Meta WhatsApp Cloud API."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"messages": [{"id": "wamid.HBgL123456"}]}
        mock_post.return_value = mock_resp

        with self.settings(WHATSAPP_ACCESS_TOKEN="token123", WHATSAPP_PHONE_NUMBER_ID="phone123"):
            wa_msg = WhatsAppConsaltingService.send_message(self.lead, "Hello Meta API")
            self.assertEqual(wa_msg.status, WhatsAppMessageConsalting.Status.SENT)
            self.assertEqual(wa_msg.message_id, "wamid.HBgL123456")

    def test_handle_incoming_message_creates_lead_if_not_found(self):
        """При входящем сообщении с нового номера должен создаться новый лид."""
        new_phone = "79998887766"
        lead = WhatsAppConsaltingService.handle_incoming_message(
            company_id=self.company.id,
            phone=new_phone,
            text="Здравствуйте, меня интересует консультация",
            message_id="wamid.inbound.001"
        )
        self.assertIsNotNone(lead)
        self.assertEqual(lead.phone, new_phone)
        self.assertTrue(WhatsAppMessageConsalting.objects.filter(message_id="wamid.inbound.001").exists())

    def test_meta_webhook_verification_get(self):
        """Проверка GET метода для верификации вебхука Meta."""
        url = reverse("whatsapp-consalting-webhook")
        if not url.endswith("/"):
            url += "/"
        with self.settings(WHATSAPP_VERIFY_TOKEN="secret_token_123", SECURE_SSL_REDIRECT=False):
            response = self.client.get(url, {
                "hub.mode": "subscribe",
                "hub.verify_token": "secret_token_123",
                "hub.challenge": "ch_987654"
            }, secure=True)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.content.decode("utf-8"), "ch_987654")

    def test_meta_webhook_incoming_message_post(self):
        """Проверка POST метода приема сообщения от Meta Cloud API."""
        base_url = reverse("whatsapp-consalting-webhook")
        if not base_url.endswith("/"):
            base_url += "/"
        url = f"{base_url}?company_id={self.company.id}"
        meta_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "waba_id_123",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "phone_123"},
                                "contacts": [{"profile": {"name": "Клиент"}}],
                                "messages": [
                                    {
                                        "from": "79001112233",
                                        "id": "wamid.meta.inbound.77",
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": "Добрый день, какая стоимость?"}
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        with self.settings(SECURE_SSL_REDIRECT=False):
            response = self.client.post(url, data=meta_payload, format="json", secure=True)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(WhatsAppMessageConsalting.objects.filter(message_id="wamid.meta.inbound.77").exists())
