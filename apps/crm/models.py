from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
import uuid

from apps.users.models import Company, User, Branch


class SalesFunnel(models.Model):
    """Воронка продаж"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company, 
        on_delete=models.CASCADE, 
        related_name='sales_funnels',
        verbose_name='Компания'
    )
    name = models.CharField(max_length=255, verbose_name='Название воронки')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Воронка продаж'
        verbose_name_plural = 'Воронки продаж'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.company.name})"


class FunnelStage(models.Model):
    """Стадия воронки продаж"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    funnel = models.ForeignKey(
        SalesFunnel,
        on_delete=models.CASCADE,
        related_name='stages',
        verbose_name='Воронка'
    )
    name = models.CharField(max_length=255, verbose_name='Название стадии')
    order = models.PositiveIntegerField(default=0, verbose_name='Порядок')
    color = models.CharField(
        max_length=7,
        default='#3498db',
        verbose_name='Цвет',
        help_text='Hex цвет (например, #3498db)'
    )
    is_final = models.BooleanField(
        default=False,
        verbose_name='Финальная стадия',
        help_text='Стадия закрытия сделки (успех или провал)'
    )
    is_success = models.BooleanField(
        default=False,
        verbose_name='Успешная стадия',
        help_text='Стадия успешного закрытия сделки'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')

    class Meta:
        verbose_name = 'Стадия воронки'
        verbose_name_plural = 'Стадии воронки'
        ordering = ['funnel', 'order']
        unique_together = [['funnel', 'order']]
        indexes = [
            models.Index(fields=['funnel', 'order']),
        ]

    def __str__(self):
        return f"{self.funnel.name} - {self.name}"


class Contact(models.Model):
    """Контакт/Клиент"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='crm_contacts',
        verbose_name='Компания'
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='crm_contact_set',
        verbose_name='Филиал'
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='crm_contacts',
        verbose_name='Ответственный'
    )
    
    # Основная информация
    first_name = models.CharField(max_length=100, verbose_name='Имя')
    last_name = models.CharField(max_length=100, blank=True, null=True, verbose_name='Фамилия')
    middle_name = models.CharField(max_length=100, blank=True, null=True, verbose_name='Отчество')
    
    # Контакты
    phone = models.CharField(max_length=20, db_index=True, verbose_name='Телефон')
    phone_secondary = models.CharField(max_length=20, blank=True, null=True, verbose_name='Дополнительный телефон')
    email = models.EmailField(blank=True, null=True, verbose_name='Email')
    whatsapp = models.CharField(max_length=20, blank=True, null=True, verbose_name='WhatsApp')
    instagram = models.CharField(max_length=100, blank=True, null=True, verbose_name='Instagram')
    
    # Дополнительная информация
    company_name = models.CharField(max_length=255, blank=True, null=True, verbose_name='Название компании')
    position = models.CharField(max_length=100, blank=True, null=True, verbose_name='Должность')
    address = models.TextField(blank=True, null=True, verbose_name='Адрес')
    notes = models.TextField(blank=True, null=True, verbose_name='Заметки')
    
    # Теги и категории
    tags = models.JSONField(default=list, blank=True, verbose_name='Теги')
    source = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Источник',
        help_text='Откуда пришел контакт (WhatsApp, Instagram, сайт и т.д.)'
    )
    
    # Статусы
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    is_client = models.BooleanField(default=False, verbose_name='Является клиентом')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Контакт'
        verbose_name_plural = 'Контакты'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['phone']),
            models.Index(fields=['owner']),
        ]

    def __str__(self):
        full_name = f"{self.first_name} {self.last_name or ''}".strip()
        return full_name or self.phone

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join([p for p in parts if p])


class Lead(models.Model):
    """Лид - потенциальный клиент"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='crm_leads',
        verbose_name='Компания'
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name='leads',
        verbose_name='Контакт'
    )
    funnel = models.ForeignKey(
        SalesFunnel,
        on_delete=models.CASCADE,
        related_name='leads',
        verbose_name='Воронка'
    )
    stage = models.ForeignKey(
        FunnelStage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='leads',
        verbose_name='Текущая стадия'
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='leads',
        verbose_name='Ответственный'
    )
    
    # Информация о лиде
    title = models.CharField(max_length=255, verbose_name='Название лида')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    
    # Оценка
    estimated_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name='Оценочная стоимость'
    )
    probability = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name='Вероятность закрытия (%)'
    )
    
    # Источник
    source = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Источник',
        help_text='Откуда пришел лид'
    )
    
    # Даты
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата закрытия')

    class Meta:
        verbose_name = 'Лид'
        verbose_name_plural = 'Лиды'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'stage']),
            models.Index(fields=['owner']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.title} - {self.contact}"


