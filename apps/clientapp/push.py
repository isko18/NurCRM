"""Уведомления через Expo Push (https://exp.host/--/api/v2/push/send)."""
import logging
from decimal import Decimal

import httpx

from apps.main.models import ClientBonusTransaction
from apps.main.phone_utils import normalize_phone_e164

from .models import AppCustomer, AppPushToken

logger = logging.getLogger("clientapp.push")
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_CHUNK = 100


def format_points(value: Decimal, lang: str) -> str:
    d = abs(Decimal(str(value))).quantize(Decimal("0.01")).normalize()
    is_int = d == d.to_integral_value()
    text = f"{int(d)}" if is_int else f"{d:f}".replace(".", ",")
    if lang == "ky":
        return f"{text} упай"
    if not is_int:
        return f"{text} балла"
    n = int(d)
    if n % 10 == 1 and n % 100 != 11:
        word = "балл"
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = "балла"
    else:
        word = "баллов"
    return f"{text} {word}"


def bonus_message(shop_name: str, delta: Decimal, reason: str, lang: str):
    pts = format_points(delta, lang)
    if lang == "ky":
        title = f"«{shop_name}» дүкөнү"
        body = f"{pts} кошулду" if delta > 0 else f"{pts} колдонулду"
        return title, f"{title}: {body}"
    title = f"Магазин «{shop_name}»"
    body = f"начислено {pts}" if delta > 0 else f"списано {pts}"
    return title, f"{title}: {body}"


def send_expo(messages):
    """Отправляет сообщения, удаляет токены с DeviceNotRegistered. Возвращает число успешных."""
    ok = 0
    for i in range(0, len(messages), EXPO_CHUNK):
        chunk = messages[i:i + EXPO_CHUNK]
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    EXPO_PUSH_URL,
                    json=chunk,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
            data = resp.json().get("data") or []
        except Exception as exc:
            logger.warning("Expo push failed: %s", exc)
            continue
        if isinstance(data, dict):
            data = [data]
        dead = []
        for msg, ticket in zip(chunk, data):
            if (ticket or {}).get("status") == "ok":
                ok += 1
                continue
            details = (ticket or {}).get("details") or {}
            if details.get("error") == "DeviceNotRegistered":
                dead.append(msg["to"])
            else:
                logger.info("Expo push error: %s", ticket)
        if dead:
            AppPushToken.objects.filter(token__in=dead).delete()
    return ok


def push_to_customer(customer: AppCustomer, title: str, body: str, data: dict = None):
    tokens = list(customer.push_tokens.values_list("token", flat=True))
    if not tokens:
        return 0
    messages = [
        {"to": t, "title": title, "body": body, "sound": "default", "data": data or {}, "priority": "high"}
        for t in tokens
    ]
    return send_expo(messages)


def push_for_bonus_tx(tx_id):
    tx = (
        ClientBonusTransaction.objects.select_related("client", "company", "sale__branch")
        .filter(pk=tx_id)
        .first()
    )
    if tx is None or tx.sale_id is None or tx.reason not in (
        ClientBonusTransaction.Reason.EARN,
        ClientBonusTransaction.Reason.REDEEM,
    ):
        return 0
    phone = normalize_phone_e164(tx.client.phone)
    if not phone:
        return 0
    customer = AppCustomer.objects.filter(phone=phone, deleted_at__isnull=True).first()
    if customer is None:
        return 0
    branch = tx.sale.branch if tx.sale_id else None
    shop_name = f"{tx.company.name} — {branch.name}" if branch is not None else tx.company.name
    title, body = bonus_message(shop_name, tx.delta, tx.reason, customer.lang)
    data = {
        "type": "bonus",
        "companyId": str(tx.company_id),
        "purchaseId": str(tx.sale_id),
        "delta": str(tx.delta),
        "balance": str(tx.balance_after),
    }
    return push_to_customer(customer, title, body, data)
