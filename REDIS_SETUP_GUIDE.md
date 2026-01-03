# Руководство по настройке Redis кэширования на сервере

## 📋 Содержание

1. [Установка Redis](#установка-redis)
2. [Настройка Redis](#настройка-redis)
3. [Настройка Django](#настройка-django)
4. [Переменные окружения](#переменные-окружения)
5. [Проверка работоспособности](#проверка-работоспособности)
6. [Мониторинг и обслуживание](#мониторинг-и-обслуживание)
7. [Решение проблем](#решение-проблем)

---

## 🚀 Установка Redis

### Ubuntu/Debian

```bash
# Обновление пакетов
sudo apt update

# Установка Redis
sudo apt install redis-server -y

# Проверка версии
redis-server --version

# Запуск Redis
sudo systemctl start redis-server

# Автозапуск при загрузке
sudo systemctl enable redis-server

# Проверка статуса
sudo systemctl status redis-server
```

### CentOS/RHEL

```bash
# Установка EPEL репозитория
sudo yum install epel-release -y

# Установка Redis
sudo yum install redis -y

# Запуск Redis
sudo systemctl start redis

# Автозапуск
sudo systemctl enable redis

# Проверка статуса
sudo systemctl status redis
```

### Docker (рекомендуется для продакшена)

```bash
# Запуск Redis в Docker
docker run -d \
  --name redis-nurcrm \
  --restart unless-stopped \
  -p 6379:6379 \
  -v redis-data:/data \
  redis:7-alpine redis-server --appendonly yes

# Проверка
docker ps | grep redis
```

---

## ⚙️ Настройка Redis

### 1. Базовые настройки безопасности

Отредактируйте файл конфигурации Redis:

```bash
sudo nano /etc/redis/redis.conf
```

**Важные настройки для продакшена:**

```conf
# Привязка к localhost (безопаснее) или внутреннему IP
bind 127.0.0.1

# Пароль (обязательно для продакшена!)
requirepass ваш_надежный_пароль_redis

# Отключение опасных команд
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command CONFIG ""

# Максимальное использование памяти (например, 512MB)
maxmemory 512mb
maxmemory-policy allkeys-lru

# Логирование
loglevel notice
logfile /var/log/redis/redis-server.log

# Сохранение на диск (персистентность)
save 900 1
save 300 10
save 60 10000

# Директория для данных
dir /var/lib/redis
```

### 2. Создание пользователя для Redis (опционально, но рекомендуется)

```bash
# Создание системного пользователя
sudo useradd -r -s /bin/false redis
sudo chown -R redis:redis /var/lib/redis
sudo chown -R redis:redis /var/log/redis
```

### 3. Настройка firewall

```bash
# Если Redis на том же сервере - не открывать порт
# Если Redis на отдельном сервере - открыть только для внутренней сети

# UFW (Ubuntu)
sudo ufw allow from 10.0.0.0/8 to any port 6379
sudo ufw allow from 172.16.0.0/12 to any port 6379
sudo ufw allow from 192.168.0.0/16 to any port 6379

# Firewalld (CentOS)
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/8" port port="6379" protocol="tcp" accept'
sudo firewall-cmd --reload
```

### 4. Перезапуск Redis

```bash
sudo systemctl restart redis-server
# или
sudo systemctl restart redis
```

---

## 🐍 Настройка Django

### 1. Обновление settings.py

Убедитесь, что в `core/settings.py` есть правильная конфигурация:

```python
import os

# Кэширование (Redis)
REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/1')
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

# Если есть пароль
if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@127.0.0.1:6379/1"

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PARSER_CLASS': 'redis.connection.HiredisParser',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
            'IGNORE_EXCEPTIONS': True,  # Не падать, если Redis недоступен
        },
        'KEY_PREFIX': 'nurcrm',
        'TIMEOUT': 300,  # 5 минут по умолчанию
    }
}

# Время кэширования для разных типов данных (в секундах)
CACHE_TIMEOUT_SHORT = 60  # 1 минута
CACHE_TIMEOUT_MEDIUM = 300  # 5 минут
CACHE_TIMEOUT_LONG = 3600  # 1 час
CACHE_TIMEOUT_ANALYTICS = 600  # 10 минут
```

### 2. Разделение Redis баз данных

Рекомендуется использовать разные базы данных Redis для разных целей:

- **База 0**: Celery broker
- **База 1**: Django cache
- **База 2**: Channels (WebSocket)

```python
# В settings.py
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://127.0.0.1:6379/0')

CACHES = {
    'default': {
        'LOCATION': os.getenv('REDIS_CACHE_URL', 'redis://127.0.0.1:6379/1'),
        # ...
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("REDIS_CHANNELS_URL", "redis://127.0.0.1:6379/2")],
            # ...
        },
    }
}
```

---

## 🔐 Переменные окружения

### Создание .env файла

Создайте файл `.env` в корне проекта:

```bash
# .env
# Redis настройки
REDIS_URL=redis://127.0.0.1:6379/1
REDIS_PASSWORD=ваш_надежный_пароль

# Или с паролем в URL
REDIS_URL=redis://:ваш_надежный_пароль@127.0.0.1:6379/1

# Celery
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# Channels
REDIS_CHANNELS_URL=redis://127.0.0.1:6379/2
```

### Загрузка переменных окружения

Установите `python-decouple` или `django-environ`:

```bash
pip install python-decouple
```

Обновите `settings.py`:

```python
from decouple import config

REDIS_URL = config('REDIS_URL', default='redis://127.0.0.1:6379/1')
REDIS_PASSWORD = config('REDIS_PASSWORD', default=None)
```

### .env.example (шаблон)

Создайте `.env.example` для команды:

```bash
# .env.example
# Redis настройки
REDIS_URL=redis://127.0.0.1:6379/1
REDIS_PASSWORD=

# Celery
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# Channels
REDIS_CHANNELS_URL=redis://127.0.0.1:6379/2
```

**⚠️ ВАЖНО:** Добавьте `.env` в `.gitignore`!

---

## ✅ Проверка работоспособности

### 1. Проверка Redis

```bash
# Подключение к Redis
redis-cli

# Если есть пароль
redis-cli -a ваш_пароль

# Проверка ping
PING
# Должно вернуть: PONG

# Проверка информации
INFO server
INFO memory

# Выход
exit
```

### 2. Проверка из Django

Создайте management команду для тестирования:

```python
# apps/main/management/commands/test_cache.py
from django.core.management.base import BaseCommand
from django.core.cache import cache

class Command(BaseCommand):
    help = 'Тестирование Redis кэша'

    def handle(self, *args, **options):
        # Тест записи
        cache.set('test_key', 'test_value', 60)
        self.stdout.write(self.style.SUCCESS('✓ Запись в кэш успешна'))

        # Тест чтения
        value = cache.get('test_key')
        if value == 'test_value':
            self.stdout.write(self.style.SUCCESS('✓ Чтение из кэша успешно'))
        else:
            self.stdout.write(self.style.ERROR('✗ Ошибка чтения из кэша'))

        # Тест удаления
        cache.delete('test_key')
        value = cache.get('test_key')
        if value is None:
            self.stdout.write(self.style.SUCCESS('✓ Удаление из кэша успешно'))
        else:
            self.stdout.write(self.style.ERROR('✗ Ошибка удаления из кэша'))

        # Информация о кэше
        self.stdout.write(f'\nBackend: {cache.__class__.__name__}')
```

Запуск:

```bash
python manage.py test_cache
```

### 3. Проверка через Python shell

```bash
python manage.py shell
```

```python
from django.core.cache import cache

# Тест записи
cache.set('test', 'value', 60)

# Тест чтения
cache.get('test')

# Очистка тестового ключа
cache.delete('test')
```

---

## 📊 Мониторинг и обслуживание

### 1. Мониторинг Redis

```bash
# Статистика в реальном времени
redis-cli --stat

# Информация о памяти
redis-cli INFO memory

# Количество ключей
redis-cli DBSIZE

# Список всех ключей (осторожно на продакшене!)
redis-cli KEYS "*"

# Информация о клиентах
redis-cli CLIENT LIST
```

### 2. Очистка кэша

```bash
# Очистка текущей базы данных
redis-cli FLUSHDB

# Очистка всех баз данных
redis-cli FLUSHALL
```

Или через Django:

```python
from django.core.cache import cache
cache.clear()
```

### 3. Логирование

Проверьте логи Redis:

```bash
# Ubuntu/Debian
sudo tail -f /var/log/redis/redis-server.log

# CentOS
sudo tail -f /var/log/redis/redis.log
```

### 4. Настройка мониторинга (опционально)

Установите `redis-stat` для веб-мониторинга:

```bash
# Установка
sudo gem install redis-stat

# Запуск
redis-stat --server
# Откройте http://localhost:63790
```

---

## 🔧 Решение проблем

### Проблема: Redis не запускается

```bash
# Проверка логов
sudo journalctl -u redis-server -n 50

# Проверка конфигурации
sudo redis-server /etc/redis/redis.conf --test-memory 1

# Проверка порта
sudo netstat -tlnp | grep 6379
```

### Проблема: Django не может подключиться к Redis

1. Проверьте, что Redis запущен:
   ```bash
   sudo systemctl status redis-server
   ```

2. Проверьте подключение:
   ```bash
   redis-cli ping
   ```

3. Проверьте настройки в `.env` и `settings.py`

4. Проверьте firewall:
   ```bash
   sudo ufw status
   ```

### Проблема: Ошибка "Connection refused"

1. Проверьте `bind` в `redis.conf` - должен быть `127.0.0.1` или внутренний IP
2. Проверьте, что Redis слушает правильный порт:
   ```bash
   sudo netstat -tlnp | grep 6379
   ```

### Проблема: Недостаточно памяти

1. Увеличьте `maxmemory` в `redis.conf`
2. Настройте `maxmemory-policy` (например, `allkeys-lru`)
3. Мониторьте использование:
   ```bash
   redis-cli INFO memory
   ```

### Проблема: Медленная работа

1. Проверьте количество ключей:
   ```bash
   redis-cli DBSIZE
   ```

2. Проверьте медленные команды:
   ```bash
   redis-cli SLOWLOG GET 10
   ```

3. Оптимизируйте использование кэша (меньше записей, правильные таймауты)

---

## 🚀 Оптимизация для продакшена

### 1. Настройка персистентности

Для продакшена рекомендуется использовать AOF (Append Only File):

```conf
# В redis.conf
appendonly yes
appendfsync everysec
```

### 2. Репликация (для высокой доступности)

Настройте Redis Sentinel или репликацию для отказоустойчивости.

### 3. Мониторинг производительности

Используйте инструменты:
- **RedisInsight** - GUI для Redis
- **Grafana + Redis Exporter** - визуализация метрик
- **Sentry** - отслеживание ошибок

### 4. Резервное копирование

```bash
# Создание бэкапа
redis-cli BGSAVE

# Ручной бэкап
redis-cli SAVE

# Автоматический бэкап (настройте cron)
0 2 * * * redis-cli BGSAVE
```

---

## 📝 Чеклист настройки

- [ ] Redis установлен и запущен
- [ ] Redis настроен с паролем
- [ ] Firewall настроен правильно
- [ ] Django settings.py обновлен
- [ ] .env файл создан с правильными настройками
- [ ] Тест кэширования пройден успешно
- [ ] Мониторинг настроен
- [ ] Логи проверяются регулярно
- [ ] Резервное копирование настроено

---

## 📚 Полезные команды

```bash
# Перезапуск Redis
sudo systemctl restart redis-server

# Остановка Redis
sudo systemctl stop redis-server

# Просмотр логов
sudo tail -f /var/log/redis/redis-server.log

# Подключение к Redis CLI
redis-cli

# Проверка статуса
redis-cli ping

# Информация о сервере
redis-cli INFO

# Очистка кэша Django
python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

---

*Документ создан: 2025-01-27*
*Последнее обновление: 2025-01-27*

