import requests
import uuid

WAZZUP_API_TOKEN = "28ff3c3fcfd84435a9c6bee997acadce"
BASE_URL = "https://api.wazzup24.com/v3"
HEADERS = {
    "Authorization": f"Bearer {WAZZUP_API_TOKEN}",
    "Content-Type": "application/json"
}


def get_channel_id_by_plain_id(plain_id: str, transport: str = "whatsapp") -> str:
    """
    Получает channelId по plainId (номер или username) и типу транспорта.
    """
    url = f"{BASE_URL}/channels"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    channels = response.json()

    for ch in channels:
        if (
            ch["transport"] == transport
            and ch["state"] == "active"
            and ch.get("plainId") == plain_id
        ):
            return ch["channelId"]

    raise ValueError(f"Не найден активный канал с plainId={plain_id} и transport={transport}")


def get_first_active_channel_id(transport: str = "whatsapp") -> str:
    """
    Возвращает первый активный канал нужного транспорта
    """
    url = f"{BASE_URL}/channels"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    channels = response.json()

    for ch in channels:
        if ch["transport"] == transport and ch["state"] == "active":
            return ch["channelId"]

    raise ValueError(f"Нет активного канала для transport={transport}")


def send_message(
    *,
    channel_id: str = None,
    plain_id: str = None,
    transport: str = "whatsapp",
    chat_type: str,
    chat_id: str = None,
    phone: str = None,
    username: str = None,
    text: str = None,
    content_uri: str = None,
    crm_user_id: str = None,
    crm_message_id: str = None,
    ref_message_id: str = None,
    buttons: list = None,
    clear_unanswered: bool = True
):
    """
    Отправляет сообщение через Wazzup.
    Можно указать channel_id напрямую или plain_id (номер/username) + transport.
    """
    if not channel_id:
        if plain_id:
            channel_id = get_channel_id_by_plain_id(plain_id, transport)
        else:
            channel_id = get_first_active_channel_id(transport)

    if not chat_type:
        raise ValueError("chat_type обязателен")
    if not (chat_id or phone or username):
        raise ValueError("Нужно указать chat_id или phone или username")
    if not (text or content_uri):
        raise ValueError("Нужно указать text или content_uri")
    if text and content_uri:
        raise ValueError("Нельзя использовать одновременно text и content_uri")

    payload = {
        "channelId": channel_id,
        "chatType": chat_type,
        "clearUnanswered": clear_unanswered,
        "crmMessageId": crm_message_id or str(uuid.uuid4())
    }

    if chat_id:
        payload["chatId"] = chat_id
    elif phone:
        payload["chatId"] = phone
    elif username:
        payload["username"] = username

    if text:
        payload["text"] = text
    elif content_uri:
        payload["contentUri"] = content_uri

    if crm_user_id:
        payload["crmUserId"] = crm_user_id
    if ref_message_id:
        payload["refMessageId"] = ref_message_id
    if buttons:
        payload["buttonsObject"] = {"buttons": buttons}

    url = f"{BASE_URL}/message"
    response = requests.post(url, json=payload, headers=HEADERS)
    return response.json()


def edit_message(message_id: str, text: str = None, content_uri: str = None, crm_user_id: str = None):
    """
    Редактирует отправленное сообщение.
    """
    if not (text or content_uri):
        raise ValueError("Нужно передать text или content_uri")
    if text and content_uri:
        raise ValueError("Нельзя редактировать text и content_uri одновременно")

    payload = {}
    if text:
        payload["text"] = text
    if content_uri:
        payload["contentUri"] = content_uri
    if crm_user_id:
        payload["crmUserId"] = crm_user_id

    url = f"{BASE_URL}/message/{message_id}"
    response = requests.patch(url, json=payload, headers=HEADERS)
    return response.json()


def delete_message(message_id: str):
    """
    Удаляет сообщение.
    """
    url = f"{BASE_URL}/message/{message_id}"
    response = requests.delete(url, headers=HEADERS)
    return {"status": response.status_code, "body": response.text}