class Deal(models.Model):
    """Сделка"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='crm_deals',
        verbose_name='Компания'
    )
    lead = models.OneToOneField(
        Lead,
        on_delete=models.CASCADE,
        related_name='deal',
        null=True,
        blank=True,
        verbose_name='Лид'
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name='deals',
        verbose_name='Контакт'
    )
    funnel = models.ForeignKey(
        SalesFunnel,
        on_delete=models.CASCADE,
        related_name='deals',
        verbose_name='Воронка'
    )
    stage = models.ForeignKey(
        FunnelStage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deals',
        verbose_name='Текущая стадия'
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deals',
        verbose_name='Ответственный'
    )
    
    # Информация о сделке
    title = models.CharField(max_length=255, verbose_name='Название сделки')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    
    # Финансы
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name='Сумма сделки'
    )
    probability = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name='Вероятность закрытия (%)'
    )
    
    # Статус
    is_won = models.BooleanField(default=False, verbose_name='Выиграна')
    is_lost = models.BooleanField(default=False, verbose_name='Проиграна')
    lost_reason = models.TextField(blank=True, null=True, verbose_name='Причина проигрыша')
    
    # Даты
    expected_close_date = models.DateField(null=True, blank=True, verbose_name='Ожидаемая дата закрытия')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата закрытия')

    class Meta:
        verbose_name = 'Сделка'
        verbose_name_plural = 'Сделки'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'stage']),
            models.Index(fields=['owner']),
            models.Index(fields=['is_won', 'is_lost']),
            models.Index(fields=['expected_close_date']),
        ]

    def __str__(self):
        return f"{self.title} - {self.amount}"


class WazzuppAccount(models.Model):
    """Аккаунт Wazzupp для интеграции"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='wazzupp_accounts',
        verbose_name='Компания'
    )
    
    # Данные для подключения
    api_key = models.CharField(max_length=255, verbose_name='API ключ')
    api_url = models.URLField(verbose_name='API URL', help_text='Базовый URL API Wazzupp')
    instance_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='ID инстанса',
        help_text='ID инстанса в Wazzupp'
    )
    
    # Тип интеграции
    INTEGRATION_TYPES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
        ('telegram', 'Telegram'),
    ]
    integration_type = models.CharField(
        max_length=20,
        choices=INTEGRATION_TYPES,
        default='whatsapp',
        verbose_name='Тип интеграции'
    )
    
    # Статус
    is_active = models.BooleanField(default=True, verbose_name='Активна')
    is_connected = models.BooleanField(default=False, verbose_name='Подключена')
    last_sync = models.DateTimeField(null=True, blank=True, verbose_name='Последняя синхронизация')
    
    # Дополнительные данные
    metadata = models.JSONField(default=dict, blank=True, verbose_name='Метаданные')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Wazzupp аккаунт'
        verbose_name_plural = 'Wazzupp аккаунты'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['integration_type']),
        ]

    def __str__(self):
        return f"{self.get_integration_type_display()} - {self.company.name}"


class WazzuppMessage(models.Model):
    """Сообщение из Wazzupp (WhatsApp/Instagram)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        WazzuppAccount,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='Аккаунт Wazzupp'
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wazzupp_messages',
        verbose_name='Контакт'
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='wazzupp_messages',
        verbose_name='Лид'
    )
    
    # Данные сообщения
    message_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name='ID сообщения в Wazzupp'
    )
    from_number = models.CharField(max_length=20, verbose_name='От кого (номер/username)')
    to_number = models.CharField(max_length=20, verbose_name='Кому (номер/username)')
    
    # Тип сообщения
    MESSAGE_TYPES = [
        ('text', 'Текст'),
        ('image', 'Изображение'),
        ('video', 'Видео'),
        ('audio', 'Аудио'),
        ('document', 'Документ'),
        ('location', 'Локация'),
        ('contact', 'Контакт'),
        ('sticker', 'Стикер'),
    ]
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPES,
        default='text',
        verbose_name='Тип сообщения'
    )
    
    # Содержимое
    text = models.TextField(blank=True, null=True, verbose_name='Текст сообщения')
    media_url = models.URLField(blank=True, null=True, verbose_name='URL медиа')
    caption = models.TextField(blank=True, null=True, verbose_name='Подпись к медиа')
    
    # Направление
    is_incoming = models.BooleanField(default=True, verbose_name='Входящее')
    is_read = models.BooleanField(default=False, verbose_name='Прочитано')
    
    # Статус доставки
    STATUS_CHOICES = [
        ('sent', 'Отправлено'),
        ('delivered', 'Доставлено'),
        ('read', 'Прочитано'),
        ('failed', 'Ошибка'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='sent',
        verbose_name='Статус'
    )
    
    # Метаданные
    metadata = models.JSONField(default=dict, blank=True, verbose_name='Метаданные')
    timestamp = models.DateTimeField(verbose_name='Время сообщения')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания в системе')

    class Meta:
        verbose_name = 'Сообщение Wazzupp'
        verbose_name_plural = 'Сообщения Wazzupp'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['account', 'timestamp']),
            models.Index(fields=['contact']),
            models.Index(fields=['from_number']),
            models.Index(fields=['is_incoming']),
        ]

    def __str__(self):
        direction = "→" if self.is_incoming else "←"
        return f"{direction} {self.from_number}: {self.text[:50] if self.text else self.message_type}"


class Activity(models.Model):
    """Активность по контакту/лиду/сделке"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='activities',
        verbose_name='Компания'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activities',
        verbose_name='Пользователь'
    )
    
    # Связи
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='activities',
        verbose_name='Контакт'
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='activities',
        verbose_name='Лид'
    )
    deal = models.ForeignKey(
        Deal,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='activities',
        verbose_name='Сделка'
    )
    
    # Тип активности
    ACTIVITY_TYPES = [
        ('call', 'Звонок'),
        ('meeting', 'Встреча'),
        ('email', 'Email'),
        ('message', 'Сообщение'),
        ('note', 'Заметка'),
        ('task', 'Задача'),
        ('stage_change', 'Смена стадии'),
    ]
    activity_type = models.CharField(
        max_length=20,
        choices=ACTIVITY_TYPES,
        verbose_name='Тип активности'
    )
    
    title = models.CharField(max_length=255, verbose_name='Название')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    
    # Даты
    activity_date = models.DateTimeField(verbose_name='Дата активности')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')

    class Meta:
        verbose_name = 'Активность'
        verbose_name_plural = 'Активности'
        ordering = ['-activity_date']
        indexes = [
            models.Index(fields=['company', 'activity_date']),
            models.Index(fields=['contact']),
            models.Index(fields=['lead']),
            models.Index(fields=['deal']),
        ]

    def __str__(self):
        return f"{self.get_activity_type_display()}: {self.title}"
