"""WebSocket-консьюмер канбана воронки consalting.

URL: ``ws/consalting/funnel/?token=<JWT>`` (JWT кладёт user в scope, см. core/ws_jwt.py)

Каждый подключённый сотрудник подписывается на:
  * ``consalting_company_<company_id>`` — карточные события доски (создание лида,
    смена стадии, взятие/возврат, удаление). Видимость «взятый лид виден только
    владельцу + руководителям» применяется здесь, при доставке.
  * ``consalting_user_<user_id>`` — персональные уведомления (например,
    «лид назначен вам»).
"""
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

from core.ws_consumer_utils import (
    is_anonymous_scope_user,
    reject_websocket_forbidden,
    reject_websocket_unauthorized,
)
from apps.cafe.consumers import resolve_user_company_and_branch
from .access import is_owner_like

logger = logging.getLogger("nurcrm.websocket.consalting")


class ConsaltingFunnelConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if is_anonymous_scope_user(self.scope):
            await reject_websocket_unauthorized(self)
            return

        user = self.scope["user"]
        company, branch = await self._get_company_and_branch(user)
        if not company:
            logger.warning(
                "websocket connect forbidden consumer=ConsaltingFunnelConsumer user_id=%s reason=no_company",
                user.id,
            )
            await reject_websocket_forbidden(self, reason="no_company")
            return

        self.user_id = str(user.id)
        self.company_id = str(company.id)
        self.branch_id = str(branch.id) if branch else None
        self.is_manager = await self._is_owner_like(user)
        # множество воронок, видимых сотруднику (для фильтра событий доски)
        self.visible_funnel_ids = await self._visible_funnel_ids(user)

        self.company_group = f"consalting_company_{self.company_id}"
        self.user_group = f"consalting_user_{self.user_id}"

        await self.channel_layer.group_add(self.company_group, self.channel_name)
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()

        logger.debug(
            "websocket connected consumer=ConsaltingFunnelConsumer user_id=%s company_id=%s manager=%s",
            self.user_id, self.company_id, self.is_manager,
        )

        await self.send(json.dumps({
            "type": "connection_established",
            "company_id": self.company_id,
            "branch_id": self.branch_id,
            "user_id": self.user_id,
            "is_manager": self.is_manager,
        }))

    async def disconnect(self, code):
        for group in (getattr(self, "company_group", None), getattr(self, "user_group", None)):
            if group:
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

    # ---- карточные события доски (с фильтром видимости) ----
    async def consalting_event(self, event):
        """Доставка карточного события с учётом видимости текущему сотруднику.

        Если лид «взят» другим сотрудником (owner задан и != текущий) и
        получатель не руководитель — карточка должна пропасть с его доски:
        шлём ``lead.removed`` с id, чтобы фронт убрал колоночную карточку.
        """
        ev = event.get("event")
        payload = event.get("payload") or {}
        owner = payload.get("owner")
        funnel = payload.get("funnel")

        # фильтр по видимости воронки (раздел 1.6): руководитель видит всё
        if not self.is_manager and funnel and funnel not in self.visible_funnel_ids:
            return

        if owner and not self.is_manager and owner != self.user_id:
            await self.send(json.dumps({
                "type": "lead.removed",
                "event": ev,
                "data": {"id": payload.get("id"), "funnel": payload.get("funnel")},
            }))
            return

        await self.send(json.dumps({"type": ev, "data": payload}))

    # ---- персональные уведомления (без фильтра) ----
    async def consalting_notify(self, event):
        await self.send(json.dumps({
            "type": event.get("event"),
            "data": event.get("payload") or {},
        }))

    # ---- события Wazzup сообщений ----
    async def wazzup_event(self, event):
        """Доставка событий Wazzup чата (новые сообщения и статусы)."""
        ev_data = event.get("event") or {}
        await self.send(json.dumps(ev_data))

    # ---- системные уведомления ----
    async def notify(self, event):
        await self.send(json.dumps({
            "type": "notification",
            "data": event.get("data") or {},
        }))

    @database_sync_to_async
    def _get_company_and_branch(self, user):
        return resolve_user_company_and_branch(user)

    @database_sync_to_async
    def _is_owner_like(self, user):
        return is_owner_like(user)

    @database_sync_to_async
    def _visible_funnel_ids(self, user):
        from .access import visible_funnels_qs
        from .models import FunnelConsalting
        if is_owner_like(user):
            return set()  # руководитель видит всё — фильтр не применяется
        qs = visible_funnels_qs(FunnelConsalting.objects.all(), user)
        return {str(fid) for fid in qs.values_list("id", flat=True)}
