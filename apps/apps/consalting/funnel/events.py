"""Шина событий воронки (тонкая обёртка над Django signals).

Фаза 3: события просто эмитятся. Подписчик-движок автоматизации (Фаза 6)
подключается через signals.py, не меняя вызывающий код.
"""
import django.dispatch

# kwargs: trigger, lead, actor, ctx (dict)
funnel_event = django.dispatch.Signal()


def emit(trigger, lead, actor=None, **ctx):
    funnel_event.send(
        sender=lead.__class__, trigger=trigger, lead=lead, actor=actor, ctx=ctx
    )
