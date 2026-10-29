"""
Сервис для работы с WhatsApp Business API.
"""
import logging
import requests
from typing import Dict, Optional, List
from datetime import datetime

from django.utils import timezone
from .models import WhatsAppMessage, WhatsAppContact, WhatsAppConfig

logger = logging.getLogger(__name__)


class WhatsAppAPIClient:
    """Клиент для работы с WhatsApp Business API."""
    
    API_BASE_URL = "https://graph.instagram.com/v18.0"
    
    def __init__(self, access_token: str, phone_number_id: str):
        """
        Инициализация клиента.
        
        Args:
            access_token: Bearer token для доступа к API
            phone_number_id: ID номера телефона в WhatsApp
        """
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
    
    def send_text_message(
        self,
        phone_number: str,
        message: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Отправка текстового сообщения.
        
        Args:
            phone_number: Номер телефона получателя (в формате +7XXXXXXXXXX)
            message: Текст сообщения
            metadata: Дополнительные данные
            
        Returns:
            Ответ от API
        """
        url = f"{self.API_BASE_URL}/{self.phone_number_id}/messages"
        
        data = {
            "messaging_product": "whatsapp",
            "to": phone_number.replace("+", ""),
            "type": "text",
            "text": {"body": message},
        }
        
        try:
            response = requests.post(url, json=data, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка отправки сообщения WhatsApp: {str(e)}")
            raise
    
    def send_template_message(
        self,
        phone_number: str,
        template_name: str,
        template_language: str = "ru",
        parameters: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Отправка сообщения по шаблону.
        
        Args:
            phone_number: Номер телефона получателя
            template_name: Имя шаблона в WhatsApp
            template_language: Язык шаблона
            parameters: Параметры для заполнения в шаблон
            
        Returns:
            Ответ от API
        """
        url = f"{self.API_BASE_URL}/{self.phone_number_id}/messages"
        
        data = {
            "messaging_product": "whatsapp",
            "to": phone_number.replace("+", ""),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": template_language},
            },
        }
        
        if parameters:
            data["template"]["components"] = [
                {"type": "body", "parameters": parameters}
            ]
        
        try:
            response = requests.post(url, json=data, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка отправки шаблона WhatsApp: {str(e)}")
            raise
    
    def mark_as_read(self, message_id: str) -> Dict:
        """
        Отметить сообщение как прочитанное.
        
        Args:
            message_id: ID сообщения в WhatsApp
            
        Returns:
            Ответ от API
        """
        url = f"{self.API_BASE_URL}/{message_id}"
        
        data = {
            "status": "read",
        }
        
        try:
            response = requests.post(url, json=data, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка отметки сообщения как прочитанного: {str(e)}")
            raise
    
    def get_message_status(self, message_id: str) -> Dict:
        """
        Получить статус сообщения.
        
        Args:
            message_id: ID сообщения в WhatsApp
            
        Returns:
            Ответ от API
        """
        url = f"{self.API_BASE_URL}/{message_id}"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка получения статуса сообщения: {str(e)}")
            raise


class WhatsAppService:
    """Основной сервис для работы с WhatsApp интеграцией."""
    
    def __init__(self, config: WhatsAppConfig):
        """
        Инициализация сервиса.
        
        Args:
            config: Объект конфигурации WhatsApp
        """
        if not config.is_active:
            raise ValueError("WhatsApp конфигурация неактивна")
        
        self.config = config
        self.client = WhatsAppAPIClient(config.access_token, config.phone_number_id)
    
    def send_message(
        self,
        phone_number: str,
        message: str,
        content_type: Optional[str] = None,
        object_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> WhatsAppMessage:
        """
        Отправить сообщение и сохранить в базу.
        
        Args:
            phone_number: Номер телефона получателя
            message: Текст сообщения
            content_type: Тип связанного объекта
            object_id: ID связанного объекта
            metadata: Дополнительные данные
            
        Returns:
            Объект WhatsAppMessage
        """
        try:
            # Отправляем сообщение через API
            api_response = self.client.send_text_message(phone_number, message)
            message_id = api_response.get("messages", [{}])[0].get("id")
            
            # Сохраняем в БД
            whatsapp_message = WhatsAppMessage.objects.create(
                config=self.config,
                whatsapp_message_id=message_id,
                phone_number=phone_number,
                message_type="text",
                content=message,
                direction="outbound",
                status="sent",
                content_type=content_type,
                object_id=object_id,
                metadata=metadata or {},
                sent_at=timezone.now(),
            )
            
            # Обновляем контакт
            contact, _ = WhatsAppContact.objects.get_or_create(
                config=self.config,
                phone_number=phone_number,
                defaults={
                    "content_type": content_type,
                    "object_id": object_id,
                }
            )
            contact.last_message_at = timezone.now()
            contact.save(update_fields=["last_message_at"])
            
            logger.info(f"Сообщение отправлено: {message_id}")
            return whatsapp_message
            
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {str(e)}")
            
            # Сохраняем как не отправленное
            whatsapp_message = WhatsAppMessage.objects.create(
                config=self.config,
                whatsapp_message_id=f"temp_{uuid.uuid4()}",
                phone_number=phone_number,
                message_type="text",
                content=message,
                direction="outbound",
                status="failed",
                content_type=content_type,
                object_id=object_id,
                metadata={"error": str(e), **(metadata or {})},
            )
            
            return whatsapp_message
    
    def send_template_message(
        self,
        phone_number: str,
        template_name: str,
        parameters: Optional[List[Dict]] = None,
        content_type: Optional[str] = None,
        object_id: Optional[str] = None,
    ) -> WhatsAppMessage:
        """
        Отправить сообщение по шаблону.
        
        Args:
            phone_number: Номер телефона получателя
            template_name: Имя шаблона
            parameters: Параметры для шаблона
            content_type: Тип связанного объекта
            object_id: ID связанного объекта
            
        Returns:
            Объект WhatsAppMessage
        """
        try:
            api_response = self.client.send_template_message(
                phone_number, template_name, parameters=parameters
            )
            message_id = api_response.get("messages", [{}])[0].get("id")
            
            template_preview = f"{template_name}"
            if parameters:
                template_preview += f" ({len(parameters)} параметров)"
            
            whatsapp_message = WhatsAppMessage.objects.create(
                config=self.config,
                whatsapp_message_id=message_id,
                phone_number=phone_number,
                message_type="text",
                content=template_preview,
                direction="outbound",
                status="sent",
                content_type=content_type,
                object_id=object_id,
                metadata={
                    "template_name": template_name,
                    "parameters": parameters or [],
                },
                sent_at=timezone.now(),
            )
            
            return whatsapp_message
            
        except Exception as e:
            logger.error(f"Ошибка отправки шаблона: {str(e)}")
            raise
    
    def handle_webhook(self, data: Dict) -> Optional[WhatsAppMessage]:
        """
        Обработка вебхука от WhatsApp.
        
        Args:
            data: Данные из вебхука
            
        Returns:
            Объект WhatsAppMessage или None
        """
        try:
            # Извлекаем данные из вебхука
            entry = data.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            
            messages = value.get("messages", [])
            statuses = value.get("statuses", [])
            
            # Обработка входящих сообщений
            if messages:
                return self._process_inbound_message(messages[0])
            
            # Обработка статусов отправленных сообщений
            if statuses:
                return self._process_message_status(statuses[0])
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {str(e)}")
            return None
    
    def _process_inbound_message(self, message_data: Dict) -> WhatsAppMessage:
        """
        Обработка входящего сообщения.
        
        Args:
            message_data: Данные сообщения из вебхука
            
        Returns:
            Объект WhatsAppMessage
        """
        message_id = message_data.get("id")
        phone_number = message_data.get("from")
        timestamp = int(message_data.get("timestamp", 0))
        message_type = message_data.get("type", "text")
        
        # Извлекаем содержание в зависимости от типа
        content = ""
        media_url = None
        
        if message_type == "text":
            content = message_data.get("text", {}).get("body", "")
        elif message_type in ["image", "document", "audio", "video"]:
            media_data = message_data.get(message_type, {})
            media_url = media_data.get("link") or media_data.get("url")
            content = media_data.get("caption") or f"[{message_type.upper()}]"
        
        # Обновляем или создаем контакт
        contact, _ = WhatsAppContact.objects.get_or_create(
            config=self.config,
            phone_number=phone_number,
        )
        contact.last_message_at = timezone.now()
        contact.save(update_fields=["last_message_at"])
        
        # Сохраняем сообщение
        whatsapp_message, created = WhatsAppMessage.objects.get_or_create(
            whatsapp_message_id=message_id,
            defaults={
                "config": self.config,
                "phone_number": phone_number,
                "message_type": message_type,
                "content": content,
                "media_url": media_url,
                "direction": "inbound",
                "status": "delivered",
                "metadata": message_data,
                "created_at": datetime.fromtimestamp(timestamp, tz=timezone.utc),
            }
        )
        
        # Отправляем автоответ если включен
        if self.config.auto_reply_enabled and self.config.auto_reply_message:
            try:
                self.send_message(
                    phone_number,
                    self.config.auto_reply_message,
                    content_type="WhatsAppMessage",
                    object_id=str(whatsapp_message.id)
                )
            except Exception as e:
                logger.error(f"Ошибка отправки автоответа: {str(e)}")
        
        # Отмечаем сообщение как прочитанное
        try:
            self.client.mark_as_read(message_id)
        except Exception as e:
            logger.error(f"Ошибка отметки сообщения как прочитанного: {str(e)}")
        
        logger.info(f"Входящее сообщение обработано: {message_id}")
        return whatsapp_message
    
    def _process_message_status(self, status_data: Dict) -> Optional[WhatsAppMessage]:
        """
        Обработка статуса сообщения.
        
        Args:
            status_data: Данные статуса из вебхука
            
        Returns:
            Объект WhatsAppMessage или None
        """
        message_id = status_data.get("id")
        status = status_data.get("status")
        
        status_mapping = {
            "sent": "sent",
            "delivered": "delivered",
            "read": "read",
            "failed": "failed",
        }
        
        try:
            whatsapp_message = WhatsAppMessage.objects.get(
                whatsapp_message_id=message_id
            )
            whatsapp_message.status = status_mapping.get(status, status)
            whatsapp_message.save(update_fields=["status", "updated_at"])
            
            logger.info(f"Статус сообщения обновлен: {message_id} -> {status}")
            return whatsapp_message
            
        except WhatsAppMessage.DoesNotExist:
            logger.warning(f"Сообщение не найдено: {message_id}")
            return None


import uuid
