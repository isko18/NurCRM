"""
Сервис для работы с Meta API (WhatsApp Cloud API + Instagram Messaging API)

Документация:
- WhatsApp Cloud API: https://developers.facebook.com/docs/whatsapp/cloud-api
- Instagram Messaging API: https://developers.facebook.com/docs/messenger-platform/instagram
- Webhooks: https://developers.facebook.com/docs/graph-api/webhooks
"""
import hmac
import hashlib
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from django.utils import timezone
from django.db import transaction

from .models import (
    MetaBusinessAccount, WhatsAppBusinessAccount, InstagramBusinessAccount,
    Conversation, Message, MessageTemplate, Contact
)

logger = logging.getLogger(__name__)

# Meta Graph API base URL
GRAPH_API_URL = "https://graph.facebook.com/v18.0"


class MetaAPIError(Exception):
    """Ошибка Meta API"""
    def __init__(self, message: str, code: str = None, details: dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class MetaWebhookService:
    """Сервис для обработки webhook от Meta"""
    
    @staticmethod
    def verify_signature(payload: bytes, signature: str, app_secret: str) -> bool:
        """
        Проверяет подпись webhook от Meta.
        
        Args:
            payload: Тело запроса (bytes)
            signature: Заголовок X-Hub-Signature-256
            app_secret: App Secret из настроек приложения
        
        Returns:
            True если подпись валидна
        """
        if not signature or not signature.startswith('sha256='):
            return False
        
        expected_signature = hmac.new(
            app_secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        received_signature = signature[7:]  # Убираем "sha256="
        return hmac.compare_digest(expected_signature, received_signature)
    
    @staticmethod
    def verify_webhook(
        mode: str, 
        token: str, 
        challenge: str, 
        verify_token: str
    ) -> Optional[str]:
        """
        Верификация webhook при подписке.
        
        Args:
            mode: hub.mode из запроса
            token: hub.verify_token из запроса
            challenge: hub.challenge из запроса
            verify_token: Наш verify_token из настроек
        
        Returns:
            challenge если верификация успешна, иначе None
        """
        if mode == 'subscribe' and token == verify_token:
            return challenge
        return None


class WhatsAppService:
    """Сервис для работы с WhatsApp Cloud API"""
    
    def __init__(self, whatsapp_account: WhatsAppBusinessAccount):
        self.account = whatsapp_account
        self.meta_account = whatsapp_account.meta_account
        self.access_token = self.meta_account.access_token
        self.phone_number_id = whatsapp_account.phone_number_id
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        files: Optional[Dict] = None
    ) -> Dict:
        """
        Выполняет запрос к WhatsApp Cloud API.
        
        Raises:
            MetaAPIError: При ошибке API
        """
        url = f"{GRAPH_API_URL}/{endpoint}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
        }
        
        if not files:
            headers['Content-Type'] = 'application/json'
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method.upper() == 'POST':
                if files:
                    response = requests.post(url, headers=headers, data=data, files=files, timeout=60)
                else:
                    response = requests.post(url, headers=headers, json=data, timeout=30)
            elif method.upper() == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response_data = response.json() if response.content else {}
            
            if response.status_code >= 400:
                error = response_data.get('error', {})
                raise MetaAPIError(
                    message=error.get('message', 'Unknown API error'),
                    code=str(error.get('code', response.status_code)),
                    details=error
                )
            
            return response_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"WhatsApp API request error: {e}")
            raise MetaAPIError(message=str(e), code='REQUEST_ERROR')
    
    def send_text_message(
        self, 
        to: str, 
        text: str,
        reply_to_message_id: Optional[str] = None
    ) -> Dict:
        """
        Отправляет текстовое сообщение.
        
        Args:
            to: Номер телефона получателя (с кодом страны, без +)
            text: Текст сообщения
            reply_to_message_id: ID сообщения для ответа
        
        Returns:
            Ответ API с message_id
        """
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text}
        }
        
        if reply_to_message_id:
            data["context"] = {"message_id": reply_to_message_id}
        
        return self._make_request('POST', f"{self.phone_number_id}/messages", data=data)
    
    def send_media_message(
        self,
        to: str,
        media_type: str,
        media_url: str,
        caption: Optional[str] = None,
        filename: Optional[str] = None
    ) -> Dict:
        """
        Отправляет медиа сообщение (image, video, audio, document).
        
        Args:
            to: Номер телефона получателя
            media_type: Тип медиа (image, video, audio, document)
            media_url: URL медиа файла
            caption: Подпись к медиа (для image, video, document)
            filename: Имя файла (для document)
        """
        media_object = {"link": media_url}
        
        if caption and media_type in ('image', 'video', 'document'):
            media_object["caption"] = caption
        
        if filename and media_type == 'document':
            media_object["filename"] = filename
        
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": media_type,
            media_type: media_object
        }
        
        return self._make_request('POST', f"{self.phone_number_id}/messages", data=data)
    
    def send_template_message(
        self,
        to: str,
        template_name: str,
        language_code: str = "ru",
        components: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Отправляет шаблонное сообщение (HSM).
        Используется для инициации разговора или отправки вне 24-часового окна.
        
        Args:
            to: Номер телефона получателя
            template_name: Название шаблона
            language_code: Код языка (ru, en, kk и т.д.)
            components: Компоненты шаблона (header, body, button параметры)
        """
        template = {
            "name": template_name,
            "language": {"code": language_code}
        }
        
        if components:
            template["components"] = components
        
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": template
        }
        
        return self._make_request('POST', f"{self.phone_number_id}/messages", data=data)
    
    def send_location_message(
        self,
        to: str,
        latitude: float,
        longitude: float,
        name: Optional[str] = None,
        address: Optional[str] = None
    ) -> Dict:
        """Отправляет геолокацию."""
        location = {
            "latitude": latitude,
            "longitude": longitude
        }
        if name:
            location["name"] = name
        if address:
            location["address"] = address
        
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "location",
            "location": location
        }
        
        return self._make_request('POST', f"{self.phone_number_id}/messages", data=data)
    
    def mark_as_read(self, message_id: str) -> Dict:
        """Отмечает сообщение как прочитанное."""
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id
        }
        return self._make_request('POST', f"{self.phone_number_id}/messages", data=data)
    
    def get_media_url(self, media_id: str) -> str:
        """
        Получает URL для скачивания медиа.
        
        Args:
            media_id: ID медиа из webhook
        
        Returns:
            URL для скачивания (требует авторизации)
        """
        response = self._make_request('GET', media_id)
        return response.get('url', '')
    
    def download_media(self, media_url: str) -> bytes:
        """
        Скачивает медиа файл.
        
        Args:
            media_url: URL из get_media_url()
        
        Returns:
            Содержимое файла
        """
        headers = {'Authorization': f'Bearer {self.access_token}'}
        response = requests.get(media_url, headers=headers, timeout=60)
        response.raise_for_status()
        return response.content
    
    def get_templates(self) -> List[Dict]:
        """Получает список шаблонов сообщений."""
        waba_id = self.account.waba_id
        response = self._make_request(
            'GET', 
            f"{waba_id}/message_templates",
            params={"limit": 100}
        )
        return response.get('data', [])
    
    def sync_templates(self) -> int:
        """
        Синхронизирует шаблоны из Meta в локальную БД.
        
        Returns:
            Количество синхронизированных шаблонов
        """
        templates_data = self.get_templates()
        synced = 0
        
        for tpl in templates_data:
            template, created = MessageTemplate.objects.update_or_create(
                whatsapp_account=self.account,
                name=tpl['name'],
                language=tpl['language'],
                defaults={
                    'template_id': tpl.get('id', ''),
                    'category': tpl.get('category', 'UTILITY'),
                    'status': tpl.get('status', 'PENDING'),
                    'components': tpl.get('components', []),
                    'rejection_reason': tpl.get('rejected_reason', ''),
                }
            )
            synced += 1
        
        return synced


class InstagramService:
    """Сервис для работы с Instagram Messaging API"""
    
    def __init__(self, instagram_account: InstagramBusinessAccount):
        self.account = instagram_account
        self.meta_account = instagram_account.meta_account
        self.access_token = self.meta_account.access_token
        self.instagram_id = instagram_account.instagram_id
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict:
        """Выполняет запрос к Instagram Graph API."""
        url = f"{GRAPH_API_URL}/{endpoint}"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response_data = response.json() if response.content else {}
            
            if response.status_code >= 400:
                error = response_data.get('error', {})
                raise MetaAPIError(
                    message=error.get('message', 'Unknown API error'),
                    code=str(error.get('code', response.status_code)),
                    details=error
                )
            
            return response_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Instagram API request error: {e}")
            raise MetaAPIError(message=str(e), code='REQUEST_ERROR')
    
    def send_text_message(self, recipient_id: str, text: str) -> Dict:
        """
        Отправляет текстовое сообщение в Instagram Direct.
        
        Args:
            recipient_id: Instagram Scoped ID (IGSID) получателя
            text: Текст сообщения
        """
        data = {
            "recipient": {"id": recipient_id},
            "message": {"text": text}
        }
        return self._make_request('POST', f"{self.instagram_id}/messages", data=data)
    
    def send_image_message(self, recipient_id: str, image_url: str) -> Dict:
        """Отправляет изображение в Instagram Direct."""
        data = {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "image",
                    "payload": {"url": image_url}
                }
            }
        }
        return self._make_request('POST', f"{self.instagram_id}/messages", data=data)
    
    def send_generic_template(
        self, 
        recipient_id: str, 
        elements: List[Dict]
    ) -> Dict:
        """
        Отправляет generic template (карусель).
        
        Args:
            recipient_id: IGSID получателя
            elements: Список элементов карусели (title, image_url, buttons и т.д.)
        """
        data = {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "template",
                    "payload": {
                        "template_type": "generic",
                        "elements": elements
                    }
                }
            }
        }
        return self._make_request('POST', f"{self.instagram_id}/messages", data=data)
    
    def get_user_profile(self, user_id: str) -> Dict:
        """
        Получает профиль пользователя Instagram.
        
        Returns:
            {name, profile_pic}
        """
        return self._make_request(
            'GET', 
            user_id,
            params={'fields': 'name,profile_pic'}
        )


class ConversationService:
    """Сервис для управления переписками"""
    
    @staticmethod
    def get_or_create_conversation(
        company,
        channel: str,
        participant_id: str,
        whatsapp_account: WhatsAppBusinessAccount = None,
        instagram_account: InstagramBusinessAccount = None,
        participant_name: str = '',
        participant_username: str = ''
    ) -> Tuple[Conversation, bool]:
        """
        Получает или создает переписку.
        
        Returns:
            (conversation, created)
        """
        lookup = {
            'company': company,
            'channel': channel,
            'participant_id': participant_id,
        }
        
        if channel == 'whatsapp':
            lookup['whatsapp_account'] = whatsapp_account
        else:
            lookup['instagram_account'] = instagram_account
        
        defaults = {
            'participant_name': participant_name,
            'participant_username': participant_username,
        }
        
        return Conversation.objects.get_or_create(**lookup, defaults=defaults)
    
    @staticmethod
    def update_conversation_on_message(
        conversation: Conversation, 
        message: Message,
        is_inbound: bool
    ):
        """Обновляет данные переписки после нового сообщения."""
        conversation.last_message_text = message.text[:500] if message.text else f"[{message.message_type}]"
        conversation.last_message_at = message.timestamp
        conversation.messages_count = conversation.messages.count()
        
        if is_inbound:
            conversation.unread_count += 1
            # Обновляем окно сообщений (24 часа)
            conversation.window_expires_at = timezone.now() + timedelta(hours=24)
            conversation.status = 'pending'
        
        conversation.save()
    
    @staticmethod
    def link_conversation_to_contact(
        conversation: Conversation,
        phone: str = None,
        instagram_username: str = None
    ) -> Optional[Contact]:
        """
        Связывает переписку с контактом CRM.
        Ищет контакт по телефону (WhatsApp) или Instagram username.
        """
        company = conversation.company
        contact = None
        
        if phone:
            # Поиск по телефону (последние 9-10 цифр)
            phone_clean = ''.join(filter(str.isdigit, phone))[-10:]
            contact = Contact.objects.filter(
                company=company,
                phone__endswith=phone_clean
            ).first()
            
            if not contact:
                contact = Contact.objects.filter(
                    company=company,
                    whatsapp__endswith=phone_clean
                ).first()
        
        if not contact and instagram_username:
            contact = Contact.objects.filter(
                company=company,
                instagram__iexact=instagram_username
            ).first()
        
        if contact:
            conversation.contact = contact
            conversation.save(update_fields=['contact'])
        
        return contact


class WebhookProcessor:
    """Обработчик входящих webhook от Meta"""
    
    @staticmethod
    def process_whatsapp_webhook(payload: Dict, meta_account: MetaBusinessAccount):
        """
        Обрабатывает webhook от WhatsApp Cloud API.
        
        Структура payload:
        {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA_ID",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "...", "display_phone_number": "..."},
                        "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
                        "messages": [{...}],
                        "statuses": [{...}]
                    },
                    "field": "messages"
                }]
            }]
        }
        """
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                
                if change.get('field') != 'messages':
                    continue
                
                phone_number_id = value.get('metadata', {}).get('phone_number_id')
                
                try:
                    whatsapp_account = WhatsAppBusinessAccount.objects.get(
                        meta_account=meta_account,
                        phone_number_id=phone_number_id
                    )
                except WhatsAppBusinessAccount.DoesNotExist:
                    logger.warning(f"WhatsApp account not found: {phone_number_id}")
                    continue
                
                # Обработка входящих сообщений
                for msg_data in value.get('messages', []):
                    WebhookProcessor._process_whatsapp_message(
                        whatsapp_account, msg_data, value.get('contacts', [])
                    )
                
                # Обработка статусов доставки
                for status_data in value.get('statuses', []):
                    WebhookProcessor._process_whatsapp_status(status_data)
    
    @staticmethod
    def _process_whatsapp_message(
        whatsapp_account: WhatsAppBusinessAccount,
        msg_data: Dict,
        contacts_data: List[Dict]
    ):
        """Обрабатывает входящее сообщение WhatsApp."""
        message_id = msg_data.get('id')
        
        # Проверяем, не обработано ли уже
        if Message.objects.filter(meta_message_id=message_id).exists():
            return
        
        from_number = msg_data.get('from')
        timestamp = datetime.fromtimestamp(
            int(msg_data.get('timestamp', 0)), 
            tz=timezone.utc
        )
        
        # Получаем имя из contacts
        contact_name = ''
        for c in contacts_data:
            if c.get('wa_id') == from_number:
                contact_name = c.get('profile', {}).get('name', '')
                break
        
        # Получаем или создаем переписку
        conversation, created = ConversationService.get_or_create_conversation(
            company=whatsapp_account.meta_account.company,
            channel='whatsapp',
            participant_id=from_number,
            whatsapp_account=whatsapp_account,
            participant_name=contact_name
        )
        
        # Определяем тип сообщения
        msg_type = msg_data.get('type', 'text')
        
        # Извлекаем контент
        text = ''
        media_id = ''
        media_mime_type = ''
        media_caption = ''
        location_data = {}
        
        if msg_type == 'text':
            text = msg_data.get('text', {}).get('body', '')
        elif msg_type in ('image', 'video', 'audio', 'document', 'sticker'):
            media_info = msg_data.get(msg_type, {})
            media_id = media_info.get('id', '')
            media_mime_type = media_info.get('mime_type', '')
            media_caption = media_info.get('caption', '')
        elif msg_type == 'location':
            loc = msg_data.get('location', {})
            location_data = {
                'latitude': loc.get('latitude'),
                'longitude': loc.get('longitude'),
                'name': loc.get('name', ''),
                'address': loc.get('address', '')
            }
        elif msg_type == 'reaction':
            # Реакции обрабатываем отдельно
            emoji = msg_data.get('reaction', {}).get('emoji', '')
            text = f"[Реакция: {emoji}]"
            msg_type = 'reaction'
        
        # Создаем сообщение
        with transaction.atomic():
            message = Message.objects.create(
                conversation=conversation,
                meta_message_id=message_id,
                direction='inbound',
                message_type=msg_type,
                text=text,
                media_id=media_id,
                media_mime_type=media_mime_type,
                media_caption=media_caption,
                location_latitude=location_data.get('latitude'),
                location_longitude=location_data.get('longitude'),
                location_name=location_data.get('name', ''),
                location_address=location_data.get('address', ''),
                status='delivered',
                timestamp=timestamp,
                metadata=msg_data
            )
            
            # Обновляем переписку
            ConversationService.update_conversation_on_message(
                conversation, message, is_inbound=True
            )
            
            # Пытаемся связать с контактом CRM
            if created:
                ConversationService.link_conversation_to_contact(
                    conversation, phone=from_number
                )
        
        logger.info(f"Processed WhatsApp message: {message_id}")
    
    @staticmethod
    def _process_whatsapp_status(status_data: Dict):
        """Обрабатывает статус доставки сообщения WhatsApp."""
        message_id = status_data.get('id')
        status = status_data.get('status')  # sent, delivered, read, failed
        
        status_map = {
            'sent': 'sent',
            'delivered': 'delivered',
            'read': 'read',
            'failed': 'failed'
        }
        
        try:
            message = Message.objects.get(meta_message_id=message_id)
            message.status = status_map.get(status, message.status)
            
            if status == 'failed':
                errors = status_data.get('errors', [])
                if errors:
                    message.error_code = str(errors[0].get('code', ''))
                    message.error_message = errors[0].get('title', '')
            
            message.save()
            logger.info(f"Updated message status: {message_id} -> {status}")
        except Message.DoesNotExist:
            pass
    
    @staticmethod
    def process_instagram_webhook(payload: Dict, meta_account: MetaBusinessAccount):
        """
        Обрабатывает webhook от Instagram Messaging API.
        
        Структура payload:
        {
            "object": "instagram",
            "entry": [{
                "id": "INSTAGRAM_ID",
                "time": 1234567890,
                "messaging": [{
                    "sender": {"id": "SENDER_IGSID"},
                    "recipient": {"id": "OUR_IGSID"},
                    "timestamp": 1234567890,
                    "message": {...}
                }]
            }]
        }
        """
        for entry in payload.get('entry', []):
            instagram_id = entry.get('id')
            
            try:
                instagram_account = InstagramBusinessAccount.objects.get(
                    meta_account=meta_account,
                    instagram_id=instagram_id
                )
            except InstagramBusinessAccount.DoesNotExist:
                logger.warning(f"Instagram account not found: {instagram_id}")
                continue
            
            for messaging_event in entry.get('messaging', []):
                WebhookProcessor._process_instagram_message(
                    instagram_account, messaging_event
                )
    
    @staticmethod
    def _process_instagram_message(
        instagram_account: InstagramBusinessAccount,
        event: Dict
    ):
        """Обрабатывает входящее сообщение Instagram."""
        message_data = event.get('message', {})
        message_id = message_data.get('mid')
        
        if not message_id or Message.objects.filter(meta_message_id=message_id).exists():
            return
        
        sender_id = event.get('sender', {}).get('id')
        recipient_id = event.get('recipient', {}).get('id')
        
        # Определяем направление
        if recipient_id == instagram_account.instagram_id:
            direction = 'inbound'
            participant_id = sender_id
        else:
            direction = 'outbound'
            participant_id = recipient_id
        
        timestamp = datetime.fromtimestamp(
            int(event.get('timestamp', 0)) / 1000,  # Instagram отдает в миллисекундах
            tz=timezone.utc
        )
        
        # Получаем или создаем переписку
        conversation, created = ConversationService.get_or_create_conversation(
            company=instagram_account.meta_account.company,
            channel='instagram',
            participant_id=participant_id,
            instagram_account=instagram_account
        )
        
        # Извлекаем контент
        text = message_data.get('text', '')
        msg_type = 'text'
        media_url = ''
        
        attachments = message_data.get('attachments', [])
        if attachments:
            attachment = attachments[0]
            att_type = attachment.get('type', '')
            if att_type == 'image':
                msg_type = 'image'
                media_url = attachment.get('payload', {}).get('url', '')
            elif att_type == 'video':
                msg_type = 'video'
                media_url = attachment.get('payload', {}).get('url', '')
            elif att_type == 'audio':
                msg_type = 'audio'
                media_url = attachment.get('payload', {}).get('url', '')
        
        # Создаем сообщение
        with transaction.atomic():
            message = Message.objects.create(
                conversation=conversation,
                meta_message_id=message_id,
                direction=direction,
                message_type=msg_type,
                text=text,
                media_url=media_url,
                status='delivered' if direction == 'inbound' else 'sent',
                timestamp=timestamp,
                metadata=event
            )
            
            if direction == 'inbound':
                ConversationService.update_conversation_on_message(
                    conversation, message, is_inbound=True
                )
        
        logger.info(f"Processed Instagram message: {message_id}")
