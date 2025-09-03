# apps/instagram/service.py
from __future__ import annotations

import json
import uuid
import logging
from datetime import datetime, timezone as dt_tz
from typing import Any, Dict, List, Optional

from django.db import transaction, IntegrityError
from django.utils import timezone
from django.utils.timezone import is_naive, make_aware

try:
    import orjson
except Exception:
    orjson = None

try:
    from instagrapi import Client
except Exception:  # pragma: no cover
    Client = None

from .models import CompanyIGAccount, IGThread, IGMessage

logger = logging.getLogger(__name__)


# ----------------- utils -----------------
def _loads(s: Any) -> Optional[dict]:
    if not s:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return orjson.loads(s) if orjson else json.loads(s)
    except Exception:
        return None


def _from_ig_ts_to_dt(ts: Any) -> Optional[datetime]:
    """
    IG timestamp -> aware datetime (UTC). Поддерживаем сек/мс/мкс/ISO.
    """
    if not ts:
        return None
    if isinstance(ts, datetime):
        return make_aware(ts) if is_naive(ts) else ts

    s = str(ts)
    if not s.isdigit():
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    n = int(s)
    try:
        if n > 10**14:   # microseconds
            return datetime.fromtimestamp(n / 1_000_000, tz=dt_tz.utc)
        if n > 10**11:   # milliseconds
            return datetime.fromtimestamp(n / 1_000, tz=dt_tz.utc)
        return datetime.fromtimestamp(n, tz=dt_tz.utc)  # seconds
    except Exception:
        return None


def _dt_to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if not dt:
        return None
    if is_naive(dt):
        dt = make_aware(dt)
    return int(dt.timestamp() * 1000)


def _user_public(u: dict) -> dict:
    pk = u.get("pk") or u.get("id") or u.get("user_id") or ""
    return {"pk": str(pk), "username": (u.get("username") or "").strip()}


