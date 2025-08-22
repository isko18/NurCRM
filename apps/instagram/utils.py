from instagrapi import Client
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import os

channel_layer = get_channel_layer()
SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)


def get_client(company_id, username):
    session_file = os.path.join(SESSIONS_DIR, f"{company_id}_{username}.json")
    cl = Client()

    if os.path.exists(session_file):
        cl.load_settings(session_file)

    # перехватываем входящие
    def handler(msg):
        text = msg.text
        user_id = msg.user_id
        async_to_sync(channel_layer.group_send)(
            f"ig_{company_id}",
            {
                "type": "ig.message",
                "message": {"user_id": user_id, "text": text, "direction": "in"},
            },
        )
    cl.on_direct_message(handler)

    return cl
