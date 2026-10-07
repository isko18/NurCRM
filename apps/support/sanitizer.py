"""
Серверная чистка секретов в отчётах об ошибках (ТЗ ч.7, п. 3.2).

Прогоняется по message, stack и context перед сохранением:
Bearer-токены, JWT, токены Telegram-ботов (bot<цифры>:...), ключи Google (AIza...),
пароли/токены в JSON и в виде key=value, номера банковских карт (с проверкой Луна).
"""
import re
from typing import Any, List

MAX_MESSAGE = 4000
MAX_STACK = 8 * 1024
MAX_CONTEXT_LINES = 50
MAX_CONTEXT_TOTAL = 16 * 1024
MAX_CONTEXT_LINE = 2000

RE_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.=+/~]+", re.IGNORECASE)
RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]*)?")
# bot<цифры>:<секрет> — в URL api.telegram.org/bot123:AA... и в тексте
RE_BOT_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_\-]+", re.IGNORECASE)
# голый токен бота без префикса: 123456789:AAH... (секрет >= 20 символов)
RE_BARE_BOT_TOKEN = re.compile(r"(?<![\w])\d{6,}:[A-Za-z0-9_\-]{20,}")
RE_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z\-_]{20,}")

_SECRET_KEYS = (
    r"password|passwd|pwd|pin|pin_code|secret|client_secret|token|access_token|"
    r"refresh_token|id_token|api_key|apikey|x-api-key|authorization|bot_token|private_key"
)
# "password": "..." (с экранированными кавычками внутри) и 'password': '...'
RE_JSON_SECRET_DQ = re.compile(
    r'("(?:' + _SECRET_KEYS + r')"\s*:\s*")((?:[^"\\]|\\.)*)(")', re.IGNORECASE
)
RE_JSON_SECRET_SQ = re.compile(
    r"('(?:" + _SECRET_KEYS + r")'\s*:\s*')((?:[^'\\]|\\.)*)(')", re.IGNORECASE
)
# password=... / password: ... (query string, логи)
RE_KV_SECRET = re.compile(
    r"(\b(?:" + _SECRET_KEYS + r")\b\s*[=:]\s*)(?![\"'\[{]|\[REDACTED)([^\s&,;\"']+)", re.IGNORECASE
)
# Кандидаты в номера карт: 13–19 цифр, допускаются пробелы/дефисы между группами
RE_CARD_CANDIDATE = re.compile(r"(?<![\d\w])\d(?:[ -]?\d){12,18}(?![\d\w])")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card_sub(match: "re.Match") -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if 13 <= len(digits) <= 19 and _luhn_ok(digits) and len(set(digits)) > 1:
        return "[REDACTED_CARD]"
    return raw


def sanitize_text(text: Any, limit: int = 0) -> str:
    """Очищает строку от секретов. limit > 0 — обрезать результат."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text

    text = RE_BEARER.sub(r"\1[REDACTED]", text)
    text = RE_JWT.sub("[REDACTED_JWT]", text)
    text = RE_BOT_TOKEN.sub("[REDACTED_BOT_TOKEN]", text)
    text = RE_BARE_BOT_TOKEN.sub("[REDACTED_BOT_TOKEN]", text)
    text = RE_GOOGLE_KEY.sub("[REDACTED_AI_KEY]", text)
    text = RE_JSON_SECRET_DQ.sub(r"\1[REDACTED]\3", text)
    text = RE_JSON_SECRET_SQ.sub(r"\1[REDACTED]\3", text)
    text = RE_KV_SECRET.sub(r"\1[REDACTED]", text)
    text = RE_CARD_CANDIDATE.sub(_card_sub, text)

    if limit and len(text) > limit:
        text = text[:limit]
    return text


def sanitize_context(context_lines: Any) -> List[str]:
    """Последние 50 строк журнала, до 16 КБ суммарно, каждая строка очищена."""
    if isinstance(context_lines, str):
        context_lines = context_lines.splitlines()
    if not isinstance(context_lines, (list, tuple)):
        return []
    lines = [sanitize_text(line, MAX_CONTEXT_LINE) for line in list(context_lines)[-MAX_CONTEXT_LINES:]]
    # Оставляем самые свежие строки в пределах общего лимита
    out: List[str] = []
    total = 0
    for line in reversed(lines):
        size = len(line.encode("utf-8", errors="ignore")) + 1
        if total + size > MAX_CONTEXT_TOTAL:
            break
        out.append(line)
        total += size
    out.reverse()
    return out
