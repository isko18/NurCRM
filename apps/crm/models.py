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


# ==================== META BUSINESS INTEGRATION ====================


class MetaBusinessAccount(models.Model):
    """
    Аккаунт Meta Business для интеграции с WhatsApp Business API и Instagram Messaging API.
    Один Meta Business Account может иметь несколько WhatsApp и Instagram аккаунтов.
    
    Документация:
    - https://developers.facebook.com/docs/whatsapp/cloud-api
    - https://developers.facebook.com/docs/messenger-platform/instagram
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='meta_accounts',
        verbose_name='Компания'
    )
    
    # Данные Meta Business
    business_id = models.CharField(
        max_length=100,
        verbose_name='Meta Business ID',
        help_text='ID бизнес-аккаунта в Meta Business Suite'
    )
    business_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Название бизнеса'
    )
    
    # Токены доступа
    access_token = models.TextField(
        verbose_name='Access Token',
        help_text='Постоянный токен доступа от Meta (System User Token)'
    )
    
    # Webhook настройки
    webhook_verify_token = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Webhook Verify Token',
        help_text='Токен для верификации webhook от Meta'
    )
    webhook_secret = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='App Secret',
        help_text='App Secret для проверки подписи webhook'
    )
    
    # Статус
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    is_verified = models.BooleanField(default=False, verbose_name='Верифицирован')
    
    # Метаданные
    metadata = models.JSONField(default=dict, blank=True, verbose_name='Метаданные')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Meta Business аккаунт'
        verbose_name_plural = 'Meta Business аккаунты'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['business_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'business_id'],
                name='uq_meta_business_per_company'
            ),
        ]

    def __str__(self):
        return f"Meta Business: {self.business_name or self.business_id} ({self.company.name})"


class WhatsAppBusinessAccount(models.Model):
    """
    WhatsApp Business Account (WABA) — подключенный через Meta Cloud API.
    
    Документация: https://developers.facebook.com/docs/whatsapp/cloud-api/get-started
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meta_account = models.ForeignKey(
        MetaBusinessAccount,
        on_delete=models.CASCADE,
        related_name='whatsapp_accounts',
        verbose_name='Meta Business аккаунт'
    )
    
    # WhatsApp Business Account ID
    waba_id = models.CharField(
        max_length=100,
        verbose_name='WABA ID',
        help_text='WhatsApp Business Account ID'
    )
    
    # Phone Number ID (для отправки сообщений)
    phone_number_id = models.CharField(
        max_length=100,
        verbose_name='Phone Number ID',
        help_text='ID номера телефона в WhatsApp Business'
    )
    
    # Отображаемые данные
    phone_number = models.CharField(
        max_length=20,
        verbose_name='Номер телефона',
        help_text='Номер в формате +7XXXXXXXXXX'
    )
    display_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Отображаемое имя'
    )
    
    # Качество аккаунта (от Meta)
    QUALITY_CHOICES = [
        ('green', 'Высокое'),
        ('yellow', 'Среднее'),
        ('red', 'Низкое'),
        ('unknown', 'Неизвестно'),
    ]
    quality_rating = models.CharField(
        max_length=20,
        choices=QUALITY_CHOICES,
        default='unknown',
        verbose_name='Рейтинг качества'
    )
    
    # Лимиты
    messaging_limit = models.CharField(
        max_length=50,
        blank=True,
        verbose_name='Лимит сообщений',
        help_text='TIER_1K, TIER_10K, TIER_100K, UNLIMITED'
    )
    
    # Статус
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    is_verified = models.BooleanField(default=False, verbose_name='Номер верифицирован')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'WhatsApp Business аккаунт'
        verbose_name_plural = 'WhatsApp Business аккаунты'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['meta_account', 'is_active']),
            models.Index(fields=['waba_id']),
            models.Index(fields=['phone_number_id']),
            models.Index(fields=['phone_number']),
        ]

    def __str__(self):
        return f"WhatsApp: {self.display_name or self.phone_number}"

    @property
    def company(self):
        return self.meta_account.company


