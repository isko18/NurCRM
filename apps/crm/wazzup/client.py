import requests
import logging

logger = logging.getLogger(__name__)


class WazzupClient:
    """
    HTTP Клиент для Wazzup API v3 (https://api.wazzup24.com/v3)
    """

    def __init__(self, api_key: str, api_url: str = "https://api.wazzup24.com"):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def send_message(self, channel_id: str, chat_id: str, chat_type: str, text: str, content_uri: str = None):
        """
        Отправка сообщения через Wazzup (POST /v3/message)
        """
        endpoint = f"{self.api_url}/v3/message"
        payload = {
            "channelId": channel_id,
            "chatId": chat_id,
            "chatType": chat_type,  # 'whatsapp' or 'instagram' or 'telegram'
            "text": text,
        }
        if content_uri:
            payload["contentUri"] = content_uri

        try:
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Wazzup send_message failed: {e}")
            raise RuntimeError(f"Не удалось отправить сообщение через Wazzup: {e}")

    def setup_webhooks(self, webhooks_url: str):
        """
        Настройка URL вебхука в Wazzup (POST /v3/webhooks)
        """
        endpoint = f"{self.api_url}/v3/webhooks"
        payload = {
            "webhooksUri": webhooks_url,
            "subscriptions": {
                "messagesAndStatuses": True
            }
        }
        try:
            response = requests.post(endpoint, json=payload, headers=self.headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Wazzup setup_webhooks failed: {e}")
            raise RuntimeError(f"Не удалось зарегистрировать Webhook в Wazzup: {e}")
