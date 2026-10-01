# WhatsApp Integration - Контрольный список и Next Steps

## ✅ Завершено

### Структура и файлы
- [x] Создана папка `apps/construction/integrations/whatsapp/`
- [x] Реализованы моделиданных (WhatsAppConfig, WhatsAppMessage, WhatsAppContact)
- [x] Разработан сервис для работы с WhatsApp API (WhatsAppService, WhatsAppAPIClient)
- [x] Реализованы REST API views и serializers
- [x] Настроены URL роуты
- [x] Добавлена Django Admin интерфейс
- [x] Созданы утилиты для использования в других местах (utils.py)
- [x] Подготовлена полная документация

### Документация
- [x] WHATSAPP_INTEGRATION.md - полное руководство с примерами
- [x] WHATSAPP_QUICKSTART.md - быстрая установка за 5 минут
- [x] WHATSAPP_CONSTRUCTION_EXAMPLES.md - примеры для construction app
- [x] WHATSAPP_ARCHITECTURE.md - техническое описание архитектуры
- [x] Этот файл - контрольный список

## 🚀 Следующие шаги для вас

### Фаза 1: Подготовка (день 1)

- [ ] Получить credentials от Meta Business Platform
  - [ ] Phone Number ID
  - [ ] Business Account ID  
  - [ ] Access Token
  - [ ] Phone Number (+7XXXXXXXXXX)

- [ ] Выполнить миграции
  ```bash
  cd /home/fondante/descktop/NurCRM/NurCRM
  python manage.py migrate
  ```

- [ ] Создать первую конфигурацию через Django Admin
  - [ ] Откройте `/admin/construction/whatsappconfig/add/`
  - [ ] Заполните credentials
  - [ ] Сохраните

### Фаза 2: Тестирование (день 1-2)

- [ ] Проверить простую отправку сообщения
  ```bash
  python manage.py shell
  # Выполнить код из WHATSAPP_QUICKSTART.md
  ```

- [ ] Настроить webhook в Meta Business Platform
  - [ ] Скопировать webhook URL из Django Admin
  - [ ] Вставить в Settings → Webhook
  - [ ] Указать Verify Token

- [ ] Протестировать получение входящих сообщений
  - [ ] Отправить сообщение на номер из конфигурации
  - [ ] Проверить что сообщение появилось в БД

### Фаза 3: Интеграция с construction (день 2-3)

#### Выберите что интегрировать:

**Вариант 1: Уведомления при CashShift**
- [ ] Добавить сигнал в `apps/construction/signals.py`
- [ ] Протестировать отправку уведомлений
- [ ] Документировать процесс

**Вариант 2: Квитанции при CashFlow**
- [ ] Создать функцию `send_cashflow_receipt()` в handlers.py
- [ ] Привязать к модели CashFlow
- [ ] Настроить текст квитанций

**Вариант 3: Воронка продаж (Leads)**
- [ ] Интегрировать с моделью Lead
- [ ] Отправлять уведомления при смене статуса
- [ ] Запрос на обратный звонок через WhatsApp

**Вариант 4: Все сразу**
- [ ] Создать handlers.py с несколькими функциями
- [ ] Подключить все через signals
- [ ] Обширное тестирование

### Фаза 4: Оптимизация (день 3+)

- [ ] Настроить Celery для массовых рассылок
- [ ] Добавить retry логику для неудачных сообщений
- [ ] Создать кастомные команды (management commands)
- [ ] Настроить логирование и мониторинг
- [ ] Добавить тесты

## 📋 Контрольный список для продакшена

- [ ] Access Token хранится в `.env` файле, не в git
- [ ] Webhook URL настроен как HTTPS
- [ ] Все сообщения логируются
- [ ] Есть обработка ошибок для сетевых проблем
- [ ] Настроена ротация логов
- [ ] Есть мониторинг failed сообщений
- [ ] Пользователи могут управлять конфигурацией через админ
- [ ] REST API защищена аутентификацией
- [ ] Есть rate limiting на API
- [ ] Резервная копия данных регулярно создается

## 📚 Документы для чтения

**В порядке приоритета:**

1. **Начните здесь:**
   - [WHATSAPP_QUICKSTART.md](./WHATSAPP_QUICKSTART.md) (15 мин)
   - [WHATSAPP_CONSTRUCTION_EXAMPLES.md](./WHATSAPP_CONSTRUCTION_EXAMPLES.md) (30 мин)

2. **Справочники:**
   - [WHATSAPP_INTEGRATION.md](./WHATSAPP_INTEGRATION.md) (60 мин)
   - [WHATSAPP_ARCHITECTURE.md](./WHATSAPP_ARCHITECTURE.md) (45 мин)

