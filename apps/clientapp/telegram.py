"""
Глобальный бот входа в приложение клиентов (один на платформу, не боты компаний).

Env: CLIENT_APP_TELEGRAM_BOT_TOKEN, CLIENT_APP_TELEGRAM_WEBHOOK_SECRET, CLIENT_APP_BOT_USERNAME.
Поток: /start <nonce> → кнопка «Поделиться контактом» → contact (contact.user_id == from.id) → nonce ok.
"""
import logging
import re
from datetime import timedelta

import httpx
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.main.phone_utils import normalize_phone_e164

from .models import AppCustomer, TelegramAuthNonce, hash_secret

logger = logging.getLogger("clientapp.telegram")
TELEGRAM_API_BASE = "https://api.telegram.org"
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
PHONE_CONFIRM_LIMIT_PER_HOUR = 10

TEXTS = {
    "ask_contact": (
        "Чтобы войти в приложение, нажмите кнопку «Поделиться контактом» ниже.\n"
        "Тиркемеге кирүү үчүн төмөнкү «Байланышты бөлүшүү» баскычын басыңыз."
    ),
    "button": "📱 Поделиться контактом / Байланышты бөлүшүү",
    "no_nonce": (
        "Откройте вход через приложение — там будет ссылка на этого бота.\n"
        "Кирүүнү тиркеме аркылуу ачыңыз."
    ),
    "expired": "Ссылка для входа устарела. Начните вход в приложении заново.\nШилтеменин мөөнөтү бүттү, кайра баштаңыз.",
    "foreign_contact": (
        "Можно подтвердить только свой номер: нажмите кнопку «Поделиться контактом».\n"
        "Өзүңүздүн номериңизди гана ырастай аласыз."
    ),
    "bad_phone": "Не удалось распознать номер телефона.\nТелефон номери таанылган жок.",
    "too_many": "Слишком много попыток. Попробуйте через час.\nАракет өтө көп, бир сааттан кийин кайталаңыз.",
    "ok": "Готово! Номер подтверждён, вернитесь в приложение.\nДаяр! Номер ырасталды, тиркемеге кайтыңыз.",
}


def bot_token():
    return (getattr(settings, "CLIENT_APP_TELEGRAM_BOT_TOKEN", "") or "").strip()


def bot_username():
    return (getattr(settings, "CLIENT_APP_BOT_USERNAME", "") or "").strip().lstrip("@")


def webhook_secret():
    return (getattr(settings, "CLIENT_APP_TELEGRAM_WEBHOOK_SECRET", "") or "").strip()


def is_configured() -> bool:
    return bool(bot_token() and bot_username())


def api_call(method: str, payload: dict, timeout: float = 8.0):
    token = bot_token()
    if not token:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{TELEGRAM_API_BASE}/bot{token}/{method}", json=payload)
            data = resp.json()
            if not data.get("ok"):
                logger.warning("Telegram %s failed: %s", method, data.get("description"))
            return data
    except Exception as exc:
        logger.warning("Telegram %s error: %s", method, exc)
        return None


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return api_call("sendMessage", payload)