class InstagramBusinessAccount(models.Model):
    """
    Instagram Business/Creator Account — подключенный через Instagram Graph API.
    
    Документация: https://developers.facebook.com/docs/instagram-api/guides/messaging
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meta_account = models.ForeignKey(
        MetaBusinessAccount,
        on_delete=models.CASCADE,
        related_name='instagram_accounts',
        verbose_name='Meta Business аккаунт'
    )
    
    # Instagram Business Account ID (IGBA)
    instagram_id = models.CharField(
        max_length=100,
        verbose_name='Instagram Business ID',
        help_text='ID бизнес-аккаунта Instagram'
    )
    
    # Facebook Page ID (связанная страница)
    facebook_page_id = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Facebook Page ID',
        help_text='ID связанной Facebook страницы'
    )
    
    # Данные профиля
    username = models.CharField(
        max_length=100,
        verbose_name='Username',
        help_text='@username в Instagram'
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Имя профиля'
    )
    profile_picture_url = models.URLField(
        blank=True,
        verbose_name='URL аватара'
    )
    
    # Статистика
    followers_count = models.PositiveIntegerField(default=0, verbose_name='Подписчиков')
    
    # Статус
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Instagram Business аккаунт'
        verbose_name_plural = 'Instagram Business аккаунты'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['meta_account', 'is_active']),
            models.Index(fields=['instagram_id']),
            models.Index(fields=['username']),
        ]

    def __str__(self):
        return f"Instagram: @{self.username}"

    @property
    def company(self):
        return self.meta_account.company


class Conversation(models.Model):
    """
    Переписка (чат) с клиентом через WhatsApp или Instagram.
    Объединяет сообщения в один тред.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='conversations',
        verbose_name='Компания'
    )
    
    # Связь с аккаунтами (один из двух)
    whatsapp_account = models.ForeignKey(
        WhatsAppBusinessAccount,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='conversations',
        verbose_name='WhatsApp аккаунт'
    )
    instagram_account = models.ForeignKey(
        InstagramBusinessAccount,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='conversations',
        verbose_name='Instagram аккаунт'
    )
    
    # Связь с CRM
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='conversations',
        verbose_name='Контакт'
    )
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='conversations',
        verbose_name='Лид'
    )
    
    # Канал
    CHANNEL_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('instagram', 'Instagram'),
    ]
    channel = models.CharField(
        max_length=20,
        choices=CHANNEL_CHOICES,
        verbose_name='Канал'
    )
    
    # Идентификатор клиента в канале
    participant_id = models.CharField(
        max_length=100,
        verbose_name='ID участника',
        help_text='Номер телефона (WhatsApp) или IGSID (Instagram)'
    )
    participant_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Имя участника'
    )
    participant_username = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Username (Instagram)'
    )
    
    # Ответственный менеджер
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_conversations',
        verbose_name='Ответственный'
    )
    
    # Статус переписки
    STATUS_CHOICES = [
        ('active', 'Активная'),
        ('pending', 'Ожидает ответа'),
        ('resolved', 'Решена'),
        ('archived', 'Архив'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active',
        verbose_name='Статус'
    )
    
    # Окно сообщений (24 часа для WhatsApp)
    window_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Окно истекает',
        help_text='Время истечения 24-часового окна для бесплатных сообщений'
    )
    
    # Счетчики
    unread_count = models.PositiveIntegerField(default=0, verbose_name='Непрочитанных')
    messages_count = models.PositiveIntegerField(default=0, verbose_name='Всего сообщений')
    
    # Последнее сообщение (для превью)
    last_message_text = models.TextField(blank=True, verbose_name='Последнее сообщение')
    last_message_at = models.DateTimeField(null=True, blank=True, verbose_name='Время последнего сообщения')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Переписка'
        verbose_name_plural = 'Переписки'
        ordering = ['-last_message_at']
        indexes = [
            models.Index(fields=['company', 'status', 'last_message_at']),
            models.Index(fields=['channel', 'participant_id']),
            models.Index(fields=['contact']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['whatsapp_account', 'participant_id']),
            models.Index(fields=['instagram_account', 'participant_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['whatsapp_account', 'participant_id'],
                condition=models.Q(whatsapp_account__isnull=False),
                name='uq_conversation_whatsapp_participant'
            ),
            models.UniqueConstraint(
                fields=['instagram_account', 'participant_id'],
                condition=models.Q(instagram_account__isnull=False),
                name='uq_conversation_instagram_participant'
            ),
        ]

    def __str__(self):
        channel_icon = "📱" if self.channel == 'whatsapp' else "📷"
        return f"{channel_icon} {self.participant_name or self.participant_id}"

    @property
    def is_window_open(self):
        """Проверяет, открыто ли 24-часовое окно для отправки сообщений"""
        if not self.window_expires_at:
            return False
        return timezone.now() < self.window_expires_at


class Message(models.Model):
    """
    Сообщение в переписке (WhatsApp или Instagram).
    Соответствует формату Meta Webhook/API.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='Переписка'
    )
    
    # ID сообщения в Meta
    meta_message_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name='Meta Message ID',
        help_text='wamid для WhatsApp, mid для Instagram'
    )
    
    # Направление
    DIRECTION_CHOICES = [
        ('inbound', 'Входящее'),
        ('outbound', 'Исходящее'),
    ]
    direction = models.CharField(
        max_length=10,
        choices=DIRECTION_CHOICES,
        verbose_name='Направление'
    )
    
    # Отправитель (для исходящих — наш сотрудник)
    sender_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_messages',
        verbose_name='Отправитель (сотрудник)'
    )
    
    # Тип сообщения (WhatsApp Cloud API types)
    MESSAGE_TYPE_CHOICES = [
        ('text', 'Текст'),
        ('image', 'Изображение'),
        ('video', 'Видео'),
        ('audio', 'Аудио'),
        ('document', 'Документ'),
        ('sticker', 'Стикер'),
        ('location', 'Геолокация'),
        ('contacts', 'Контакты'),
        ('interactive', 'Интерактивное'),
        ('template', 'Шаблон'),
        ('reaction', 'Реакция'),
        ('unknown', 'Неизвестно'),
    ]
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES,
        default='text',
        verbose_name='Тип сообщения'
    )
    
    # Содержимое
    text = models.TextField(blank=True, verbose_name='Текст')
    
    # Медиа
    media_id = models.CharField(max_length=255, blank=True, verbose_name='Media ID')
    media_url = models.URLField(blank=True, verbose_name='URL медиа')
    media_mime_type = models.CharField(max_length=100, blank=True, verbose_name='MIME тип')
    media_filename = models.CharField(max_length=255, blank=True, verbose_name='Имя файла')
    media_caption = models.TextField(blank=True, verbose_name='Подпись к медиа')
    
    # Локация (для location type)
    location_latitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True, verbose_name='Широта'
    )
    location_longitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True, verbose_name='Долгота'
    )
    location_name = models.CharField(max_length=255, blank=True, verbose_name='Название места')
    location_address = models.CharField(max_length=255, blank=True, verbose_name='Адрес')
    
    # Ответ на сообщение (reply)
    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replies',
        verbose_name='В ответ на'
    )
    
    # Контекст (для interactive messages)
    context = models.JSONField(default=dict, blank=True, verbose_name='Контекст')
    
    # Статус доставки
    STATUS_CHOICES = [
        ('pending', 'Отправляется'),
        ('sent', 'Отправлено'),
        ('delivered', 'Доставлено'),
        ('read', 'Прочитано'),
        ('failed', 'Ошибка'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Статус'
    )
    error_code = models.CharField(max_length=50, blank=True, verbose_name='Код ошибки')
    error_message = models.TextField(blank=True, verbose_name='Сообщение об ошибке')
    
    # Прочтение
    is_read = models.BooleanField(default=False, verbose_name='Прочитано нами')
    read_at = models.DateTimeField(null=True, blank=True, verbose_name='Время прочтения')
    
    # Метаданные
    metadata = models.JSONField(default=dict, blank=True, verbose_name='Метаданные')
    
    # Временные метки
    timestamp = models.DateTimeField(verbose_name='Время сообщения')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Создано в системе')

    class Meta:
        verbose_name = 'Сообщение'
        verbose_name_plural = 'Сообщения'
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['conversation', 'timestamp']),
            models.Index(fields=['meta_message_id']),
            models.Index(fields=['direction', 'status']),
            models.Index(fields=['conversation', 'is_read']),
        ]

    def __str__(self):
        direction_icon = "←" if self.direction == 'inbound' else "→"
        preview = (self.text[:50] + '...') if self.text and len(self.text) > 50 else (self.text or self.message_type)
        return f"{direction_icon} {preview}"


class MessageTemplate(models.Model):
    """
    Шаблон сообщения WhatsApp (HSM - Highly Structured Message).
    Шаблоны должны быть одобрены Meta перед использованием.
    
    Документация: https://developers.facebook.com/docs/whatsapp/message-templates
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    whatsapp_account = models.ForeignKey(
        WhatsAppBusinessAccount,
        on_delete=models.CASCADE,
        related_name='templates',
        verbose_name='WhatsApp аккаунт'
    )
    
    # Данные шаблона из Meta
    template_id = models.CharField(
        max_length=100,
        verbose_name='Template ID',
        help_text='ID шаблона в Meta'
    )
    name = models.CharField(
        max_length=255,
        verbose_name='Название шаблона',
        help_text='Уникальное имя шаблона (lowercase, underscore)'
    )
    language = models.CharField(
        max_length=10,
        default='ru',
        verbose_name='Язык',
        help_text='Код языка: ru, en, kk и т.д.'
    )
    
    # Категория
    CATEGORY_CHOICES = [
        ('AUTHENTICATION', 'Аутентификация'),
        ('MARKETING', 'Маркетинг'),
        ('UTILITY', 'Утилита'),
    ]
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        verbose_name='Категория'
    )
    
    # Статус одобрения
    STATUS_CHOICES = [
        ('PENDING', 'На рассмотрении'),
        ('APPROVED', 'Одобрен'),
        ('REJECTED', 'Отклонен'),
        ('PAUSED', 'Приостановлен'),
        ('DISABLED', 'Отключен'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING',
        verbose_name='Статус'
    )
    rejection_reason = models.TextField(blank=True, verbose_name='Причина отклонения')
    
    # Компоненты шаблона (header, body, footer, buttons)
    components = models.JSONField(
        default=list,
        verbose_name='Компоненты',
        help_text='JSON структура шаблона (header, body, footer, buttons)'
    )
    
    # Примеры переменных (для отправки)
    example_values = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Примеры значений',
        help_text='Примеры значений для {{1}}, {{2}} и т.д.'
    )
    
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Шаблон сообщения'
        verbose_name_plural = 'Шаблоны сообщений'
        ordering = ['name']
        indexes = [
            models.Index(fields=['whatsapp_account', 'status']),
            models.Index(fields=['name', 'language']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['whatsapp_account', 'name', 'language'],
                name='uq_template_per_account_name_lang'
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.language}) - {self.status}"

    @property
    def company(self):
        return self.whatsapp_account.meta_account.company


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
