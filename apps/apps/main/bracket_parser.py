"""
Разбор bracket-notation из multipart/form-data в вложенные структуры.

Браузерный FormData плоский, поэтому фронт отправляет вложенные списки так:

    title                   = "Маркет"
    lessons[0][title]       = "Интерфейс кассира"
    lessons[0][url]         = "https://youtu.be/…"
    lessons[0][thumbnail]   = <File>
    lessons[1][title]       = "Настройки"

`unflatten_bracket_data` превращает это в:

    {"title": "Маркет", "lessons": [{"title": …, "url": …, "thumbnail": <File>}, {...}]}

JSON-тело проходит насквозь без изменений — вложенность там уже есть.
"""

from __future__ import annotations

import re

from django.http import QueryDict

__all__ = ["unflatten_bracket_data", "has_bracket_keys"]

# name, затем ноль или больше сегментов [...]
_KEY_RE = re.compile(r"^([^\[\]]+)((?:\[[^\[\]]*\])*)$")
_SEGMENT_RE = re.compile(r"\[([^\[\]]*)\]")


def has_bracket_keys(data) -> bool:
    try:
        return any("[" in key for key in data.keys())
    except Exception:
        return False


def _split_key(key: str):
    """'lessons[0][title]' -> ('lessons', ['0', 'title']); None если ключ не подходит."""
    match = _KEY_RE.match(key)
    if not match:
        return None
    name, rest = match.group(1), match.group(2)
    return name, _SEGMENT_RE.findall(rest)


def _assign(container: dict, path: list, value):
    """
    Кладёт value по пути path внутрь container. Числовые сегменты остаются
    строковыми ключами — списки собираются позже, в _collapse.
    """
    cursor = container
    for segment in path[:-1]:
        nxt = cursor.get(segment)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[segment] = nxt
        cursor = nxt
    cursor[path[-1]] = value


def _collapse(node):
    """
    Рекурсивно превращает словари с ключами '0', '1', '2'… в списки,
    отсортированные по индексу. Пропуски в индексах не ломают порядок.
    """
    if not isinstance(node, dict):
        return node

    collapsed = {key: _collapse(value) for key, value in node.items()}

    if collapsed and all(key.isdigit() for key in collapsed):
        return [collapsed[key] for key in sorted(collapsed, key=int)]

    return collapsed


def unflatten_bracket_data(data):
    """
    QueryDict с bracket-ключами -> вложенный dict.
    Данные без bracket-ключей (в т.ч. JSON) возвращаются как есть.
    """
    if not isinstance(data, QueryDict) or not has_bracket_keys(data):
        return data

    result: dict = {}

    for key in data.keys():
        values = data.getlist(key)
        value = values[0] if len(values) == 1 else values

        parsed = _split_key(key)
        if parsed is None:
            result[key] = value
            continue

        name, segments = parsed
        if not segments:
            result[name] = value
            continue

        # пустой сегмент (field[]) трактуем как «добавить в конец»
        path = [name]
        for index, segment in enumerate(segments):
            if segment == "":
                segment = str(index)
            path.append(segment)

        _assign(result, path, value)

    return _collapse(result)
