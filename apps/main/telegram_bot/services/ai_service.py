import base64
import logging
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


def get_effective_ai_key(company_key: str = None) -> str:
    """Возвращает ключ ИИ компании или глобальный GEMINI_API_KEY из settings."""
    if company_key:
        return company_key
    return getattr(settings, "GEMINI_API_KEY", "") or ""


def generate_chat_response(
    api_key: str,
    system_instruction: str,
    contents: List[dict],
    temperature: float = 0.4,
    max_tokens: int = 1000,
) -> Tuple[str, str]:
    """
    Генерирует ответ через Gemini REST API.
    При ошибках 5xx/503/429/timeout переходит к следующей модели из списка.
    Возвращает (текст_ответа, имя_модели).
    """
    if not api_key:
        raise ValueError("Google Gemini API key не настроен.")

    last_error = None
    with httpx.Client(timeout=15.0) as client:
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

            try:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts).strip()
                        return text, model
                    return "", model

                # Если ошибка 5xx или 429 — пробуем следующую модель
                if resp.status_code >= 500 or resp.status_code == 429:
                    logger.warning("Gemini model %s returned HTTP %s, trying next model", model, resp.status_code)
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
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
        return {"ok": True, "answer": ans, "model": model}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
