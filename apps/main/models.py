from django.db import models
import uuid
from apps.users.models import User  # Убедитесь, что путь к модели User указан корректно


class Contact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID')
    name = models.CharField(max_length=128, verbose_name='Имя')
    email = models.EmailField(verbose_name='Email')
    phone = models.CharField(max_length=32, verbose_name='Телефон')
    address = models.CharField(max_length=256, verbose_name='Адрес')
    company = models.CharField(max_length=128, verbose_name='Компания')
    notes = models.TextField(blank=True, null=True, verbose_name='Заметки')
    department = models.CharField(max_length=64, blank=True, null=True, verbose_name='Отдел')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contacts', verbose_name='Владелец')

    class Meta:
        verbose_name = 'Контакт'
        verbose_name_plural = 'Контакты'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.company})"
    
    
class Pipeline(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID')
    name = models.CharField(max_length=128, verbose_name='Название воронки')
    stages = models.JSONField(verbose_name='Этапы воронки')  # JSONB
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pipelines', verbose_name='Владелец')

    class Meta:
        verbose_name = 'Воронка продаж'
        verbose_name_plural = 'Воронки продаж'
        ordering = ['-created_at']

    def __str__(self):
        return self.name
    
    
class Deal(models.Model):
    STATUS_CHOICES = [
        ('lead', 'Лид'),
        ('prospect', 'Потенциальный клиент'),
        ('deal', 'Сделка в работе'),
        ('closed', 'Закрыта'),
        ('lost', 'Потеряна'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name='ID')
    title = models.CharField(max_length=255, verbose_name='Название сделки')
    value = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма сделки')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, verbose_name='Статус')
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name='deals', verbose_name='Воронка')
    stage = models.CharField(max_length=128, verbose_name='Текущий этап')
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='deals', verbose_name='Контакт')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_deals', verbose_name='Ответственный')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Сделка'
        verbose_name_plural = 'Сделки'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.status})"
    
class Task(models.Model):
    STATUS_CHOICES = [
        ('pending', 'В ожидании'),
        ('in_progress', 'В процессе'),
        ('done', 'Выполнена'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255, verbose_name='Заголовок задачи')
    description = models.TextField(blank=True, verbose_name='Описание')
    due_date = models.DateTimeField(verbose_name='Срок выполнения')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='Статус')

    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks', verbose_name='Ответственный')
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks', verbose_name='Сделка (необязательно)')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Задача'
        verbose_name_plural = 'Задачи'
        ordering = ['-due_date']

    def __str__(self):
        return f"{self.title} — {self.status}"
    
    
class Integration(models.Model):
    TYPE_CHOICES = [
        ('telephony', 'Телефония'),
        ('messenger', 'Мессенджер'),
        ('1c', '1C'),
    ]

    STATUS_CHOICES = [
        ('active', 'Активна'),
        ('inactive', 'Неактивна'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name='Тип интеграции')
    config = models.JSONField(verbose_name='Конфигурация')  # JSONB в PostgreSQL
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='inactive', verbose_name='Статус')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Интеграция'
        verbose_name_plural = 'Интеграции'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.type} — {self.status}"
    
class Analytics(models.Model):
    TYPE_CHOICES = [
        ('sales', 'Продажи'),
        ('activity', 'Активность'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name='Тип аналитики')
    data = models.JSONField(verbose_name='Данные')  # Пример: {"metric": "total_sales", "value": 150000, "date": "2025-06-01"}
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')

    class Meta:
        verbose_name = 'Аналитика'
        verbose_name_plural = 'Аналитика'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.type} — {self.data.get('metric', '')}"
    
class Order(models.Model):
    STATUS_CHOICES = [
        ('new', 'Новый'),
        ('pending', 'В процессе'),
        ('completed', 'Завершён'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=50, verbose_name='Номер заказа')
    customer_name = models.CharField(max_length=128, verbose_name='Имя клиента')
    date_ordered = models.DateField(verbose_name='Дата заказа')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new', verbose_name='Статус')
    phone = models.CharField(max_length=32, verbose_name='Телефон')
    department = models.CharField(max_length=64, verbose_name='Отдел')
    total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Сумма')
    quantity = models.PositiveIntegerField(verbose_name='Количество')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-date_ordered']

    def __str__(self):
        return f"{self.order_number} — {self.customer_name}"
    
class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, verbose_name='Название')
    article = models.CharField(max_length=64, verbose_name='Артикул')
    brand = models.CharField(max_length=64, verbose_name='Бренд')
    category = models.CharField(max_length=64, verbose_name='Категория')
    quantity = models.PositiveIntegerField(verbose_name='Остаток')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Цена')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.article})"
    
    
class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews', verbose_name='Пользователь')
    rating = models.PositiveSmallIntegerField(verbose_name='Оценка (1-5)')
    comment = models.TextField(verbose_name='Комментарий')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} — {self.rating}★"
    
    
class Notification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', verbose_name='Получатель')
    message = models.TextField(verbose_name='Сообщение')
    is_read = models.BooleanField(default=False, verbose_name='Прочитано')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано')

    class Meta:
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email}: {self.message[:30]}..."
