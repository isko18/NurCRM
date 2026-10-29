Документация по онлайн-записям (Barber App)
Публичные эндпоинты (без авторизации)
Все публичные эндпоинты требуют company_slug в URL для идентификации компании.
1. Получить список услуг
GET /api/barber/public/{company_slug}/services/
Query параметры:
branch (опционально) — UUID филиала
Ответ:
[  {    "id": "uuid",    "name": "Мужская стрижка",    "time": "30",    "price": "500.00",    "category": "uuid",    "category_name": "Стрижки"  }]
2. Получить категории с услугами
GET /api/barber/public/{company_slug}/service-categories/
Query параметры:
branch (опционально) — UUID филиала
Ответ:
[  {    "id": "uuid",    "name": "Стрижки",    "services": [      {        "id": "uuid",        "name": "Мужская стрижка",        "time": "30",        "price": "500.00",        "category": "uuid",        "category_name": "Стрижки"      }    ]  }]
3. Получить список мастеров
GET /api/barber/public/{company_slug}/masters/
Query параметры:
branch (опционально) — UUID филиала
Ответ:
[  {    "id": "uuid",    "first_name": "Иван",    "last_name": "Иванов",    "full_name": "Иван Иванов",    "avatar": "https://example.com/avatar.jpg",    "phone_number": "+7999123456"  }]
4. Получить доступность всех мастеров на дату
GET /api/barber/public/{company_slug}/masters/availability/
Query параметры:
date (обязательно) — дата в формате YYYY-MM-DD
branch (опционально) — UUID филиала
Ответ:
{  "date": "2026-01-22",  "masters": [    {      "master_id": "uuid",      "master_name": "Иван Иванов",      "avatar": "https://example.com/avatar.jpg",      "date": "2026-01-22",      "busy_slots": [        {          "id": "uuid",          "start_at": "2026-01-22T10:00:00+06:00",          "end_at": "2026-01-22T11:00:00+06:00"        },        {          "id": "uuid",          "start_at": "2026-01-22T14:00:00+06:00",          "end_at": "2026-01-22T15:30:00+06:00"        }      ],      "work_start": "09:00",      "work_end": "21:00"    }  ]}
5. Получить расписание конкретного мастера
GET /api/barber/public/{company_slug}/masters/{master_id}/schedule/
Query параметры:
date (обязательно) — дата начала в формате YYYY-MM-DD
days (опционально) — количество дней (по умолчанию 1, максимум 14)
Ответ:
{  "master_id": "uuid",  "master_name": "Иван Иванов",  "date_from": "2026-01-22",  "date_to": "2026-01-28",  "busy_slots": [    {      "id": "uuid",      "start_at": "2026-01-22T10:00:00+06:00",      "end_at": "2026-01-22T11:00:00+06:00"    }  ],  "work_start": "09:00",  "work_end": "21:00"}
6. Создать онлайн-заявку на запись
POST /api/barber/public/{company_slug}/bookings/
Тело запроса:
{  "services": [    {      "service_id": "uuid",      "title": "Мужская стрижка",      "price": 500,      "duration_min": 30    }  ],  "master_id": "uuid",  "master_name": "Иван Иванов",  "date": "2026-01-22",  "time_start": "10:00:00",  "time_end": "10:30:00",  "client_name": "Петр Петров",  "client_phone": "+79991234567",  "client_comment": "Хочу короткую стрижку",  "payment_method": "cash"}
Поля:
Поле	Тип	Обязательно	Описание
services	array	Да	Массив услуг
services[].service_id	uuid	Да	ID услуги
services[].title	string	Да	Название услуги
services[].price	number	Да	Цена услуги
services[].duration_min	number	Да	Длительность в минутах
master_id	uuid	Нет	ID мастера
master_name	string	Нет	Имя мастера
date	date	Да	Дата записи (YYYY-MM-DD)
time_start	time	Да	Время начала (HH:MM:SS)
time_end	time	Да	Время окончания (HH:MM:SS)
client_name	string	Да	Имя клиента
client_phone	string	Да	Телефон клиента
client_comment	string	Нет	Комментарий клиента
payment_method	string	Нет	Способ оплаты: cash, card, online
Ответ (201 Created):
{  "id": "uuid",  "status": "new",  "services": [...],  "master_id": "uuid",  "master_name": "Иван Иванов",  "date": "2026-01-22",  "time_start": "10:00:00",  "time_end": "10:30:00",  "client_name": "Петр Петров",  "client_phone": "+79991234567",  "total_price": "500.00",  "total_duration_min": 30}
Защищенные эндпоинты (требуют авторизации)
7. Список заявок
GET /api/barber/bookings/
Query параметры для фильтрации:
status — фильтр по статусу (new, confirmed, no_show, spam)
date — фильтр по дате
payment_method — фильтр по способу оплаты
branch — UUID филиала
search — поиск по имени/телефону клиента
Сортировка:
ordering=-created_at (по умолчанию — новые сверху)
ordering=date,time_start
8. Детали заявки
GET /api/barber/bookings/{id}/
9. Изменить статус заявки
PATCH /api/barber/bookings/{id}/status/
Тело запроса:
{  "status": "confirmed"}
Доступные статусы:
Статус	Описание
new	Новая заявка
confirmed	Подтверждена
no_show	Клиент не пришёл
spam	Спам
Статусы заявок (OnlineBooking.Status)
Значение	Описание
new	Новая заявка (по умолчанию)
confirmed	Подтверждена администратором
no_show	Клиент не пришёл
spam	Помечена как спам
Способы оплаты (PaymentMethod)
Значение	Описание
cash	Наличные (по умолчанию)
card	Карта
online	Онлайн оплата