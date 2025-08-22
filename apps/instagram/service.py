import uuid
import datetime
from typing import Optional, Dict, List

from django.utils import timezone
from django.utils.timezone import is_naive, make_aware
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

from .models import CompanyIGAccount


def _to_dt(ts) -> Optional[datetime.datetime]:
    """
    Приводим Instagram timestamp к aware-datetime (local TZ).
    Поддерживаем seconds / ms / µs и datetime.
    """
    if not ts:
        return None
    if isinstance(ts, datetime.datetime):
        return make_aware(ts) if is_naive(ts) else ts

    s = str(ts)
    if not s.isdigit():
        return None

    n = int(s)
    # эвристика масштаба
    if n > 10**14:       # microseconds
        dt = datetime.datetime.fromtimestamp(n / 1_000_000)
    elif n > 10**11:     # milliseconds
        dt = datetime.datetime.fromtimestamp(n / 1_000)
    else:                # seconds
        dt = datetime.datetime.fromtimestamp(n)
    return make_aware(dt)


class IGChatService:
    """
    Лёгкий «без БД» сервис для Direct:
    - авторизация/возврат сессии
    - live-инбокс
    - чтение/отправка сообщений
    """
    def __init__(self, account: CompanyIGAccount):
        self.account = account
        self.cl = Client()

        # ускоряем клиент: уменьшаем внутренние рандомные задержки
        # (instagrapi поддерживает .delay_range как [min, max] в секундах)
        try:
            self.cl.delay_range = [0.2, 0.5]
        except Exception:
            pass

        if isinstance(account.settings_json, dict) and account.settings_json:
            self.cl.set_settings(account.settings_json)

    # ---------- session helpers ----------
    def _persist_ok(self):
        self.account.mark_logged_in(settings=self.cl.get_settings())

    def try_resume_session(self) -> bool:
        if not self.account.settings_json:
            return False
        try:
            self.cl.account_info()  # приватный вызов — валидирует куки/uuid/ua
            self._persist_ok()
            return True
        except LoginRequired:
            return False
        except Exception:
            # не палим стек: просто считаем, что нужна переавторизация
            return False

    def login_manual(self, password: str, verification_code: Optional[str] = None) -> bool:
        if not password:
            raise ValueError("Password is required")
        self.cl.login(self.account.username, password, verification_code=verification_code)
        # опционально проверим, что сессия рабочая
        self.cl.account_info()
        self._persist_ok()
        return True

    # ---------- thread users / usernames ----------
    def fetch_thread_users(self, thread_id: str) -> List[dict]:
        """
        Вернёт [{pk, username}, ...] участников треда (без записи в БД).
        """
        try:
            raw_thread = self.cl.private_request(f"direct_v2/threads/{thread_id}/", params={"limit": 1})
            users = (raw_thread.get("thread") or {}).get("users", []) or []
        except Exception:
            users = []
        out = []
        for u in users:
            if not isinstance(u, dict):
                continue
            pk = u.get("pk")
            if not pk:
                continue
            out.append({
                "pk": str(pk),
                "username": (u.get("username") or "").strip()
            })
        return out

    def thread_user_map(self, thread_id: str) -> Dict[str, str]:
        """Удобная мапа pk -> username"""
        return {u["pk"]: u["username"] for u in self.fetch_thread_users(thread_id)}

    # ---------- LIVE helpers ----------
    def fetch_threads_live(self, amount: int = 20) -> List[dict]:
        """
        Быстрый инбокс без pydantic-парсинга (raw JSON через private_request).
        """
        threads_accum, cursor = [], None
        while len(threads_accum) < amount:
            params = {"limit": min(20, amount - len(threads_accum))}
            if cursor:
                params["cursor"] = cursor
            inbox = self.cl.private_request("direct_v2/inbox/", params=params)
            inbox_data = inbox.get("inbox", {}) if isinstance(inbox, dict) else {}
            threads = inbox_data.get("threads", []) or []
            threads_accum.extend(threads)
            cursor = inbox_data.get("oldest_cursor") or inbox_data.get("cursor")
            if not cursor or not threads:
                break

        out = []
        now_iso = timezone.now().isoformat()
        for th in threads_accum[:amount]:
            if not isinstance(th, dict):
                continue
            thread_id = th.get("thread_id") or th.get("thread_v2_id") or th.get("id")
            if not thread_id:
                continue
            last_iso = (_to_dt(th.get("last_activity_at") or th.get("last_activity_at_ms")) or timezone.now()).isoformat()
            users_raw = th.get("users") or []
            users = []
            for u in users_raw:
                if not isinstance(u, dict): continue
                pk = u.get("pk")
                if not pk: continue
                users.append({"pk": str(pk), "username": (u.get("username") or "").strip()})
            out.append({
                "thread_id": str(thread_id),
                "title": th.get("thread_title") or "",
                "users": users,
                "last_activity": last_iso or now_iso,
            })
        return out

    def fetch_messages_live(
        self,
        thread_id: str,
        limit: int = 30,
        *,
        user_map: Optional[Dict[str, str]] = None,
        include_usernames: bool = False,
    ) -> List[dict]:
        """
        Возвращает последние сообщения треда (только текстовые).
        Если include_usernames=True — проставит m['username'] из user_map
        (если user_map не передан — будет вызван fetch_thread_users один раз).
        """
        try:
            raw_thread = self.cl.private_request(f"direct_v2/threads/{thread_id}/", params={"limit": limit})
            items = (raw_thread.get("thread") or {}).get("items", []) or []
        except Exception:
            items = []

        # При необходимости получим пользователей один раз
        if include_usernames and user_map is None:
            user_map = self.thread_user_map(thread_id)

        out = []
        now_iso = timezone.now().isoformat()
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("item_type") != "text":
                continue

            sender_pk = str(it.get("user_id") or it.get("sender_id") or "")

            msg = {
                "mid": str(it.get("item_id") or it.get("client_context") or uuid.uuid4()),
                "text": (it.get("text") or "").strip(),
                "sender_pk": sender_pk,
                "created_at": (_to_dt(it.get("timestamp")) or timezone.now()).isoformat(),
                "direction": "in",
            }
            if include_usernames and user_map:
                msg["username"] = user_map.get(sender_pk, "")
            out.append(msg)

        # по возрастанию времени, чтобы фронт просто аппендил
        out.sort(key=lambda m: m["created_at"] or now_iso)
        return out

    def send_text(self, thread_id: str, text: str) -> dict:
        """
        Отправка текста в тред. Возвращаем «локальное» сообщение для мгновенного UI.
        """
        self.cl.direct_send(text, thread_ids=[thread_id])
        return {
            "mid": f"local-{uuid.uuid4()}",
            "text": text,
            "sender_pk": "me",
            "username": self.account.username,  # фронт сразу отрендерит отправителя
            "created_at": timezone.now().isoformat(),
            "direction": "out",
        }


# добавь в конец класса IGChatService

def fetch_last_text(self, thread_id: str, user_map: dict[str, str] | None = None) -> dict | None:
    """
    Возвращает превью последнего текстового сообщения треда:
    { mid, text, sender_pk, username?, created_at }
    """
    try:
        raw = self.cl.private_request(f"direct_v2/threads/{thread_id}/", params={"limit": 1})
        items = (raw.get("thread") or {}).get("items", []) or []
        if not items:
            return None
        it = items[0]
        if not isinstance(it, dict) or it.get("item_type") != "text":
            return None
        sender = str(it.get("user_id") or it.get("sender_id") or "")
        return {
            "mid": str(it.get("item_id") or it.get("client_context") or ""),
            "text": (it.get("text") or "").strip(),
            "sender_pk": sender,
            "username": (user_map or {}).get(sender),
            "created_at": (_to_dt(it.get("timestamp")) or timezone.now()).isoformat(),
        }
    except Exception:
        return None
