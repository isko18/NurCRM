"""
Запуск тяжёлых вызовов eKassa вне критического пути HTTP: после commit транзакции
в отдельном daemon-потоке (не ждём ответа API eKassa в веб-воркере).
"""
from __future__ import annotations

import logging
import threading

from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)


def schedule_after_commit(fn, *args, **kwargs) -> None:
    """
    После успешного commit текущей транзакции выполняет ``fn(*args, **kwargs)``
    в фоновом потоке. Не блокирует ответ клиенту.
    """

    def _job() -> None:
        close_old_connections()
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception(
                "Фоновая задача eKassa не удалась: %s",
                getattr(fn, "__name__", repr(fn)),
            )
        finally:
            close_old_connections()

    def _start_thread() -> None:
        threading.Thread(target=_job, daemon=True).start()

    transaction.on_commit(_start_thread)
