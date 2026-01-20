"""
Сервис для работы с Wazzupp API
"""
import requests
import logging
from datetime import datetime
from typing import Dict, List, Optional
from django.utils import timezone
from .models import WazzuppAccount, WazzuppMessage, Contact

logger = logging.getLogger(__name__)


class WazzuppService:
    """Сервис для работы с Wazzupp API"""
    
    def __init__(self, account: WazzuppAccount):
        self.account = account
        self.api_url = account.api_url.rstrip('/')
        self.api_key = account.api_key
        self.instance_id = account.instance_id
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Optional[Dict]:
        """Выполняет запрос к Wazzupp API"""
        url = f"{self.api_url}/{endpoint}"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method.upper() == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=30)
            elif method.upper() == 'PUT':
                response = requests.put(url, headers=headers, json=data, timeout=30)
            elif method.upper() == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json() if response.content else {}
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Wazzupp API error: {e}")
            return None
    
    def check_connection(self) -> bool:
        """Проверяет подключение к Wazzupp API"""
        try:
            # Попытка получить информацию об инстансе
            result = self._make_request('GET', f'instance/status/{self.instance_id}')
            if result:
                self.account.is_connected = True
                self.account.last_sync = timezone.now()
                self.account.save()
                return True
        except Exception as e:
            logger.error(f"Connection check failed: {e}")
        
        self.account.is_connected = False
        self.account.save()
        return False
    
    def send_message(
        self,
        to: str,
        message: str,
        message_type: str = 'text',
        media_url: Optional[str] = None,
        caption: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Отправляет сообщение через Wazzupp
        
        Args:
            to: Номер получателя или username
            message: Текст сообщения
            message_type: Тип сообщения (text, image, video, audio, document)
            media_url: URL медиа файла (для типов кроме text)
            caption: Подпись к медиа
        """
        endpoint = f'message/sendText/{self.instance_id}'
        
        if message_type == 'text':
            data = {
                'number': to,
                'text': message
            }
        elif message_type in ['image', 'video', 'audio', 'document']:
            endpoint = f'message/sendMedia/{self.instance_id}'
            data = {
                'number': to,
                'media': media_url,
                'caption': caption or message,
                'mediatype': message_type
            }
        else:
            logger.error(f"Unsupported message type: {message_type}")
            return None
        
        result = self._make_request('POST', endpoint, data=data)
        
        # Сохраняем сообщение в БД
        if result:
            self._save_message(
                message_id=result.get('key', {}).get('id', ''),
                from_number=self.instance_id or '',
                to_number=to,
                text=message,
                message_type=message_type,
                media_url=media_url,
                caption=caption,
                is_incoming=False,
                status='sent'
            )
        
        return result
    
    def get_messages(
        self,
        limit: int = 50,
        chat_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Получает сообщения из Wazzupp
        
        Args:
            limit: Количество сообщений
            chat_id: ID чата (опционально)
        """
        endpoint = f'message/fetchMessages/{self.instance_id}'
        params = {'limit': limit}
        if chat_id:
            params['chatId'] = chat_id
        
        result = self._make_request('GET', endpoint, params=params)
        return result.get('messages', []) if result else []
    
    def sync_messages(self) -> int:
        """
        Синхронизирует сообщения из Wazzupp в БД
        
        Returns:
            Количество синхронизированных сообщений
        """
        messages = self.get_messages(limit=100)
        synced_count = 0
        
        for msg_data in messages:
            message_id = msg_data.get('key', {}).get('id', '')
            if not message_id:
                continue
            
            # Проверяем, есть ли уже такое сообщение
            if WazzuppMessage.objects.filter(message_id=message_id).exists():
                continue
            
            # Ищем или создаем контакт
            from_number = msg_data.get('key', {}).get('remoteJid', '').split('@')[0]
            contact = self._get_or_create_contact(from_number)
            
            # Сохраняем сообщение
            message = self._save_message(
                message_id=message_id,
                from_number=from_number,
                to_number=self.instance_id or '',
                text=msg_data.get('message', {}).get('conversation') or 
                     msg_data.get('message', {}).get('extendedTextMessage', {}).get('text', ''),
                message_type=self._detect_message_type(msg_data),
                media_url=self._extract_media_url(msg_data),
                caption=msg_data.get('message', {}).get('imageMessage', {}).get('caption', ''),
                is_incoming=True,
                status='delivered',
                contact=contact,
                timestamp=self._parse_timestamp(msg_data)
            )
            
            if message:
                synced_count += 1
        
        # Обновляем время последней синхронизации
        self.account.last_sync = timezone.now()
        self.account.save()
        
        return synced_count
    
    def _get_or_create_contact(self, phone: str) -> Optional[Contact]:
        """Находит или создает контакт по номеру телефона"""
        if not phone:
            return None
        
        # Очищаем номер от лишних символов
        phone_clean = ''.join(filter(str.isdigit, phone))
        
        # Ищем существующий контакт
        contact = Contact.objects.filter(
            company=self.account.company,
            phone__contains=phone_clean[-9:]  # Последние 9 цифр
        ).first()
        
        if not contact:
            # Создаем новый контакт
            contact = Contact.objects.create(
                company=self.account.company,
                phone=phone_clean,
                first_name=f"Контакт {phone_clean[-4:]}",
                source=self.account.get_integration_type_display(),
                is_active=True
            )
        
        # Обновляем WhatsApp/Instagram в зависимости от типа интеграции
        if self.account.integration_type == 'whatsapp':
            if not contact.whatsapp:
                contact.whatsapp = phone_clean
        elif self.account.integration_type == 'instagram':
            if not contact.instagram:
                contact.instagram = phone_clean
        
        contact.save()
        return contact
    
    def _save_message(
        self,
        message_id: str,
        from_number: str,
        to_number: str,
        text: Optional[str] = None,
        message_type: str = 'text',
        media_url: Optional[str] = None,
        caption: Optional[str] = None,
        is_incoming: bool = True,
        status: str = 'sent',
        contact: Optional[Contact] = None,
        timestamp: Optional[datetime] = None
    ) -> Optional[WazzuppMessage]:
        """Сохраняет сообщение в БД"""
        try:
            if not contact:
                contact = self._get_or_create_contact(from_number)
            
            message = WazzuppMessage.objects.create(
                account=self.account,
                contact=contact,
                message_id=message_id,
                from_number=from_number,
                to_number=to_number,
                message_type=message_type,
                text=text,
                media_url=media_url,
                caption=caption,
                is_incoming=is_incoming,
                status=status,
                timestamp=timestamp or timezone.now()
            )
            
            return message
        except Exception as e:
            logger.error(f"Error saving message: {e}")
            return None
    
    def _detect_message_type(self, msg_data: Dict) -> str:
        """Определяет тип сообщения из данных Wazzupp"""
        message = msg_data.get('message', {})
        if 'imageMessage' in message:
            return 'image'
        elif 'videoMessage' in message:
            return 'video'
        elif 'audioMessage' in message:
            return 'audio'
        elif 'documentMessage' in message:
            return 'document'
        elif 'locationMessage' in message:
            return 'location'
        elif 'contactMessage' in message:
            return 'contact'
        elif 'stickerMessage' in message:
            return 'sticker'
        else:
            return 'text'
    
    def _extract_media_url(self, msg_data: Dict) -> Optional[str]:
        """Извлекает URL медиа из данных сообщения"""
        message = msg_data.get('message', {})
        for msg_type in ['imageMessage', 'videoMessage', 'audioMessage', 'documentMessage']:
            if msg_type in message:
                return message[msg_type].get('url', '')
        return None
    
    def _parse_timestamp(self, msg_data: Dict) -> datetime:
        """Парсит timestamp из данных сообщения"""
        timestamp = msg_data.get('messageTimestamp', 0)
        if timestamp:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return timezone.now()
