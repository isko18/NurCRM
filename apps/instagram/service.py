import uuid
import datetime
from typing import Optional, Dict, List

from django.utils import timezone
from django.utils.timezone import is_naive
from instagrapi import Client
from instagrapi.exceptions import LoginRequired
from django.db import transaction

from .models import CompanyIGAccount, IGThread, IGMessage


def _to_local_aware(dt: datetime.datetime) -> datetime.datetime:
    """
    Приводит aware/naive datetime к aware в текущей TZ Django.
    Naive трактуем как UTC (обычно так приходят внешние источники).
    """
    tz = timezone.get_current_timezone()
    if dt.tzinfo is None or is_naive(dt):
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(tz)


def _epoch_to_dt(n: int) -> datetime.datetime:
    """
    Конвертируем epoch (s/ms/us) в aware (локальная TZ).
    Считаем эпоху как UTC, затем переводим в текущую TZ.
    """
    if n > 10**14:       # microseconds
        seconds = n / 1_000_000
    elif n > 10**11:     # milliseconds
        seconds = n / 1_000
    else:                # seconds
        seconds = n
    dt_utc = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    return _to_local_aware(dt_utc)


def _to_dt(ts) -> Optional[datetime.datetime]:
    """
    Универсальный парсер: seconds/ms/us epoch или datetime → aware (локальная TZ).
    Возвращает None, если распарсить невозможно.
    """
    if not ts:
        return None
    if isinstance(ts, datetime.datetime):
        return _to_local_aware(ts)

    s = str(ts).strip()
    if not s.isdigit():
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return _epoch_to_dt(n)