# ----------------- service -----------------
class IGChatService:
    """
    Лёгкая, быстрая обёртка над instagrapi.Client:
    - логинимся через session/settings (без pydantic),
    - читаем инбокс и треды через private_request,
    - нормализуем сообщения: text / voice / photo / video / link(file),
    - epoch-ms для фронта и Redis, aware datetime для БД.
    """

    def __init__(self, account: CompanyIGAccount):
        if Client is None:
            raise RuntimeError("instagrapi is not installed")

        self.account = account
        self.client = Client()

        # Сдержанные таймауты/паузу — чтобы резвее чувствовался фронт
        try:
            self.client.delay_range = [0.12, 0.3]
            self.client.request_timeout = 12
            self.client.request_read_timeout = 12
            # self.client.logger.setLevel(logging.WARNING)  # при желании притушить шум
        except Exception:
            pass

        # Поднять сессии если хранились
        if isinstance(account.settings_json, dict) and account.settings_json:
            try:
                self.client.set_settings(account.settings_json)
            except Exception:
                pass

        # user_id кэш — для отправки исходящих
        self._self_pk: Optional[str] = None

    # ---------- session ----------
    def try_resume_session(self) -> bool:
        # 1) sessionid
        for name in ("sessionid", "session_id", "ig_sessionid"):
            sid = getattr(self.account, name, None)
            if sid:
                try:
                    self.client.login_by_sessionid(str(sid))
                    self.account.mark_logged_in(settings=self.client.get_settings())
                    self._cache_self_pk()
                    return True
                except Exception as e:
                    logger.warning("login_by_sessionid failed (%s): %s", name, e)

        # 2) settings_json / cookies_json
        for name in ("settings_json", "settings", "ig_session", "cookies_json"):
            raw = getattr(self.account, name, None)
            data = _loads(raw)
            if data:
                try:
                    self.client.set_settings(data)
                    self.client.account_info()  # лёгкий пинг
                    self.account.mark_logged_in(settings=self.client.get_settings())
                    self._cache_self_pk()
                    return True
                except Exception as e:
                    logger.warning("set_settings failed (%s): %s", name, e)

        # 3) username/password (крайний случай)
        user = getattr(self.account, "username", None)
        pwd = getattr(self.account, "password", None)
        if user and pwd:
            try:
                self.client.login(user, pwd)
                self.account.mark_logged_in(settings=self.client.get_settings())
                self._cache_self_pk()
                return True
            except Exception as e:
                logger.error("password login failed: %s", e)
                return False

        return False

    def _cache_self_pk(self):
        try:
            uid = getattr(self.client, "user_id", None)
            if uid:
                self._self_pk = str(uid)
        except Exception:
            self._self_pk = None

    # ---------- threads (RAW API) ----------
    def fetch_threads_live(self, amount: int = 20) -> List[dict]:
        """
        Быстрый инбокс: /direct_v2/inbox/ с пагинацией.
        Возвращаем: thread_id, title, users[], last_activity (datetime).
        """
        out, cursor = [], None
        collected = 0

        while collected < amount:
            limit = min(20, amount - collected)
            params = {
                "limit": limit,
                "thread_message_limit": 5,      # меньше сообщений внутри треда → быстрее ответ
                "persistentBadging": True,
                "is_prefetching": False,
                "visual_message_return_type": "unseen",
            }
            if cursor:
                params.update({
                    "cursor": cursor,
                    "direction": "older",
                    "fetch_reason": "page_scroll",
                })

            try:
                inbox = self.client.private_request("direct_v2/inbox/", params=params) or {}
            except Exception as e:
                logger.warning("inbox request failed: %s", e)
                break

            data = inbox.get("inbox") or {}
            threads = data.get("threads") or []
            if not threads:
                break

            for th in threads:
                tid = th.get("thread_id") or th.get("thread_v2_id") or th.get("id")
                if not tid:
                    continue
                users = th.get("users") or []
                last_dt = (
                    _from_ig_ts_to_dt(th.get("last_activity_at") or th.get("last_activity_at_ms"))
                    or timezone.now()
                )
                out.append({
                    "thread_id": str(tid),
                    "title": th.get("thread_title") or "",
                    "users": [_user_public(u) for u in users if isinstance(u, dict)],
                    "last_activity": last_dt,
                })
                collected += 1
                if collected >= amount:
                    break

            cursor = data.get("oldest_cursor") or data.get("cursor")
            if not cursor:
                break

        return out

    def fetch_thread_users(self, thread_id: str) -> List[dict]:
        try:
            raw = self.client.private_request(f"direct_v2/threads/{thread_id}/", params={"limit": 1})
            users = (raw.get("thread") or {}).get("users", []) or []
        except Exception:
            users = []
        return [_user_public(u) for u in users if isinstance(u, dict)]

    def fetch_last_text(self, thread_id: str, user_map: Optional[Dict[str, str]] = None) -> Optional[dict]:
        try:
            raw = self.client.private_request(f"direct_v2/threads/{thread_id}/", params={"limit": 1})
            items = (raw.get("thread") or {}).get("items", []) or []
        except Exception:
            items = []
        if not items:
            return None
        return self._item_to_public_fast(items[0], user_map=user_map, include_usernames=bool(user_map))

    # ---------- messages (RAW API) ----------
    def fetch_messages_live(
        self,
        thread_id: str,
        limit: int,
        *,
        user_map: Optional[Dict[str, str]] = None,
        include_usernames: bool = False,
        since: Optional[datetime] = None,
    ) -> List[dict]:
        """
        /direct_v2/threads/{id}/ — items приходят новыми вперёд.
        Возвращаем ХРОНОЛОГИЧЕСКИ (old->new) и только новее 'since'.
        """
        try:
            raw = self.client.private_request(
                f"direct_v2/threads/{thread_id}/",
                params={"limit": max(20, limit), "visual_message_return_type": "unseen"}
            ) or {}
            items = (raw.get("thread") or {}).get("items", []) or []
        except Exception:
            items = []

        res: List[dict] = []
        for it in reversed(items):  # старые → новые
            msg = self._item_to_public_fast(it, user_map=user_map, include_usernames=include_usernames)
            if not msg:
                continue
            ts_ms = msg.get("created_at")
            if since and ts_ms:
                created_dt = _from_ig_ts_to_dt(ts_ms)
                if created_dt and created_dt <= since:
                    continue
            res.append(msg)

        if len(res) > limit:
            res = res[-limit:]
        return res

    # ---------- normalization (FAST) ----------
    def _item_to_public_fast(
        self,
        it: dict,
        *,
        user_map: Optional[Dict[str, str]] = None,
        include_usernames: bool = False,
    ) -> Optional[dict]:
        """
        Лёгкая нормализация без тяжёлых полей:
          - created_at: epoch-ms (int)
          - attachments: [{'type','url',...}] (минимум)
          - покрываем text / voice / photo / video / link(file)
        """
        if not isinstance(it, dict):
            return None

        item_type = (it.get("item_type") or "").lower()
        mid = str(it.get("item_id") or it.get("client_context") or uuid.uuid4())
        sender_pk = str(it.get("user_id") or it.get("sender_id") or "")
        created_dt = _from_ig_ts_to_dt(it.get("timestamp")) or timezone.now()
        created_ms = _dt_to_epoch_ms(created_dt)

        # text — самый частый кейс
        if item_type == "text":
            msg = {
                "mid": mid,
                "text": (it.get("text") or "").strip(),
                "sender_pk": sender_pk,
                "created_at": created_ms,
                "direction": "in",
                "attachments": [],
            }
            if include_usernames and user_map:
                msg["username"] = user_map.get(sender_pk, "")
            return msg

        attachments: List[dict] = []
        text = (it.get("text") or "").strip()

        # voice
        if item_type in ("voice_media", "audio", "voice"):
            vm = it.get("voice_media") or {}
            m = vm.get("media") or vm
            url = m.get("audio_src") or m.get("src") or m.get("url")
            dur = m.get("duration") or m.get("audio_duration") or it.get("audio_duration")
            if url:
                try:
                    dur = float(dur) if dur is not None else None
                except Exception:
                    dur = None
                attachments.append({"type": "audio", "url": url, "duration": dur})

        # photo / video (media/clip/raven_media)
        elif item_type in ("media", "clip", "raven_media"):
            m = it.get("media") or {}
            media_type = m.get("media_type")
            if media_type == 2:
                vv = (m.get("video_versions") or [])
                if vv:
                    # берём первую (как правило, самая лёгкая/достаточная)
                    attachments.append({"type": "video", "url": vv[0].get("url")})
            else:
                cands = (m.get("image_versions2") or {}).get("candidates") or []
                if cands:
                    attachments.append({"type": "image", "url": cands[0].get("url")})
            if not text:
                cap = (m.get("caption") or {}).get("text") or ""
                if cap:
                    text = cap.strip()

        # link/share/file → сведём к "file" с URL
        elif item_type in ("link", "share", "file", "animated_media", "felix_share", "story_share"):
            link = it.get("link") or it.get("share") or {}
            url = (
                (link.get("link_url") or link.get("url"))
                or (it.get("animated_media") or {}).get("images", {}).get("fixed_height", {}).get("url")
            )
            if not url:
                # иногда ссылка лежит внутри nested объектов
                url = (it.get("reel_share") or {}).get("link_url") or (it.get("clip") or {}).get("clip") or None
            if url:
                attachments.append({"type": "file", "url": url})

        # другое игнорируем (реакции, лайки, стикеры)
        msg = {
            "mid": mid,
            "text": text,
            "sender_pk": sender_pk,
            "created_at": created_ms,
            "direction": "in",
            "attachments": attachments,
        }
        if include_usernames and user_map:
            msg["username"] = user_map.get(sender_pk, "")
        return msg

    # ---------- send ----------
    def send_text(self, thread_id: str, text: str) -> dict:
        self.client.direct_send(text, thread_ids=[thread_id])
        now = timezone.now()
        return {
            "mid": f"local-{uuid.uuid4()}",
            "text": text,
            "sender_pk": self._self_pk or "me",
            "username": self.account.username,
            "created_at": _dt_to_epoch_ms(now),
            "direction": "out",
            "attachments": [],
        }

    # ---------- DB sync ----------
    @transaction.atomic
    def sync_thread(self, t: dict) -> IGThread:
        tid = str(t.get("thread_id") or "")
        if not tid:
            raise ValueError("thread_id required")

        last = t.get("last_activity")
        last_dt = last if isinstance(last, datetime) else _from_ig_ts_to_dt(last) or timezone.now()

        obj, created = IGThread.objects.select_for_update().get_or_create(
            ig_account=self.account,
            thread_id=tid,
            defaults={
                "title": t.get("title") or "",
                "users": t.get("users") or [],
                "last_activity": last_dt,
            },
        )
        changed = False
        title = t.get("title")
        users = t.get("users")
        if title and obj.title != title:
            obj.title = title; changed = True
        if users and obj.users != users:
            obj.users = users; changed = True
        if last_dt and (not obj.last_activity or last_dt > obj.last_activity):
            obj.last_activity = last_dt; changed = True
        if changed:
            obj.save(update_fields=["title", "users", "last_activity"])
        return obj

    @transaction.atomic
    def sync_message(self, thread: IGThread, m: dict) -> IGMessage:
        mid = str(m.get("mid") or "")
        if not mid:
            raise ValueError("mid required")
        created_at = _from_ig_ts_to_dt(m.get("created_at")) or timezone.now()

        defaults = {
            "text": m.get("text") or "",
            "sender_pk": str(m.get("sender_pk") or ""),
            "created_at": created_at,
            "direction": m.get("direction") or "in",
            "attachments": m.get("attachments") or [],
        }
        try:
            obj, created = IGMessage.objects.get_or_create(thread=thread, mid=mid, defaults=defaults)
            if not created:
                need = False
                if obj.text != defaults["text"]:
                    obj.text = defaults["text"]; need = True
                if obj.sender_pk != defaults["sender_pk"]:
                    obj.sender_pk = defaults["sender_pk"]; need = True
                if obj.direction != defaults["direction"]:
                    obj.direction = defaults["direction"]; need = True
                if (obj.attachments or []) != (defaults["attachments"] or []):
                    obj.attachments = defaults["attachments"]; need = True
                if obj.created_at != created_at:
                    obj.created_at = created_at; need = True
                if need:
                    obj.save(update_fields=["text", "sender_pk", "direction", "attachments", "created_at"])
        except IntegrityError:
            obj = IGMessage.objects.get(thread=thread, mid=mid)
        return obj

    def fetch_messages_db(self, thread: IGThread, limit: int) -> List[dict]:
        rows = list(
            IGMessage.objects.filter(thread=thread)
            .order_by("-created_at")
            .values("mid", "text", "sender_pk", "created_at", "direction", "attachments")[:limit]
        )
        rows.reverse()
        out = []
        for r in rows:
            out.append({
                "mid": r["mid"],
                "text": r["text"],
                "sender_pk": r["sender_pk"],
                "created_at": _dt_to_epoch_ms(r["created_at"] or timezone.now()),
                "direction": r["direction"],
                "attachments": r.get("attachments") or [],
            })
        return out

    # ---------- bootstrap ----------
    def initial_sync(self, threads_limit: int = 30, msgs_per_thread: int = 10) -> None:
        try:
            threads = self.fetch_threads_live(amount=threads_limit)
        except Exception:
            threads = []
        for t in threads:
            th = self.sync_thread(t)
            try:
                msgs = self.fetch_messages_live(
                    t["thread_id"], msgs_per_thread, user_map=None, include_usernames=False, since=None
                )
            except Exception:
                msgs = []
            for m in msgs:
                self.sync_message(th, m)
