"""
Утилиты и вспомогательные функции для WhatsApp интеграции.
"""
import logging
from typing import Optional, Dict, List

from .models import WhatsAppConfig, WhatsAppMessage
from .services import WhatsAppService

logger = logging.getLogger(__name__)


def get_whatsapp_service(company_id, branch_id: Optional[str] = None) -> Optional[WhatsAppService]:
    """
    Получить сервис WhatsApp для компании/филиала.
    
    Args:
        company_id: ID компании
        branch_id: ID филиала (опционально)
        
    Returns:
        Инстанс WhatsAppService или None если конфигурация не найдена
        
    Example:
        service = get_whatsapp_service(company_id)
        if service:
            service.send_message("+79991234567", "Привет!")
    """
    try:
        if branch_id:
            config = WhatsAppConfig.objects.get(
                company_id=company_id,
                branch_id=branch_id,
                is_active=True,
            )
        else:
            config = WhatsAppConfig.objects.filter(
                company_id=company_id,
                branch_id__isnull=True,
                is_active=True,
            ).first()
        
        if config:
            return WhatsAppService(config)
    except Exception as e:
        logger.error(f"Ошибка получения WhatsApp сервиса: {str(e)}")
    
    return None


def send_whatsapp_message(
    company_id,
    phone_number: str,
    message: str,
    branch_id: Optional[str] = None,
    content_type: Optional[str] = None,
    object_id: Optional[str] = None,
) -> Optional[WhatsAppMessage]:
    """
    Отправить сообщение через WhatsApp.
    
    Args:
        company_id: ID компании
        phone_number: Номер телефона получателя
        message: Текст сообщения
        branch_id: ID филиала (опционально)
        content_type: Тип связанного объекта
        object_id: ID связанного объекта
        
    Returns:
        Объект WhatsAppMessage или None
        
    Example:
        msg = send_whatsapp_message(
            company_id="uuid",
            phone_number="+79991234567",
            message="Привет, это автоматическое сообщение!",
            content_type="Lead",
            object_id="lead_uuid"
        )
    """
    service = get_whatsapp_service(company_id, branch_id)
    
    if not service:
        logger.warning(f"WhatsApp сервис не найден для компании {company_id}")
        return None
    
    try:
        return service.send_message(
            phone_number=phone_number,
            message=message,
            content_type=content_type,
            object_id=object_id,
        )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {str(e)}")
        return None


def send_whatsapp_template(
    company_id,
    phone_number: str,
    template_name: str,
    parameters: Optional[List[Dict]] = None,
    branch_id: Optional[str] = None,
    content_type: Optional[str] = None,
    object_id: Optional[str] = None,
) -> Optional[WhatsAppMessage]:
    """
    Отправить сообщение по шаблону через WhatsApp.
    
    Args:
        company_id: ID компании
        phone_number: Номер телефона получателя
        template_name: Имя шаблона
        parameters: Параметры для заполнения в шаблон
        branch_id: ID филиала (опционально)
        content_type: Тип связанного объекта
        object_id: ID связанного объекта
        
    Returns:
        Объект WhatsAppMessage или None
        
    Example:
        msg = send_whatsapp_template(
            company_id="uuid",
            phone_number="+79991234567",
            template_name="order_confirmation",
            parameters=[
                {"type": "text", "text": "Order #123"},
                {"type": "text", "text": "5000 RUB"},
            ],
        )
    """
    service = get_whatsapp_service(company_id, branch_id)
    
    if not service:
        logger.warning(f"WhatsApp сервис не найден для компании {company_id}")
        return None
    
    try:
        return service.send_template_message(
            phone_number=phone_number,
            template_name=template_name,
            parameters=parameters,
            content_type=content_type,
            object_id=object_id,
        )
    except Exception as e:
        logger.error(f"Ошибка отправки шаблона: {str(e)}")
        return None


def get_contact_conversations(
    company_id,
    phone_number: str,
    limit: int = 50,
    branch_id: Optional[str] = None,
) -> List[WhatsAppMessage]:
    """
    Получить диалог с контактом.
    
    Args:
        company_id: ID компании
        phone_number: Номер телефона
        limit: Максимум сообщений
        branch_id: ID филиала (опционально)
        
    Returns:
        Список сообщений
        
    Example:
        messages = get_contact_conversations(
            company_id="uuid",
            phone_number="+79991234567",
            limit=20
        )
    """
    try:
        if branch_id:
            config = WhatsAppConfig.objects.get(
                company_id=company_id,
                branch_id=branch_id,
                is_active=True,
            )
        else:
            config = WhatsAppConfig.objects.filter(
                company_id=company_id,
                branch_id__isnull=True,
                is_active=True,
            ).first()
        
        if config:
            return list(
                config.messages.filter(phone_number=phone_number)
                .order_by("-created_at")
                [:limit]
            )
    except Exception as e:
        logger.error(f"Ошибка получения диалога: {str(e)}")
    
    return []
