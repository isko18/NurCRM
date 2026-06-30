"""Локальная разработка весов: БД на localhost, без SSL-редиректа."""
from .settings import *  # noqa: F401,F403

import os

DEBUG = True

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "api.nur.kg",
    "app.nurcrm.kg",
    "app.nurcrm.kg",
]

CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "https://api.nur.kg",
    "http://api.nur.kg",
]

CORS_ALLOWED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://localhost:5173",
    "http://localhost:3000",
]

DATABASES["default"]["HOST"] = os.getenv("DATABASE_HOST", "127.0.0.1")
DATABASES["default"]["PORT"] = os.getenv("DATABASE_PORT", "5433")

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
