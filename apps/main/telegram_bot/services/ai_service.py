import base64
import hashlib
import html
import logging
import os
import re
import time
from typing import List, Tuple, Optional
import httpx
from django.conf import settings

logger = logging.getLogger("telegram_bot.ai")

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
]

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# ТЗ-07 1.1.6: на 429 — повтор с паузой, после 3 неудачных кругов — исключение,
# обработчики отвечают без ИИ (поиск по каталогу / «попробуйте позже»).
AI_RETRY_ROUNDS = 3
AI_RETRY_PAUSES = (1.0, 3.0)


class CompanyAiConcurrencyLimit:
    """ТЗ-09 п. 3.3: Очередь ИИ на компанию — не больше 3 одновременных запросов к Gemini."""
    def __init__(self, company_id, max_concurrent=3, timeout=15):
        self.key = f"tg_ai_concurrency:{company_id}"
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.acquired = False

    def __enter__(self):
        from django.core.cache import cache
        start = time.time()
        while time.time() - start < self.timeout:
            try:
                current = cache.get(self.key) or 0
                if current < self.max_concurrent:
                    cache.set(self.key, current + 1, timeout=60)
                    self.acquired = True
                    return self
            except Exception:
                return self
            time.sleep(0.5)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            from django.core.cache import cache
            try:
                current = cache.get(self.key) or 1
                cache.set(self.key, max(0, current - 1), timeout=60)
            except Exception:
                pass


def get_cached_question_reply(company_id, question: str) -> Optional[str]:
    """ТЗ-09 п. 3.4: Кэш одинаковых вопросов о товарах на 5 минут."""
    if not company_id or not question:
        return None
    from django.core.cache import cache
    h = hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:24]
    return cache.get(f"tg_ai_q_cache:{company_id}:{h}")


def set_cached_question_reply(company_id, question: str, reply: str) -> None:
    """ТЗ-09 п. 3.4: Сохраняет в кэш ответ на 5 минут (300 сек)."""
    if not company_id or not question or not reply:
        return
    from django.core.cache import cache
    h = hashlib.sha256(question.strip().lower().encode("utf-8")).hexdigest()[:24]
    cache.set(f"tg_ai_q_cache:{company_id}:{h}", reply, timeout=300)