class IGChatService:
    """
    Сервис работы с Direct:
    - авторизация/сессия
    - live-инбокс/сообщения
    - отправка сообщений
    - синк в БД
    Политика: ВНУТРИ сервиса и БД всегда datetime (aware, локальная TZ).
    """
    def __init__(self, account: CompanyIGAccount):
        self.account = account
        self.cl = Client()

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
            self.cl.account_info()
            self._persist_ok()
            return True
        except LoginRequired:
            return False
        except Exception:
            return False

    def login_manual(self, password: str, verification_code: Optional[str] = None) -> bool:
        if not password:
            raise ValueError("Password is required")
        self.cl.login(self.account.username, password, verification_code=verification_code)
        self.cl.account_info()
        self._persist_ok()
        return True

    # ---------- thread users / usernames ----------
    def fetch_thread_users(self, thread_id: str) -> List[dict]:
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
        return {u["pk"]: u["username"] for u in self.fetch_thread_users(thread_id)}

    # ---------- LIVE helpers ----------
    def fetch_threads_live(self, amount: int = 20) -> List[dict]:
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
        for th in threads_accum[:amount]:
            if not isinstance(th, dict):
                continue
            thread_id = th.get("thread_id") or th.get("thread_v2_id") or th.get("id")
            if not thread_id:
                continue
            last_dt = _to_dt(th.get("last_activity_at") or th.get("last_activity_at_ms")) or timezone.localtime()
            users_raw = th.get("users") or []
            users = []
            for u in users_raw:
                if not isinstance(u, dict):
                    continue
                pk = u.get("pk")
                if not pk:
                    continue
                users.append({"pk": str(pk), "username": (u.get("username") or "").strip()})
            out.append({
                "thread_id": str(thread_id),
                "title": th.get("thread_title") or "",
                "users": users,
                "last_activity": last_dt,  # ← datetime
            })
        return out

    def fetch_messages_live(
        self,
        thread_id: str,
        limit: int = 30,
        *,
        user_map: Optional[Dict[str, str]] = None,
        include_usernames: bool = False,
        since: Optional[datetime.datetime] = None,
    ) -> List[dict]:
        try:
            raw_thread = self.cl.private_request(
                f"direct_v2/threads/{thread_id}/",
                params={"limit": limit}
            )
            items = (raw_thread.get("thread") or {}).get("items", []) or []
        except Exception:
            items = []

        if include_usernames and user_map is None:
            user_map = self.thread_user_map(thread_id)

        out: List[tuple[datetime.datetime, dict]] = []
        for it in items:
            if not isinstance(it, dict):
                continue

            created_at_dt = _to_dt(it.get("timestamp") or it.get("timestamp_ms")) or timezone.localtime()
            if since and created_at_dt <= since:
                continue

            sender_pk = str(it.get("user_id") or it.get("sender_id") or "")
            item_type = it.get("item_type")

            if item_type == "text":
                msg = {
                    "mid": str(it.get("item_id") or it.get("client_context") or uuid.uuid4()),
                    "text": (it.get("text") or "").strip(),
                    "sender_pk": sender_pk,
                    "created_at": created_at_dt,  # ← datetime
                    "direction": "in",
                    "attachments": [],
                }
            else:
                # Универсальная обвязка для нетекстовых сообщений
                msg = {
                    "mid": str(it.get("item_id") or it.get("client_context") or uuid.uuid4()),
                    "text": "",
                    "sender_pk": sender_pk,
                    "created_at": created_at_dt,
                    "direction": "in",
                    "attachments": [{"type": item_type}],
                }

            if include_usernames and user_map:
                msg["username"] = user_map.get(sender_pk, "")

            out.append((created_at_dt, msg))

        out.sort(key=lambda pair: pair[0])
        return [msg for _, msg in out]

    def send_text(self, thread_id: str, text: str) -> dict:
        self.cl.direct_send(text, thread_ids=[thread_id])
        return {
            "mid": f"local-{uuid.uuid4()}",
            "text": text,
            "sender_pk": "me",
            "username": self.account.username,
            "created_at": timezone.localtime(),  # ← datetime
            "direction": "out",
            "attachments": [],
        }

    # ---------- DB helpers ----------
    def fetch_last_text(self, thread_id: str, user_map: dict[str, str] | None = None) -> dict | None:
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
                "created_at": _to_dt(it.get("timestamp")) or timezone.localtime(),  # ← datetime
                "direction": "in",
                "attachments": [],
            }
        except Exception:
            return None

    def sync_thread(self, thread_data: dict) -> IGThread:
        with transaction.atomic():
            th, _ = IGThread.objects.update_or_create(
                ig_account=self.account,
                thread_id=thread_data["thread_id"],
                defaults={
                    "title": thread_data.get("title") or "",
                    "users": thread_data.get("users") or [],
                    "last_activity": _to_dt(thread_data.get("last_activity")) or timezone.localtime(),
                }
            )
        return th

    def sync_message(self, thread: IGThread, msg: dict) -> IGMessage:
        created_at = _to_dt(msg.get("created_at")) or timezone.localtime()
        with transaction.atomic():
            m, _ = IGMessage.objects.update_or_create(
                mid=msg["mid"],
                defaults={
                    "thread": thread,
                    "sender_pk": msg["sender_pk"],
                    "text": msg.get("text") or "",
                    "attachments": msg.get("attachments") or [],
                    "created_at": created_at,
                    "direction": msg.get("direction", "in"),
                }
            )
        return m

    def fetch_messages_db(self, thread: IGThread, limit: int = 50) -> list[dict]:
        qs = (
            IGMessage.objects
            .filter(thread=thread)
            .order_by("-created_at")[:limit]
        )
        msgs = list(qs)
        out: List[dict] = []
        for m in reversed(msgs):
            out.append({
                "mid": m.mid,
                "text": m.text,
                "sender_pk": m.sender_pk,
                "username": None,
                "created_at": m.created_at,  # ← datetime
                "direction": m.direction,
                "attachments": m.attachments or [],
            })
        return out

    def initial_sync(self, threads_limit: int = 50, msgs_per_thread: int = 20) -> None:
        """
        Первый прогон: грузим все треды и по каждому сохраняем N последних сообщений.
        """
        try:
            threads = self.fetch_threads_live(amount=threads_limit)
        except Exception:
            threads = []

        for t in threads:
            th = self.sync_thread(t)
            try:
                msgs = self.fetch_messages_live(t["thread_id"], limit=msgs_per_thread)
            except Exception:
                msgs = []
            for m in msgs:
                self.sync_message(th, m)
