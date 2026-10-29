# Примеры интеграции WhatsApp в Construction

Этот документ содержит конкретные примеры как интегрировать WhatsApp в приложение construction.

## 1. Отправка уведомлений при создании CashShift

Когда открывается смена, отправить подтверждение менеджеру.

### Вариант 1: Через signals

**Файл:** `apps/construction/signals.py`

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import CashShift
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@receiver(post_save, sender=CashShift)
def notify_shift_opened(sender, instance, created, **kwargs):
    """Отправить уведомление при открытии смены."""
    if created and instance.employee and instance.employee.phone_number:
        message_text = f"✅ Смена открыта\\n" \
                       f"Касса: {instance.cashbox.name}\\n" \
                       f"Время: {instance.opened_at.strftime('%H:%M')}"
        
        send_whatsapp_message(
            company_id=instance.cashbox.company_id,
            branch_id=instance.cashbox.branch_id,
            phone_number=instance.employee.phone_number,
            message=message_text,
            content_type="CashShift",
            object_id=instance.id,
        )
```

### Вариант 2: Через Celery задачу

**Файл:** `apps/construction/tasks.py`

```python
from celery import shared_task
from .models import CashShift
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@shared_task
def notify_shift_status(shift_id):
    """Отправить статус смены."""
    try:
        shift = CashShift.objects.get(id=shift_id)
        
        if shift.closed_at:
            message_text = f"✅ Смена закрыта\\n" \
                           f"Касса: {shift.cashbox.name}\\n" \
                           f"Сумма: {shift.total_amount} RUB"
        else:
            message_text = f"📊 Статус смены\\n" \
                           f"Касса: {shift.cashbox.name}\\n" \
                           f"Текущая сумма: {shift.current_amount} RUB"
        
        send_whatsapp_message(
            company_id=shift.cashbox.company_id,
            branch_id=shift.cashbox.branch_id,
            phone_number=shift.manager.phone_number,
            message=message_text,
            content_type="CashShift",
            object_id=shift.id,
        )
    except Exception as e:
        print(f"Ошибка отправки уведомления смены: {e}")
```

## 2. Отправка квитанций при движении по кассе

Когда фиксируется расход или доход, отправить квитанцию.

### Реализация

**Файл:** `apps/construction/integrations/whatsapp/handlers.py`

```python
"""
Обработчики для отправки сообщений при различных событиях в construction.
"""
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

def send_cashflow_receipt(cashflow):
    """
    Отправить квитанцию о движении по кассе.
    
    Args:
        cashflow: Объект CashFlow
    """
    status_emoji = "➕" if cashflow.is_income else "➖"
    amount_str = f"+{cashflow.amount}" if cashflow.is_income else f"-{cashflow.amount}"
    
    message_text = f"{status_emoji} Квитанция\\n" \
                   f"Сумма: {amount_str} RUB\\n" \
                   f"Категория: {cashflow.category.title}\\n" \
                   f"Касса: {cashflow.cashbox.name}\\n" \
                   f"Баланс: {cashflow.cashbox.balance} RUB"
    
    if cashflow.notes:
        message_text += f"\\nПримечание: {cashflow.notes}"
    
    send_whatsapp_message(
        company_id=cashflow.cashbox.company_id,
        branch_id=cashflow.cashbox.branch_id,
        phone_number=cashflow.created_by.phone_number,
        message=message_text,
        content_type="CashFlow",
        object_id=cashflow.id,
    )


def send_daily_report(cashbox, report_data):
    \"\"\"
    Отправить дневной отчет по кассе.
    
    Args:
        cashbox: Объект Cashbox
        report_data: Dict с данными отчета
    \"\"\"
    message_text = f"📋 Дневной отчет\\n" \
                   f"Касса: {cashbox.name}\\n" \
                   f"Приход: {report_data['income']} RUB\\n" \
                   f"Расход: {report_data['outcome']} RUB\\n" \
                   f"Баланс: {report_data['balance']} RUB\\n" \
                   f"Операций: {report_data['transaction_count']}"
    
    send_whatsapp_message(
        company_id=cashbox.company_id,
        branch_id=cashbox.branch_id,
        phone_number=cashbox.manager.phone_number,
        message=message_text,
        content_type="Cashbox",
        object_id=cashbox.id,
    )
