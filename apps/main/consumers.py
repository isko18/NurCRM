"""
WebSocket-консьюмер уведомлений (doc 08).

URL: ``ws/notifications/?token=<JWT>`` (JWT кладёт user в scope, см. core/ws_jwt.py).

Подключение подписывается на минимально необходимый набор групп:
  * notif_user_<user_id>     — личные уведомления (основной канал доставки);
  * notif_company_<id>       — общая компания;
  * notif_branch_<id>        — филиал (если есть);
  * notif_role_<role>        — роль (если есть);
  * notif_agent_<id>         — если пользователь-агент.
"""
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from core.ws_consumer_utils import (
    is_anonymous_scope_user,
    reject_websocket_unauthorized,
    reject_websocket_forbidden,
)
from apps.cafe.consumers import resolve_user_company_and_branch
from apps.main.realtime import (
    user_group_name,
    company_group_name,
    branch_group_name,
    role_group_name,
    agent_group_name,
)

logger = logging.getLogger("nurcrm.websocket.notifications")


class NotificationsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if is_anonymous_scope_user(self.scope):
            await reject_websocket_unauthorized(self)  # close 4401 → фронт обновит токен
            return

        user = self.scope["user"]
        company, branch = await self._get_company_and_branch(user)
        if not company:
            await reject_websocket_forbidden(self, reason="no_company")
            return

        self.user_id = str(user.id)
        self.company_id = str(company.id)
        self.branch_id = str(branch.id) if branch else None
        role = getattr(user, "role", None)

        self.groups_subscribed = [
            user_group_name(self.user_id),
            company_group_name(self.company_id),
        ]
        if self.branch_id:
            self.groups_subscribed.append(branch_group_name(self.branch_id))
        if role:
            self.groups_subscribed.append(role_group_name(role))
        if role == "agent":
            self.groups_subscribed.append(agent_group_name(self.user_id))

        for group in self.groups_subscribed:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

        await self.send(json.dumps({
            "type": "connection_established",
            "user_id": self.user_id,
        }))

    async def disconnect(self, code):
        for group in getattr(self, "groups_subscribed", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except Exception:
            return
        if data.get("action") == "ping":
            await self.send(json.dumps({"type": "pong"}))

    # ---- доставка уведомления (group_send type="notify") ----
    async def notify(self, event):
        await self.send(json.dumps({
            "type": "notification",
            "data": event.get("data") or {},
        }))

    @database_sync_to_async
    def _get_company_and_branch(self, user):
        return resolve_user_company_and_branch(user)
