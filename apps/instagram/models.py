# apps/instagram/models.py
import uuid
from django.db import models
from django.utils import timezone

# -------------------------------
# 1) Аккаунт Instagram на компанию
# -------------------------------

class CompanyIGAccount(models.Model):
    """
    IG-аккаунт, закреплённый за конкретной Company.
    Хранит 'settings_json' из instagrapi (по сути, сессию).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    company = models.ForeignKey(
        'users.Company',                     # <--- app_label.ModelName
        on_delete=models.CASCADE,
        related_name='ig_accounts',
        verbose_name='Компания'
    )
    username = models.CharField(max_length=150, verbose_name='Имя пользователя Instagram')
    device_seed = models.CharField(
        max_length=128,
        blank=True,
        verbose_name='Device seed (устойчивость авторизации)'
    )
    settings_json = models.JSONField(
        default=dict, blank=True,
        verbose_name='Сохранённые настройки/сессия instagrapi'
    )
    is_logged_in = models.BooleanField(default=False, verbose_name='Сессия активна')
    last_login_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата/время последнего входа')
    is_active = models.BooleanField(default=True, verbose_name='Аккаунт активен')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'IG аккаунт компании'
        verbose_name_plural = 'IG аккаунты компании'
        unique_together = (('company', 'username'),)
        indexes = [
            models.Index(fields=['company', 'username']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self) -> str:
        return f'{self.company.name} → @{self.username}'

    # Удобный флажок: «сессию можно попробовать резюмировать»
    @property
    def has_session(self) -> bool:
        return bool(self.settings_json)

    def mark_logged_in(self, settings: dict | None = None) -> None:
        if settings is not None:
            self.settings_json = settings
        self.is_logged_in = True
        self.last_login_at = timezone.now()
        self.save(update_fields=['settings_json', 'is_logged_in', 'last_login_at', 'updated_at'])

    def mark_logged_out(self) -> None:
        self.is_logged_in = False
        self.save(update_fields=['is_logged_in', 'updated_at'])


# -------------------------------
# 2) Тред (диалог) в Instagram DM
# -------------------------------

class IGThread(models.Model):
    """
    Отражение треда Direct. Один и тот же thread_id уникален в рамках IG-аккаунта.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    ig_account = models.ForeignKey(
        CompanyIGAccount,
        on_delete=models.CASCADE,
        related_name='threads',
        verbose_name='IG аккаунт'
    )
    thread_id = models.CharField(max_length=64, db_index=True, verbose_name='ID треда (Instagram)')
    title = models.CharField(max_length=255, blank=True, verbose_name='Заголовок треда')
    users = models.JSONField(default=list, blank=True, verbose_name='Участники (pk/username)')
    last_activity = models.DateTimeField(null=True, blank=True, verbose_name='Последняя активность')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Тред Instagram'
        verbose_name_plural = 'Треды Instagram'
        unique_together = (('ig_account', 'thread_id'),)
        indexes = [
            models.Index(fields=['ig_account', 'thread_id']),
            models.Index(fields=['-last_activity']),
        ]

    def __str__(self) -> str:
        return f'{self.thread_id} ({self.ig_account.username})'


# -------------------------------
# 3) Сообщение в треде
# -------------------------------

class IGMessage(models.Model):
    """
    Сообщение из Direct. mid — уникальный id сообщения.
    direction:
        - 'in'  — входящее от клиента
        - 'out' — исходящее от нас
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    thread = models.ForeignKey(
        IGThread,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='Тред'
    )
    mid = models.CharField(max_length=128, unique=True, verbose_name='ID сообщения (Instagram или локальный)')
    sender_pk = models.CharField(max_length=64, verbose_name='PK отправителя')
    text = models.TextField(blank=True, verbose_name='Текст')
    attachments = models.JSONField(default=list, blank=True, verbose_name='Вложения (опционально)')

    created_at = models.DateTimeField(verbose_name='Когда отправлено')
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name='Доставлено')
    read_at = models.DateTimeField(null=True, blank=True, verbose_name='Прочитано')

    DIRECTION_IN = 'in'
    DIRECTION_OUT = 'out'
    DIRECTIONS = (
        (DIRECTION_IN, 'Входящее'),
        (DIRECTION_OUT, 'Исходящее'),
    )
    direction = models.CharField(max_length=3, choices=DIRECTIONS, verbose_name='Направление')

    created_local_at = models.DateTimeField(auto_now_add=True, verbose_name='Записано в БД')

    class Meta:
        verbose_name = 'Сообщение Instagram'
        verbose_name_plural = 'Сообщения Instagram'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['thread']),
            models.Index(fields=['-created_at']),
            models.Index(fields=['direction']),
        ]

    def __str__(self) -> str:
        short = (self.text or '').strip().replace('\n', ' ')
        if len(short) > 40:
            short = short[:37] + '...'
        return f'{self.direction} | {self.thread.thread_id} | {short}'