## 🔧 Файлы для редактирования

Когда будете готовы интегрировать, отредактируйте:

```
apps/construction/
├── signals.py          # ← Добавьте @receiver'ы
├── tasks.py           # ← Добавьте Celery задачи
├── views.py           # ← Интегрируйте в API views
├── models.py          # ← Если нужны изменения
└── integrations/whatsapp/
    └── handlers.py    # ← Добавьте свои обработчики
```

## 💡 Советы

### Для быстрого старта
```python
# Просто используйте утилиты
from apps.construction.integrations.whatsapp.utils import (
    send_whatsapp_message,
    send_whatsapp_template,
    get_contact_conversations,
)

# Затем вызывайте их где нужно
send_whatsapp_message(company_id, phone, "text")
```

### Для сложной логики
```python
# Используйте сервис напрямую
from apps.construction.integrations.whatsapp.models import WhatsAppConfig
from apps.construction.integrations.whatsapp.services import WhatsAppService

config = WhatsAppConfig.objects.get(...)
service = WhatsAppService(config)
message = service.send_message(...)
```

### Для автоматизации
```python
# Используйте signals или Celery
from django.db.models.signals import post_save

@receiver(post_save, sender=YourModel)
def auto_send(sender, instance, created, **kwargs):
    if created:
        send_whatsapp_message(...)
```

## 🐛 Распространённые ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| 401 Unauthorized | Неверный Access Token | Обновите token в Django Admin |
| Invalid To number | Неверный формат номера | Используйте формат +7XXXXXXXXXX |
| Webhook not working | Неверный URL или token | Проверьте Settings → Webhook |
| No messages received | Webhook не подписан на события | Subscribe на `messages` event |
| "Config not found" | Конфигурация неактивна | Проверьте `is_active` флаг |

## 📞 Где получить помощь

1. **Изучить логи:**
   ```bash
   python manage.py shell
   from apps.construction.integrations.whatsapp.models import WhatsAppMessage
   WhatsAppMessage.objects.latest('created_at')
   ```

2. **Проверить конфигурацию:**
   - Откройте Django Admin
   - Перейдите в WhatsApp Интеграция → Конфигурации
   - Проверьте все поля заполнены

3. **Посмотреть примеры:**
   - [WHATSAPP_CONSTRUCTION_EXAMPLES.md](./WHATSAPP_CONSTRUCTION_EXAMPLES.md)
   - Скопируйте подходящий пример

4. **Обратиться к разработчику**
   - Приложите скриншоты ошибок
   - Опишите что пытались сделать
   - Отправьте логи приложения

## 🎯 Рекомендуемый порядок работы

**День 1:**
- ✅ Установить приложение (migrations)
- ✅ Создать первую конфигурацию
- ✅ Отправить тестовое сообщение

**День 2:**
- ✅ Настроить webhook
- ✅ Получить входящее сообщение
- ✅ Выбрать что интегрировать с construction

**День 3+:**
- ✅ Интегрировать в signals/tasks
- ✅ Написать свои handlers
- ✅ Тестирование и оптимизация

## 📊 Мониторинг

После запуска регулярно проверяйте:

```python
# WhatsApp Admin → Stats
from apps.construction.integrations.whatsapp.models import (
    WhatsAppMessage, WhatsAppConfig
)

# Сообщения за сегодня
today = datetime.date.today()
msgs = WhatsAppMessage.objects.filter(created_at__date=today)

print(f"Отправлено: {msgs.filter(direction='outbound').count()}")
print(f"Получено: {msgs.filter(direction='inbound').count()}")
print(f"Ошибок: {msgs.filter(status='failed').count()}")

# Последний webhook
config = WhatsAppConfig.objects.first()
print(f"Последний webhook: {config.last_webhook_received}")
```

## 🎉 Вы готовы!

Приложение полностью настроено и готово к использованию.

**Начните с:**
1. Прочитайте WHATSAPP_QUICKSTART.md (15 мин)
2. Создайте конфигурацию через Django Admin
3. Отправьте первое сообщение
4. Выберите что интегрировать с construction
5. Начните писать код!

**Успехов! 🚀**

---

**Архив файлов:**
- `/docs/WHATSAPP_INTEGRATION.md` - полное руководство
- `/docs/WHATSAPP_QUICKSTART.md` - быстрый старт
- `/docs/WHATSAPP_CONSTRUCTION_EXAMPLES.md` - примеры
- `/docs/WHATSAPP_ARCHITECTURE.md` - архитектура
- `/docs/WHATSAPP_CHECKLIST.md` - этот файл

**Дата создания:** 14 июля 2024  
**Версия интеграции:** 1.0.0  
**Статус:** ✅ Готово к использованию
