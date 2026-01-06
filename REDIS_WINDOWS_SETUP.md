# Установка Redis на Windows

## 🪟 Варианты установки Redis на Windows

### Вариант 1: WSL2 (Рекомендуется) ⭐

WSL2 (Windows Subsystem for Linux) - лучший вариант для разработки.

#### 1. Установка WSL2

```powershell
# В PowerShell от администратора
wsl --install

# Перезагрузите компьютер после установки
```

#### 2. Установка Redis в WSL2

```bash
# Откройте WSL (Ubuntu)
sudo apt update
sudo apt install redis-server -y

# Запуск Redis
sudo service redis-server start

# Автозапуск
sudo service redis-server enable

# Проверка
redis-cli ping
# Должно вернуть: PONG
```

#### 3. Подключение из Windows

Redis в WSL2 будет доступен по адресу `127.0.0.1:6379` из Windows.

---

### Вариант 2: Memurai (Windows-версия Redis)

Memurai - это порт Redis для Windows.

#### 1. Скачать и установить

1. Скачайте Memurai: https://www.memurai.com/get-memurai
2. Установите как службу Windows
3. Сервис запустится автоматически

#### 2. Проверка

```powershell
# Проверка статуса службы
Get-Service Memurai

# Подключение
redis-cli ping
```

---

### Вариант 3: Docker Desktop

Если у вас установлен Docker Desktop:

```powershell
# Запуск Redis в Docker
docker run -d --name redis-nurcrm -p 6379:6379 redis:7-alpine

# Проверка
docker ps | findstr redis

# Подключение
docker exec -it redis-nurcrm redis-cli ping
```

---

### Вариант 4: Redis для Windows (неофициальный порт)

⚠️ **Не рекомендуется для продакшена**, только для разработки.

1. Скачайте: https://github.com/microsoftarchive/redis/releases
2. Распакуйте архив
3. Запустите `redis-server.exe`

---

## ✅ Проверка установки

После установки любого варианта проверьте:

```powershell
# Проверка подключения
redis-cli ping
# Должно вернуть: PONG

# Или через Python
python -c "import redis; r = redis.Redis(); print(r.ping())"
```

---

## 🔧 Настройка для вашего проекта

После установки Redis, настройки в `settings.py` уже правильные:

```python
REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/1')
```

Просто убедитесь, что Redis запущен и проверьте:

```powershell
python manage.py test_cache
```

---

## 🚀 Быстрый старт (WSL2)

Если у вас уже установлен WSL2:

```bash
# В WSL терминале
sudo apt update
sudo apt install redis-server -y
sudo service redis-server start

# Проверка
redis-cli ping
```

Затем в PowerShell Windows:

```powershell
python manage.py test_cache
```

---

## ❓ Решение проблем

### Проблема: "redis-cli: command not found"

**Решение для WSL2:**
```bash
sudo apt install redis-tools -y
```

**Решение для Windows:**
- Установите Redis через один из вариантов выше
- Или используйте Docker

### Проблема: "Connection refused"

1. Проверьте, что Redis запущен:
   ```powershell
   # WSL2
   wsl sudo service redis-server status
   
   # Docker
   docker ps | findstr redis
   
   # Memurai
   Get-Service Memurai
   ```

2. Проверьте порт:
   ```powershell
   netstat -an | findstr 6379
   ```

### Проблема: Redis не запускается автоматически

**WSL2:**
```bash
# Добавьте в ~/.bashrc
sudo service redis-server start
```

**Windows Service (Memurai):**
- Установите как службу при установке
- Или: `sc config Memurai start= auto`

---

## 📝 Рекомендации

1. **Для разработки:** Используйте WSL2 или Docker
2. **Для продакшена:** Используйте Linux сервер с Redis
3. **Для тестирования:** Docker - самый простой вариант

---

*Документ создан: 2025-01-27*

