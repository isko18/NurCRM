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


class IGThread(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    ig_account = models.ForeignKey("CompanyIGAccount", on_delete=models.CASCADE)
    thread_id = models.CharField(max_length=100, db_index=True)
    title = models.CharField(max_length=255, blank=True)
    users = models.JSONField(default=list)  # [{pk, username}]
    last_activity = models.DateTimeField(default=timezone.now, null=True, blank=True)



    class Meta:
        unique_together = ("ig_account", "thread_id")
        indexes = [
            models.Index(fields=["ig_account", "last_activity"]),
        ]

    def as_dict(self):
        return {
            "thread_id": self.thread_id,
            "title": self.title,
            "users": self.users,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }


class IGMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    thread = models.ForeignKey(IGThread, on_delete=models.CASCADE)
    mid = models.CharField(max_length=100, unique=True)
    sender_pk = models.CharField(max_length=100)
    text = models.TextField(blank=True)
    attachments = models.JSONField(default=list)
    created_at = models.DateTimeField()
    direction = models.CharField(max_length=5, choices=(("in", "in"), ("out", "out")))

    class Meta:
        indexes = [
            models.Index(fields=["thread", "created_at"]),
        ]

    def as_dict(self):
        return {
            "mid": self.mid,
            "text": self.text,
            "sender_pk": self.sender_pk,
            "username": None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "direction": self.direction,
        }