```

**Использование в models.py:**

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import CashFlow
from .integrations.whatsapp.handlers import send_cashflow_receipt

@receiver(post_save, sender=CashFlow)
def notify_cashflow(sender, instance, created, **kwargs):
    if created:
        send_cashflow_receipt(instance)
```

## 3. Интеграция с воронкой продаж

### Отправка уведомлений при смене статуса лида

```python
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message, send_whatsapp_template

def notify_lead_status_change(lead, old_status, new_status):
    \"\"\"Отправить уведомление об изменении статуса лида.\"\"\"
    
    status_messages = {
        "new": "🆕 Новый лид зарегистрирован",
        "contacted": "📞 Мы связались с вами",
        "interested": "👍 Вы заинтересованы",
        "proposal": "📊 Отправлено коммерческое предложение",
        "negotiation": "🤝 Идут переговоры",
        "won": "🎉 Сделка заключена",
        "lost": "❌ Сделка не состоялась",
    }
    
    message = status_messages.get(new_status, "Статус обновлен")
    message += f"\\nЛид: {lead.name}\\nКомпания: {lead.company_name}"
    
    send_whatsapp_message(
        company_id=lead.company_id,
        phone_number=lead.phone_number,
        message=message,
        content_type="Lead",
        object_id=lead.id,
    )
```

## 4. Отправка шаблонных сообщений

### Создание и отправка шаблонов

Сначала создайте шаблоны в WhatsApp Business Platform:

- `construction_welcome` - Приветствие нового контакта
- `order_confirmation` - Подтверждение заказа
- `delivery_notification` - Уведомление о доставке
- `payment_receipt` - Квитанция об оплате

**Использование:**

```python
from apps.construction.integrations.whatsapp.utils import send_whatsapp_template

# Отправить приветствие
send_whatsapp_template(
    company_id=company_id,
    phone_number="+79991234567",
    template_name="construction_welcome",
    parameters=[
        {"type": "text", "text": "Иван"},  # {{1}} - имя
    ],
)

# Отправить подтверждение заказа
send_whatsapp_template(
    company_id=company_id,
    phone_number="+79991234567",
    template_name="order_confirmation",
    parameters=[
        {"type": "text", "text": "12345"},  # {{1}} - номер заказа
        {"type": "text", "text": "15000"},  # {{2}} - сумма
        {"type": "text", "text": "2024-07-20"},  # {{3}} - дата доставки
    ],
)
```

## 5. Запрос на обратный звонок через WhatsApp

### Создание API endpoint

**Файл:** `apps/construction/views.py`

```python
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@api_view(["POST"])
@permission_classes([AllowAny])
def request_callback(request):
    \"\"\"
    Запрос на обратный звонок через WhatsApp.
    
    Body:
    {
        "company_id": "uuid",
        "phone_number": "+79991234567",
        "name": "Иван",
        "service": "Консультация"
    }
    \"\"\"
    try:
        data = request.data
        
        # Отправляем клиенту подтверждение
        send_whatsapp_message(
            company_id=data["company_id"],
            phone_number=data["phone_number"],
            message=f"✅ Спасибо, {data['name']}!\\n"
                   f"Мы получили вашу заявку на {data['service']}.\\n"
                   f"Мы свяжемся с вами в ближайшее время.",
            content_type="CallbackRequest",
            object_id=None,
        )
        
        # Уведомляем менеджера
        send_whatsapp_message(
            company_id=data["company_id"],
            phone_number="+79991234567",  # Номер менеджера
            message=f"📞 Новая заявка на обратный звонок\\n"
                   f"Имя: {data['name']}\\n"
                   f"Услуга: {data['service']}\\n"
                   f"Телефон: {data['phone_number']}",
        )
        
        return Response(
            {"status": "Заявка отправлена"},
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )
```

## 6. Массовая рассылка сообщений

### Создание Celery задачи

**Файл:** `apps/construction/tasks.py`