def contact_keyboard():
    return {
        "keyboard": [[{"text": TEXTS["button"], "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def remove_keyboard():
    return {"remove_keyboard": True}


def set_webhook(url: str):
    payload = {
        "url": url,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    }
    if webhook_secret():
        payload["secret_token"] = webhook_secret()
    return api_call("setWebhook", payload, timeout=15.0)


# ----------------------------------------------------------------------
# Обработка обновлений
# ----------------------------------------------------------------------


def _phone_rate_limited(phone: str) -> bool:
    key = f"capp:tg_phone:{hash_secret(phone)[:32]}"
    try:
        cache.add(key, 0, timeout=3600)
        return cache.incr(key) > PHONE_CONFIRM_LIMIT_PER_HOUR
    except Exception:
        return False


def handle_update(update: dict) -> str:
    """Возвращает короткий код результата (для логов/тестов)."""
    message = update.get("message") or {}
    if not message:
        return "ignored"
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    from_id = sender.get("id")
    if chat_id is None or from_id is None or sender.get("is_bot"):
        return "ignored"
    if chat.get("type") not in (None, "private"):
        return "ignored"

    if message.get("contact"):
        return _handle_contact(chat_id, from_id, message["contact"])

    text = (message.get("text") or "").strip()
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        nonce = parts[1].strip() if len(parts) > 1 else ""
        return _handle_start(chat_id, from_id, nonce)
    send_message(chat_id, TEXTS["no_nonce"])
    return "no_nonce"


def _handle_start(chat_id, from_id, nonce: str) -> str:
    if not nonce or not NONCE_RE.match(nonce):
        send_message(chat_id, TEXTS["no_nonce"])
        return "no_nonce"
    with transaction.atomic():
        obj = TelegramAuthNonce.objects.select_for_update().filter(nonce=nonce).first()
        if obj is None or obj.status != TelegramAuthNonce.Status.PENDING or obj.is_expired:
            if obj is not None and obj.status == TelegramAuthNonce.Status.PENDING:
                obj.status = TelegramAuthNonce.Status.EXPIRED
                obj.save(update_fields=["status"])
            send_message(chat_id, TEXTS["expired"])
            return "expired"
        if obj.telegram_user_id and obj.telegram_user_id != from_id:
            # nonce уже привязан к другому аккаунту Telegram
            send_message(chat_id, TEXTS["expired"])
            return "foreign_nonce"
        obj.telegram_user_id = from_id
        obj.save(update_fields=["telegram_user_id"])
    send_message(chat_id, TEXTS["ask_contact"], reply_markup=contact_keyboard())
    return "ask_contact"


def _handle_contact(chat_id, from_id, contact: dict) -> str:
    if contact.get("user_id") != from_id:
        send_message(chat_id, TEXTS["foreign_contact"], reply_markup=contact_keyboard())
        return "foreign_contact"
    phone = normalize_phone_e164(contact.get("phone_number"))
    if not phone:
        send_message(chat_id, TEXTS["bad_phone"])
        return "bad_phone"
    now = timezone.now()
    nonce = (
        TelegramAuthNonce.objects.filter(
            telegram_user_id=from_id, status=TelegramAuthNonce.Status.PENDING, expires_at__gt=now
        )
        .order_by("-created_at")
        .first()
    )
    if nonce is None:
        send_message(chat_id, TEXTS["expired"], reply_markup=remove_keyboard())
        return "expired"
    if _phone_rate_limited(phone):
        send_message(chat_id, TEXTS["too_many"], reply_markup=remove_keyboard())
        return "rate_limited"

    with transaction.atomic():
        locked = TelegramAuthNonce.objects.select_for_update().get(pk=nonce.pk)
        if locked.status != TelegramAuthNonce.Status.PENDING or locked.is_expired:
            send_message(chat_id, TEXTS["expired"], reply_markup=remove_keyboard())
            return "expired"
        customer = _get_or_create_customer(phone, locked, from_id)
        locked.status = TelegramAuthNonce.Status.OK
        locked.customer = customer
        locked.confirmed_at = now
        locked.save(update_fields=["status", "customer", "confirmed_at"])
    send_message(chat_id, TEXTS["ok"], reply_markup=remove_keyboard())
    return "ok"


def _get_or_create_customer(phone, nonce, from_id):
    from .services import phone_hash

    customer = AppCustomer.objects.select_for_update().filter(phone=phone).first()
    if customer is None:
        try:
            with transaction.atomic():
                customer = AppCustomer.objects.create(
                    phone=phone,
                    phone_hash=phone_hash(phone),
                    full_name=nonce.full_name or "",
                    birth_date=nonce.birth_date,
                    telegram_user_id=from_id,
                )
        except IntegrityError:
            customer = AppCustomer.objects.get(phone=phone)
        customer.ensure_referral_code()
        return customer
    changed = []
    if customer.telegram_user_id != from_id:
        customer.telegram_user_id = from_id
        changed.append("telegram_user_id")
    if not customer.full_name and nonce.full_name:
        customer.full_name = nonce.full_name
        changed.append("full_name")
    if not customer.birth_date and nonce.birth_date:
        customer.birth_date = nonce.birth_date
        changed.append("birth_date")
    if changed:
        customer.save(update_fields=changed + ["updated_at"])
    return customer


def expire_old_nonces():
    cutoff = timezone.now()
    n = TelegramAuthNonce.objects.filter(status=TelegramAuthNonce.Status.PENDING, expires_at__lte=cutoff).update(
        status=TelegramAuthNonce.Status.EXPIRED
    )
    # старые записи удаляем через сутки — в них ФИО/дата рождения
    TelegramAuthNonce.objects.filter(created_at__lt=cutoff - timedelta(days=1)).delete()
    return n
