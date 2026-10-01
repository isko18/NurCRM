"""
Настройки для локального прогона тестов без PostgreSQL.

Использование:
  set DJANGO_SETTINGS_MODULE=core.settings_test_sqlite
  python manage.py test apps.main
"""
from .settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Celery-таски выполняются синхронно в тестах (без брокера Redis).
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Настройки безопасности для тестового окружения
SECURE_SSL_REDIRECT = False
ALLOWED_HOSTS = ["*"]


class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = DisableMigrations()

from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def extend_sqlite(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        connection.connection.create_function("lower", 1, lambda s: s.lower() if s is not None else None, deterministic=True)



