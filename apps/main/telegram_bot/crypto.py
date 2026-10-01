import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet() -> Fernet:
    secret = getattr(settings, "SECRET_KEY", "nurcrm-default-secret-key-32bytes")
    # 32-byte urlsafe base64 key
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    """Шифрует секрет (токен или ключ ИИ) в строку base64."""
    if not value:
        return ""
    try:
        f = _get_fernet()
        return f.encrypt(value.encode("utf-8")).decode("ascii")
    except Exception:
        return ""


def decrypt_secret(encrypted_value: str) -> str:
    """Расшифровывает секрет в исходную строку."""
    if not encrypted_value:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(encrypted_value.encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""