```python
from celery import shared_task
from .models import Contact
from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

@shared_task
def send_bulk_message(company_id, branch_id, message_text, filter_params=None):
    \"\"\"
    Отправить сообщение группе контактов.
    
    Args:
        company_id: ID компании
        branch_id: ID филиала
        message_text: Текст сообщения
        filter_params: Dict с параметрами фильтра контактов
    \"\"\"
    filter_params = filter_params or {}
    
    # Получаем контакты
    contacts = Contact.objects.filter(
        company_id=company_id,
        branch_id=branch_id,
        **filter_params
    )
    
    sent_count = 0
    failed_count = 0
    
    for contact in contacts:
        try:
            send_whatsapp_message(
                company_id=company_id,
                branch_id=branch_id,
                phone_number=contact.phone_number,
                message=message_text,
                content_type="Contact",
                object_id=contact.id,
            )
            sent_count += 1
        except Exception as e:
            print(f"Ошибка отправки контакту {contact.id}: {e}")
            failed_count += 1
    
    return {
        "sent": sent_count,
        "failed": failed_count,
        "total": sent_count + failed_count,
    }


# Использование
from .tasks import send_bulk_message

send_bulk_message.delay(
    company_id="company-uuid",
    branch_id="branch-uuid",
    message_text="Специальное предложение только для вас!",
    filter_params={"is_vip": True},
)
```

## 7. Интеграция с CRM контактами

### Синхронизация контактов

```python
from apps.construction.integrations.whatsapp.models import WhatsAppContact
from apps.crm.models import Contact

def sync_contacts(config):
    \"\"\"
    Синхронизировать контакты между CRM и WhatsApp.
    
    Args:
        config: WhatsAppConfig объект
    \"\"\"
    # Получаем CRM контакты
    crm_contacts = Contact.objects.filter(
        company_id=config.company_id,
        branch_id=config.branch_id,
    ).exclude(phone_number__isnull=True)
    
    for crm_contact in crm_contacts:
        WhatsAppContact.objects.get_or_create(
            config=config,
            phone_number=crm_contact.phone_number,
            defaults={
                "name": crm_contact.name,
                "content_type": "Contact",
                "object_id": crm_contact.id,
            }
        )
```

## 8. Аналитика и отчеты

### Получение статистики

```python
from django.db.models import Count, Q
from apps.construction.integrations.whatsapp.models import WhatsAppMessage, WhatsAppConfig

def get_whatsapp_stats(company_id, branch_id=None):
    \"\"\"Получить статистику по WhatsApp.\"\"\"
    
    query = WhatsAppConfig.objects.filter(company_id=company_id)
    if branch_id:
        query = query.filter(branch_id=branch_id)
    
    configs = query.values_list("id", flat=True)
    
    messages = WhatsAppMessage.objects.filter(config_id__in=configs)
    
    stats = {
        "total_messages": messages.count(),
        "inbound": messages.filter(direction="inbound").count(),
        "outbound": messages.filter(direction="outbound").count(),
        "delivered": messages.filter(status="delivered").count(),
        "read": messages.filter(status="read").count(),
        "failed": messages.filter(status="failed").count(),
        "unique_contacts": messages.values("phone_number").distinct().count(),
    }
    
    return stats
```

## Следующие шаги

1. ✅ Установить приложение
2. ✅ Настроить конфигурацию WhatsApp
3. ✅ Выбрать из примеров подходящие для вашего бизнеса
4. ✅ Интегрировать в существующие модели/views
5. ✅ Протестировать отправку сообщений
6. ✅ Настроить вебхуки
7. ✅ Мониторить логи и ошибки

## Тестирование

### Отправить тестовое сообщение

```bash
# Через Django shell
python manage.py shell

from apps.construction.integrations.whatsapp.utils import send_whatsapp_message

msg = send_whatsapp_message(
    company_id="ваша-company-uuid",
    phone_number="+79991234567",  # Ваш номер
    message="Тестовое сообщение!"
)

print(f"Статус: {msg.status}")
print(f"ID: {msg.whatsapp_message_id}")
```

---

**Документ обновлен:** 14 июля 2024