def _check_key_rate_limit(api_key: str) -> None:
    """Общее ограничение на ключ ИИ (запросов в минуту), поверх лимитов компании."""
    from django.core.cache import cache

    limit = int(getattr(settings, "AI_KEY_REQUESTS_PER_MINUTE", 600) or 0)
    if not limit:
        return
    key = "ai_rl:%s:%d" % (hashlib.sha1(api_key.encode()).hexdigest()[:16], int(time.time() // 60))
    try:
        cache.add(key, 0, timeout=120)
        if cache.incr(key) > limit:
            raise RuntimeError("Превышен общий лимит запросов к ИИ, попробуйте позже.")
    except RuntimeError:
        raise
    except Exception:
        pass


def get_effective_ai_key(company_key: str = None) -> str:
    """Возвращает ключ ИИ компании или глобальный GEMINI_API_KEY из settings/env."""
    if company_key:
        return company_key

    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "NURCRM_GEMINI_API_KEY",
        "NURCRM_AI_KEY",
    ):
        value = getattr(settings, name, "") or os.getenv(name, "")
        if value:
            return value
    return ""


def get_ai_key_source(company_key: str = None) -> str:
    """Где реально берётся ключ ИИ: на уровне компании или общего сервера."""
    if company_key:
        return "own"
    return "shared" if get_effective_ai_key(company_key) else "shared"


def normalize_ai_output_for_telegram(raw_text: str) -> str:
    """
    Преобразует Markdown в HTML и отбрасывает пустые/бессмысленные ответы.
    ТЗ-09 п. 1.2: Один формат — HTML. **x** -> <b>x</b>, экранировать посторонние < > &.
    """
    if raw_text is None:
        return ""
    text = str(raw_text).strip()
    if not text:
        return ""

    placeholder_map = {}
    def _save_tag(m):
        key = f"__TAG_{len(placeholder_map)}__"
        placeholder_map[key] = m.group(0)
        return key

    # Сохраняем валидные теги Telegram
    tag_re = re.compile(r"<\/?(?:b|i|code|pre)\b[^>]*>|<\/?a\b[^>]*>", re.IGNORECASE)
    text = tag_re.sub(_save_tag, text)

    # Экранируем остальные < > &
    text = html.escape(text, quote=False)

    # Восстанавливаем теги
    for key, orig in placeholder_map.items():
        text = text.replace(key, orig)

    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or ""


def generate_chat_response(
    api_key: str,
    system_instruction: str,
    contents: List[dict],
    temperature: float = 0.4,
    max_tokens: int = 1000,
    tools: Optional[List[dict]] = None,
) -> Tuple[str, str]:
    """
    Генерирует ответ через Gemini REST API.
    При ошибках 5xx/503/429/timeout переходит к следующей модели из списка.
    Возвращает (текст_ответа, имя_модели).
    """
    if not api_key:
        raise ValueError("Google Gemini API key не настроен.")

    _check_key_rate_limit(api_key)
    last_error = None
    rate_limited = False
    with httpx.Client(timeout=20.0) as client:
        for round_no in range(AI_RETRY_ROUNDS):
            if round_no:
                if not rate_limited:
                    break
                time.sleep(AI_RETRY_PAUSES[min(round_no - 1, len(AI_RETRY_PAUSES) - 1)])
            rate_limited = False
            for model in GEMINI_MODELS:
                url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
                payload = {
                    "contents": contents,
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens,
                    },
                }
                if system_instruction:
                    payload["systemInstruction"] = {
                        "parts": [{"text": system_instruction}]
                    }
                if tools:
                    payload["tools"] = [{"functionDeclarations": tools}]
                    payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

                try:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates") or []
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
                            cleaned = normalize_ai_output_for_telegram(text)
                            if cleaned:
                                return cleaned, model
                            logger.warning("Gemini %s returned empty text; trying next model", model)
                            last_error = "Gemini returned empty response"
                            continue
                        logger.warning("Gemini %s returned no candidates; trying next model", model)
                        last_error = "Gemini returned no candidates"
                        continue

                    # Если ошибка 5xx или 429 — пробуем следующую модель
                    if resp.status_code >= 500 or resp.status_code == 429:
                        logger.warning("Gemini model %s returned HTTP %s, trying next model", model, resp.status_code)
                        last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                        rate_limited = rate_limited or resp.status_code == 429
                        continue

                    # Другие ошибки (например 400 Bad Request, 403 Forbidden)
                    err_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    err_msg = err_data.get("error", {}).get("message", resp.text[:200])
                    logger.error("Gemini model %s returned error: %s", model, err_msg)
                    raise ValueError(f"Gemini error ({resp.status_code}): {err_msg}")

                except httpx.RequestError as exc:
                    logger.warning("Gemini model %s connection error: %s, trying next model", model, exc)
                    last_error = str(exc)
                    continue

    raise RuntimeError(f"Все модели Gemini недоступны. Последняя ошибка: {last_error}")


