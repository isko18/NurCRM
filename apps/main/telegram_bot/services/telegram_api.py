import logging
import httpx

logger = logging.getLogger("telegram_bot.api")
TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramAPIError(Exception):
    def __init__(self, message: str, status_code: int = None, description: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.description = description


def get_me(token: str) -> dict:
    """Проверяет токен бота через getMe."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/getMe"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            data = resp.json()
            if not data.get("ok"):
                desc = data.get("description", "Unauthorized")
                raise TelegramAPIError(f"Telegram: {desc}", status_code=resp.status_code, description=desc)
            return data.get("result", {})
    except httpx.RequestError as exc:
        logger.error("getMe request failed: %s", exc)
        raise TelegramAPIError(f"Ошибка соединения с Telegram: {exc}")


def set_webhook(token: str, webhook_url: str, secret_token: str) -> dict:
    """Устанавливает вебхук на сервере с secret_token."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/setWebhook"
    payload = {
        "url": webhook_url,
        "secret_token": secret_token,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            data = resp.json()
            if not data.get("ok"):
                desc = data.get("description", "Failed to set webhook")
                raise TelegramAPIError(f"Telegram: {desc}", status_code=resp.status_code, description=desc)
            return data
    except httpx.RequestError as exc:
        logger.error("setWebhook request failed: %s", exc)
        raise TelegramAPIError(f"Ошибка установки webhook: {exc}")


def delete_webhook(token: str) -> dict:
    """Удаляет вебхук."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/deleteWebhook"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json={"drop_pending_updates": False})
            return resp.json()
    except Exception as exc:
        logger.error("deleteWebhook error: %s", exc)
        return {"ok": False, "description": str(exc)}


def send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML", reply_markup: dict = None) -> dict:
    """Отправляет текстовое сообщение в чат."""
    if not token or not chat_id or not text:
        return {"ok": False, "description": "Empty token, chat_id or text"}

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": str(chat_id),
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.post(url, json=payload)
            data = resp.json()
            if not data.get("ok") and parse_mode:
                # Попробуем без parse_mode, если ошибка парсинга HTML/Markdown
                payload.pop("parse_mode", None)
                resp2 = client.post(url, json=payload)
                return resp2.json()
            return data
    except Exception as exc:
        logger.error("sendMessage error for chat %s: %s", chat_id, exc)
        return {"ok": False, "description": str(exc)}


def send_voice(token: str, chat_id: str, voice_bytes: bytes, caption: str = None) -> dict:
    """Отправляет голосовое сообщение (OGG/Opus)."""
    if not token or not chat_id or not voice_bytes:
        return {"ok": False, "description": "Empty token, chat_id or voice_bytes"}

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendVoice"
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1024]

    files = {"voice": ("voice.ogg", voice_bytes, "audio/ogg")}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, data=data, files=files)
            return resp.json()
    except Exception as exc:
        logger.error("sendVoice error for chat %s: %s", chat_id, exc)
        return {"ok": False, "description": str(exc)}


def get_file(token: str, file_id: str) -> dict:
    """Получает информацию о файле по file_id."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/getFile"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, params={"file_id": file_id})
            data = resp.json()
            if data.get("ok"):
                return data.get("result", {})
            return {}
    except Exception as exc:
        logger.error("getFile error: %s", exc)
        return {}


def download_file(token: str, file_path: str) -> bytes:
    """Скачивает файл из Telegram по file_path."""
    url = f"{TELEGRAM_API_BASE}/file/bot{token}/{file_path}"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.content
            return b""
    except Exception as exc:
        logger.error("download_file error: %s", exc)
        return b""
