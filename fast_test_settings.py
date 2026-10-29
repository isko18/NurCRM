import os
os.environ["USE_SQLITE"] = "1"
from core.settings import *  # noqa

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}


class _NoMigrations(dict):
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _NoMigrations()
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