def generate_chat_response_with_tools(
    api_key: str,
    system_instruction: str,
    contents: List[dict],
    tools: List[dict],
    execute_tool_fn,
    temperature: float = 0.2,
    max_tokens: int = 1000,
    max_iterations: int = 5,
) -> Tuple[str, str, List[str]]:
    """
    Выполняет цикл вызова функций (function calling) в Gemini.
    Поддерживает цепочки вызовов до max_iterations (например, для сравнения периодов).
    Возвращает (итоговый_текст, модель, список_вызванных_функций).
    """
    if not api_key:
        raise ValueError("Google Gemini API key не настроен.")

    _check_key_rate_limit(api_key)
    working_contents = [dict(c) for c in contents]
    functions_called: List[str] = []
    last_model = GEMINI_MODELS[0]

    with httpx.Client(timeout=25.0) as client:
        for iteration in range(max_iterations):
            step_executed = False
            last_error = None

            for model in GEMINI_MODELS:
                url = f"{GEMINI_API_BASE}/{model}:generateContent?key={api_key}"
                payload = {
                    "contents": working_contents,
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens,
                    },
                    "tools": [{"functionDeclarations": tools}],
                    "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
                }
                if system_instruction:
                    payload["systemInstruction"] = {
                        "parts": [{"text": system_instruction}]
                    }

                try:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        last_model = model
                        data = resp.json()
                        candidates = data.get("candidates") or []
                        if not candidates:
                            return "", last_model, functions_called

                        content = candidates[0].get("content") or {}
                        parts = content.get("parts") or []

                        # Ищем вызовы функций в ответе модели
                        func_calls = [p["functionCall"] for p in parts if "functionCall" in p]

                        if func_calls:
                            # Добавляем реплику модели с functionCall
                            working_contents.append({"role": "model", "parts": parts})

                            # Выполняем каждую запрошенную функцию
                            for fc in func_calls:
                                fn_name = fc.get("name")
                                fn_args = fc.get("args") or {}
                                functions_called.append(fn_name)

                                tool_res = execute_tool_fn(fn_name, fn_args)

                                working_contents.append({
                                    "role": "user",
                                    "parts": [
                                        {
                                            "functionResponse": {
                                                "name": fn_name,
                                                "response": {
                                                    "name": fn_name,
                                                    "content": tool_res,
                                                },
                                            }
                                        }
                                    ],
                                })
                            step_executed = True
                            break  # Переходим к следующей итерации цикла с ответом функции

                        else:
                            # Обычный текстовый ответ (завершение)
                            text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
                            cleaned = normalize_ai_output_for_telegram(text)
                            if cleaned:
                                return cleaned, last_model, functions_called
                            logger.warning("Gemini %s returned empty text on tools turn; trying next model", model)
                            last_error = "Gemini returned empty response"
                            continue

                    if resp.status_code >= 500 or resp.status_code == 429:
                        logger.warning("Gemini %s HTTP %s on tools turn, trying next", model, resp.status_code)
                        last_error = f"HTTP {resp.status_code}"
                        continue

                    err_msg = resp.text[:200]
                    logger.error("Gemini %s error on tools turn: %s", model, err_msg)
                    raise ValueError(f"Gemini error ({resp.status_code}): {err_msg}")

                except httpx.RequestError as exc:
                    logger.warning("Gemini %s connection error: %s", model, exc)
                    last_error = str(exc)
                    continue

            if not step_executed:
                raise RuntimeError(f"Не удалось выполнить шаг Function Calling. Последняя ошибка: {last_error}")

    return "Не удалось сформировать ответ за допустимое число шагов.", last_model, functions_called


