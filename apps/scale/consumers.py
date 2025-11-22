# apps/main/consumers.py
import json
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncJsonWebsocketConsumer

# если будешь проверять токен через БД:
from asgiref.sync import sync_to_async
from apps.users.models import User  # или твоя модель агента / юзера


@sync_to_async
def _check_scale_token(agent_id, token: str) -> bool:
    """
    Тут сам реши, где хранится токен:
    - user.scale_token
    - company.scale_token и связь user->company
    - отдельная модель ScaleToken
    """
    try:
        user = User.objects.get(id=agent_id)
    except User.DoesNotExist:
        return False

    # пример: токен на компании
    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if not company:
        return False

    # допустим, у компании поле scale_api_token
    return bool(company.scale_api_token and company.scale_api_token == token)


class AgentScaleConsumer(AsyncJsonWebsocketConsumer):
    """
    ws://app.nurcrm.kg/ws/agents/<uuid:agent_id>/?token=<scale_token>

    Сервер шлёт в группу события типа:
      {"type": "send_plu_batch", "payload": {...}}
    А сюда прилетает send_plu_batch(...)
    """

    async def connect(self):
        self.agent_id = str(self.scope["url_route"]["kwargs"]["agent_id"])

        # достаём token из query params
        query_string = self.scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        token = (params.get("token") or [None])[0]

        if not token or not await _check_scale_token(self.agent_id, token):
            # не пускаем
            await self.close(code=4001)
            return

        self.group_name = f"agent_scale_{self.agent_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """
        Агент может что-то присылать обратно (например, статус заливки),
        но это опционально. Пока можно игнорировать.
        """
        # print("from agent:", content)
        pass

    # ====== обработчик групповго события от Django ======
    async def send_plu_batch(self, event):
        """
        event = {"type": "send_plu_batch", "payload": {...}}
        """
        payload = event.get("payload") or {}
        await self.send_json(payload)
