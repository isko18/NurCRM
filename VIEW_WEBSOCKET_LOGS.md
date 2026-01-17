# Как просматривать логи WebSocket на продакшене

## Добавлено логирование в:
- `apps/cafe/consumers.py` - все методы WebSocket consumer'ов

## Что логируется:
- ✅ Подключения/отключения клиентов
- ✅ Получение событий от channel layer (order_created, table_status_changed и т.д.)
- ✅ Отправка уведомлений клиентам
- ✅ Ошибки при обработке событий

## Как смотреть логи:

### 1. Логи Gunicorn (ASGI сервер):
```bash
# В реальном времени
journalctl -u gunicorn -f

# Последние 100 строк
journalctl -u gunicorn -n 100

# С фильтром по WebSocket
journalctl -u gunicorn -f | grep -E "(CafeOrderConsumer|CafeTableConsumer|WebSocket)"
```

### 2. Если используется supervisor:
```bash
# Последние логи
sudo supervisorctl tail -f gunicorn

# Логи ошибок
sudo supervisorctl tail -f gunicorn stderr
```

### 3. Если логи пишутся в файл:
```bash
# Смотреть логи в реальном времени
tail -f /var/log/gunicorn/error.log | grep -E "(CafeOrderConsumer|CafeTableConsumer)"

# Или все логи
tail -f /var/log/gunicorn/error.log
```

### 4. Через Django logging (если настроено):
```bash
# Если логи Django в файле
tail -f /path/to/django.log | grep -E "(CafeOrderConsumer|CafeTableConsumer)"
```

## Примеры логов:

### Успешное подключение:
```
[CafeTableConsumer] Connected: user=xxx, company=xxx, branch=xxx, group=cafe_tables_xxx
```

### Получение события:
```
[CafeTableConsumer] Received table_status_changed event: table_id=xxx, status=BUSY, group=cafe_tables_xxx
```

### Отправка уведомления:
```
[CafeTableConsumer] Sent table_status_changed notification to client
```

### Ошибка:
```
[CafeTableConsumer] Error in table_status_changed: [error message]
```

## После перезапуска сервера:

```bash
# Перезапустить gunicorn
sudo systemctl restart gunicorn
# или
sudo supervisorctl restart gunicorn

# Затем смотреть логи
journalctl -u gunicorn -f
```

## Диагностика проблем:

1. **Если событие получено, но не отправлено клиенту:**
   - Ищите ошибки после `Received ... event`
   - Проверьте, что есть `Sent ... notification to client`

2. **Если события не приходят в consumer:**
   - Проверьте, что сообщения отправляются в правильную группу
   - Убедитесь, что group_name совпадает

3. **Если ошибки при подключении:**
   - Смотрите логи при подключении WebSocket
   - Проверьте JWT токен и права пользователя