def generate_owner_ai_response(
    company,
    settings,
    user_question: str,
    history: Optional[List[dict]] = None,
    is_voice: bool = False,
) -> Tuple[str, str, List[str]]:
    """
    Генерирует ответ владельцу с использованием аналитических функций F1-F20.
    Проверяет дневной лимит запросов ai_daily_limit.
    """
    from django.core.cache import cache
    from django.utils import timezone
    from apps.main.telegram_bot.services.ai_analytics_functions import (
        AI_TOOL_DECLARATIONS,
        execute_ai_function,
    )

    # 1. Проверка дневного лимита запросов
    today_str = timezone.localdate().isoformat()
    cache_key = f"tg_ai_daily:{company.id}:{today_str}"
    daily_limit = getattr(settings, "ai_daily_limit", 200) or 200

    try:
        current_count = cache.get(cache_key) or 0
        if current_count >= daily_limit:
            return (
                f"⚠️ Дневной лимит запросов к ИИ исчерпан ({daily_limit} запросов на сегодня).\n"
                "Бот продолжает отвечать на стандартные команды из меню /help.",
                "limit_exceeded",
                [],
            )
        cache.set(cache_key, current_count + 1, timeout=86400)
    except Exception as exc:
        logger.warning("Failed to check AI daily limit in cache: %s", exc)

    # 2. Получение ключа
    api_key = get_effective_ai_key(settings.ai_key)
    if not api_key:
        return (
            "Ключ Google Gemini не настроен. Настройте его в панели управления или используйте команды /help.",
            "no_key",
            [],
        )

    # 3. Формирование подсказки (systemInstruction)
    today = timezone.localdate()
    now = timezone.now()
    tz_name = getattr(timezone.get_current_timezone(), "zone", "Asia/Bishkek")
    company_name = getattr(company, "name", "магазина")

    voice_instruction = (
        "\nПОЛЬЗОВАТЕЛЬ ЗАДАЛ ВОПРОС ГОЛОСОМ: Отвечай предельно кратко, в 1–3 предложениях! "
        "Только суть и главный итог цифр без длинных списков."
        if is_voice else ""
    )

    system_instruction = (
        f"Ты персональный финансовый и операционный аналитик магазина «{company_name}» в системе NurCRM.\n"
        f"Сегодняшняя дата: {today.strftime('%d.%m.%Y')} ({today.strftime('%A')}). Текущее время и часовой пояс: {now.strftime('%H:%M')} ({tz_name}).\n\n"
        "СТРОГИЕ ПРАВИЛА:\n"
        "1. Ты ОБЯЗАН вызывать соответствующие функции (get_sales_summary, get_pnl, get_cashflow, get_stock, get_debtors и др.) для получения точных цифр!\n"
        "2. ЦИФРЫ — ТОЛЬКО ИЗ РЕЗУЛЬТАТОВ ВЫЗОВА ФУНКЦИЙ! Категорически запрещено выдумывать или предполагать любые суммы, остатки, проценты или количество.\n"
        "3. Если функция вернула ошибку или данных нет — честно скажи: «нет данных» и подскажи команду из меню /help.\n"
        "4. Отвечай на том языке, на котором задан вопрос (русский или кыргызский).\n"
        "5. Формат для Telegram:\n"
        "   - Короткие понятные строки.\n"
        "   - Списки с маркером «•».\n"
        "   - Суммы форматируй с пробелом между тысячами и словом «сом» (например: «12 450 сом»).\n"
        "   - НЕ используй markdown-таблицы (они ломаются на смартфонах).\n"
        "6. Если отчёт длиннее 15 строк — приведи главные цифры и спроси «Показать подробнее?».\n"
        f"{voice_instruction}"
    )

    contents: List[dict] = []
    if history:
        for h in history[-6:]:
            contents.append(h)
    contents.append({"role": "user", "parts": [{"text": user_question}]})

    return generate_chat_response_with_tools(
        api_key=api_key,
        system_instruction=system_instruction,
        contents=contents,
        tools=AI_TOOL_DECLARATIONS,
        execute_tool_fn=lambda name, args: execute_ai_function(company, name, args),
        temperature=0.2,
        max_tokens=900,
    )


def transcribe_voice(api_key: str, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """
    Распознает голосовое сообщение через Gemini (inline_data).
    """
    if not api_key:
        return ""

    b64_audio = base64.b64encode(audio_bytes).decode("ascii")
    contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": (
                        "Транскрибируй аудиосообщение на русском или кыргызском языке. "
                        "Верни ТОЛЬКО распознанный текст без кавычек, пояснений и вводных слов."
                    )
                },
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": b64_audio,
                    }
                },
            ],
        }
    ]

    try:
        text, _ = generate_chat_response(
            api_key=api_key,
            system_instruction="Ты профессиональный транскрибатор речи.",
            contents=contents,
            temperature=0.0,
            max_tokens=500,
        )
        return text.strip()
    except Exception as exc:
        logger.error("Voice transcription failed: %s", exc)
        return ""


def test_ai(api_key: str) -> dict:
    """
    Пробный запрос к ИИ для эндпоинта test-ai/.
    """
    contents = [
        {"role": "user", "parts": [{"text": "Тест связи. Ответь одним словом: Работает."}]}
    ]
    try:
        ans, model = generate_chat_response(
            api_key=api_key,
            system_instruction="Отвечай кратко.",
            contents=contents,
            temperature=0.1,
            max_tokens=50,
        )
        if not ans or not str(ans).strip():
            return {"ok": False, "error": "ИИ вернул пустой ответ. Empty response."}
        return {"ok": True, "answer": ans, "model": model}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
