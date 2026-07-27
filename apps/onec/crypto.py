import base64
import hashlib
import hmac

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet() -> Fernet:
    raw = (settings.SECRET_KEY + "|apps.onec").encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()


def verify_hmac_sha256(secret: str, raw_body: bytes, signature_header: str) -> bool:
    """
    Проверить подпись входящего callback'а.

    signature_header — значение заголовка X-OneC-Signature вида "sha256=<hex>".
    Сравнение постоянного времени (защита от timing-атак).
    """
    if not secret or not signature_header:
        return False
    provided = signature_header.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[7:]
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
