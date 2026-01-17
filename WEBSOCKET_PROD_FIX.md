# Проверка WebSocket на продакшене

## Проблема
WebSocket не работает на продакшене (app.nurcrm.kg)

## Проверено

### ✅ Что работает:
1. **Nginx конфигурация** - правильно настроена для WebSocket
   - `$connection_upgrade` определена через map
   - Location `/ws/` настроен с правильными заголовками
   - Upstream `django_asgi` настроен на unix socket

2. **ASGI сервер** - запущен (gunicorn с uvicorn workers)
   - Socket файл `/home/nur/asgi.sock` существует
   - 3 воркера запущены

3. **Redis** - работает (ping успешен)
   - Channel Layer подключен
   - Группы WebSocket создаются в Redis

4. **Django настройки** - правильные
   - `ALLOWED_HOSTS = ["*"]`
   - `ASGI_APPLICATION = "core.asgi.application"`
   - `CHANNEL_LAYERS` настроены

### ⚠️ Потенциальные проблемы:

1. **WebSocket подключения могут не доходить до consumer**
   - Проверьте логи gunicorn: `journalctl -u gunicorn -f`
   - Проверьте логи nginx: `tail -f /var/log/nginx/error.log`

2. **Сообщения могут теряться между channel layer и consumer**
   - Проверьте, что все воркеры gunicorn подключены к одному Redis
   - Проверьте, что воркеры не перезапускаются при каждом запросе

3. **Проблемы с CORS или безопасностью**
   - Убедитесь, что фронтенд использует правильный URL: `wss://app.nurcrm.kg/ws/...`
   - Проверьте, что JWT токен валиден и не истек

## Диагностика

### 1. Проверка подключения WebSocket:
```bash
# В браузере DevTools:
const ws = new WebSocket('wss://app.nurcrm.kg/ws/cafe/tables/?token=YOUR_JWT_TOKEN');
ws.onopen = () => console.log('✅ Connected');
ws.onmessage = (e) => console.log('📨 Message:', JSON.parse(e.data));
ws.onerror = (e) => console.error('❌ Error:', e);
```

### 2. Проверка отправки уведомлений:
```bash
# На сервере:
python manage.py shell -c "
from apps.cafe.models import Table
from apps.users.models import Company
import uuid

company = Company.objects.get(id=uuid.UUID('f966441f-0938-49ed-97a7-07a503655ebc'))
table = Table.objects.filter(company=company).first()
from apps.cafe.views import send_table_status_changed_notification
send_table_status_changed_notification(table)
print('✅ Notification sent')
"
```

### 3. Проверка Redis групп:
```bash
redis-cli
> KEYS "asgi:group:cafe_*"
> SUBSCRIBE asgi:group:cafe_tables_f966441f-0938-49ed-97a7-07a503655ebc
```

### 4. Проверка логов:
```bash
# Gunicorn логи
journalctl -u gunicorn -f

# Nginx логи
tail -f /var/log/nginx/error.log
tail -f /var/log/nginx/access.log | grep ws

# Django логи (если настроены)
tail -f /path/to/django.log
```

## Решение

Если WebSocket подключение работает (получено `connection_established`), но уведомления не приходят:

1. **Проверьте, что consumer получает события:**
   - Добавьте логирование в `apps/cafe/consumers.py`:
   ```python
   async def table_status_changed(self, event):
       import logging
       logger = logging.getLogger(__name__)
       logger.info(f"Received event: {event}")
       payload = event.get("payload", {})
       await self.send(json.dumps({
           "type": "table_status_changed",
           "data": payload
       }))
   ```

2. **Проверьте, что сообщения отправляются в правильную группу:**
   - Убедитесь, что имя группы совпадает между sender и consumer
   - Проверьте `branch_id` - если он указан, он должен совпадать

3. **Перезапустите gunicorn после изменений:**
   ```bash
   sudo systemctl restart gunicorn
   # или
   sudo supervisorctl restart gunicorn
   ```

## Тестирование

После исправлений проверьте:
1. ✅ WebSocket подключается и получает `connection_established`
2. ✅ Ping/Pong работает (`{"action": "ping"}` → `{"type": "pong"}`)
3. ✅ Уведомления приходят при создании/изменении заказов
4. ✅ Уведомления приходят при изменении статуса столов
